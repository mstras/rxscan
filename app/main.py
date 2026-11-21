import hashlib
import logging
import secrets
from pathlib import Path

from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import os

from . import auth, config, receipts, sheets
from .config import PROVIDERS, settings
from .fields import ALL_HEADERS, ALL_KEYS, FIELDS
from .parser import ParseError, active_model, list_models, parse_receipt, test_connection

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rxscan")

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 25 * 1024 * 1024

app = FastAPI(title="RxScan", docs_url=None, redoc_url=None, openapi_url=None)
api = []  # auth is enforced by the middleware below

if not auth.ENABLED:
    log.warning("APP_PASSWORD is not set: RxScan is open to anyone who can reach it.")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if auth.is_public(path) or auth.is_authenticated(request):
        resp = await call_next(request)
    elif path.startswith("/api/") or request.method != "GET":
        resp = JSONResponse({"detail": "Signed out. Please sign in again.", "login": True}, status_code=401)
    else:
        nxt = path + (("?" + request.url.query) if request.url.query else "")
        resp = RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


# ---- login ----------------------------------------------------------------
def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


@app.get("/login")
def login_page(request: Request):
    if not auth.ENABLED or auth.is_authenticated(request):
        return RedirectResponse(auth.safe_next(request.query_params.get("next")), status_code=303)
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    nxt = auth.safe_next(next)
    if not auth.ENABLED:
        return RedirectResponse(nxt, status_code=303)
    ip = auth.client_ip(request)
    if auth.too_many_failures(ip):
        log.warning("login locked out for %s", ip)
        return RedirectResponse(f"/login?e=429&next={quote(nxt, safe='')}", status_code=303)
    if not auth.check_credentials(username.strip(), password):
        auth.record_failure(ip)
        log.warning("failed login for %r from %s", username, ip)
        return RedirectResponse(f"/login?e=1&u={quote(username.strip())}&next={quote(nxt, safe='')}", status_code=303)
    auth.clear_failures(ip)
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_session(), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=_is_https(request), path="/")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login?e=out", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


@app.exception_handler(sheets.SheetsError)
async def sheets_error(_req, exc: sheets.SheetsError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status if exc.status < 500 else 503)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/config", dependencies=api)
def get_config():
    provider, model = active_model()
    return {
        "fields": [{"key": k, "header": h} for k, h, *_ in FIELDS],
        "all_headers": ALL_HEADERS,
        "all_keys": ALL_KEYS,
        "sheets": sheets.capabilities(),
        "receipts": {"storage": receipts.mode(),
                     "folder": settings.get("receipts_folder_path") or f"My Drive › {settings.get('receipts_folder')}",
                     "local_dir": settings.get("receipts_local_dir")},
        "default_sheet_url": settings.get("default_sheet_url"),
        "default_sheet_tab": settings.get("default_sheet_tab"),
        "vision": {"provider": provider, "model": model},
        "version": STATIC_VERSION,
    }


# ---- settings -------------------------------------------------------------
@app.get("/api/settings", dependencies=api)
def get_settings():
    return {"settings": settings.public(), "providers": PROVIDERS, "mock": config.MOCK_PARSER,
            "password_protected": auth.ENABLED, "username": auth.USERNAME if auth.ENABLED else None}


class SettingsBody(BaseModel):
    values: dict = {}
    clear: list[str] = []


@app.put("/api/settings", dependencies=api)
def put_settings(body: SettingsBody):
    try:
        settings.update(body.values, body.clear)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_settings()


class ProbeBody(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""   # optional: test a key before saving it
    url: str = ""


@app.post("/api/settings/models", dependencies=api)
async def models(body: ProbeBody):
    try:
        return {"models": await list_models(body.provider, body.api_key or None, body.url or None)}
    except ParseError as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/test", dependencies=api)
async def test(body: ProbeBody):
    try:
        return {"message": await test_connection(body.provider, body.model, body.api_key or None, body.url or None)}
    except ParseError as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/apps-script", dependencies=api)
def apps_script(regenerate: bool = False):
    """Return the bridge script with this server's secret baked in (creating the secret if needed)."""
    secret = settings.get("apps_script_secret")
    if regenerate or not secret:
        secret = secrets.token_urlsafe(32)
        try:
            settings.update({"apps_script_secret": secret})
        except ValueError as e:
            raise HTTPException(400, str(e))
    return {"script": sheets.apps_script_source(secret)}


class BridgeTest(BaseModel):
    url: str = ""


@app.post("/api/settings/sheets-test", dependencies=api)
def sheets_test(body: BridgeTest):
    url = body.url.strip()
    if url:
        be = sheets.AppsScriptBackend(url=url)
    else:
        be = sheets.backend()
    res = be.ping()
    who = res.get("user")
    msg = f"Connected{' as ' + who if who else ''}."
    outdated = isinstance(be, sheets.AppsScriptBackend) and int(res.get("version", 1)) < receipts.MIN_BRIDGE_VERSION
    if outdated:
        msg += " Your bridge script is an older version, so receipts can't be saved to Drive yet: re-copy the setup script and deploy a new version."
    return {"message": msg, "outdated": outdated}


@app.get("/api/settings/bridge", dependencies=api)
def bridge_status():
    return {"bridge": receipts.bridge_status()}


# ---- parse / sheets -------------------------------------------------------
@app.post("/api/parse", dependencies=api)
async def parse(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Image too large (25 MB max)")
    try:
        result = await parse_receipt(data)
    except ParseError as e:
        log.warning("parse failed for %s: %s", file.filename, e)
        raise HTTPException(422, str(e))
    for row in result["rows"]:
        row["source_file"] = file.filename or ""
    return result


# ---- receipt copies ---------------------------------------------------------
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")


@app.post("/api/receipts", dependencies=api)
async def save_receipt(request: Request, file: UploadFile = File(...), date: str = Form(""),
                       pharmacy: str = Form(""), medication: str = Form("")):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Image too large (25 MB max)")
    base = PUBLIC_URL or str(request.base_url)
    try:
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(receipts.save, data,
                                       {"date": date, "pharmacy": pharmacy, "medication": medication}, base)
    except receipts.ReceiptError as e:
        raise HTTPException(502, str(e))


# Drive folder picker (Apps Script bridge)
@app.get("/api/drive/folders", dependencies=api)
def drive_folders(parent: str = "root"):
    try:
        return receipts.drive_call("folders", parent=parent)
    except receipts.ReceiptError as e:
        raise HTTPException(400, str(e))


class NewFolder(BaseModel):
    parent: str = "root"
    name: str


@app.post("/api/drive/folders", dependencies=api)
def drive_new_folder(body: NewFolder):
    if body.parent == "shared":
        raise HTTPException(400, "Open a folder first. You can't create folders directly in 'Shared with me'.")
    try:
        return receipts.drive_call("create_folder", parent=body.parent, name=body.name)
    except receipts.ReceiptError as e:
        raise HTTPException(400, str(e))


class FolderRef(BaseModel):
    folder: str


@app.post("/api/drive/folder-info", dependencies=api)
def drive_folder_info(body: FolderRef):
    try:
        return receipts.drive_call("folder_info", id=receipts.folder_id_from(body.folder))
    except receipts.ReceiptError as e:
        raise HTTPException(400, str(e))


@app.get("/receipts/{rel:path}")
def get_receipt(rel: str):
    p = receipts.local_path(rel)
    if not p:
        raise HTTPException(404, "Receipt not found")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


class SheetRef(BaseModel):
    sheet: str


@app.post("/api/sheet", dependencies=api)
def sheet_info(ref: SheetRef):
    return sheets.backend().info(sheets.spreadsheet_id_from(ref.sheet))


@app.get("/api/sheets", dependencies=api)
def list_sheets():
    return {"files": sheets.backend().list_sheets()}


class NewSheet(BaseModel):
    title: str = "Prescriptions"
    tab: str = "Prescriptions"


@app.post("/api/sheets/new", dependencies=api)
def new_sheet(body: NewSheet):
    be = sheets.backend()
    info = be.create(body.title.strip() or "Prescriptions", body.tab.strip() or "Prescriptions")
    be.set_header(info["id"], info["tabs"][0], list(ALL_HEADERS))
    info["url"] = sheets.sheet_url(info["id"])
    return info


class AppendBody(BaseModel):
    sheet: str
    tab: str
    rows: list[dict]


@app.post("/api/append", dependencies=api)
def append(body: AppendBody):
    sid = sheets.spreadsheet_id_from(body.sheet)
    res = sheets.append_rows(sid, body.tab, body.rows)
    res["url"] = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    return res


# ---- PWA ------------------------------------------------------------------
def _static_version() -> str:
    h = hashlib.sha256()
    for f in sorted(STATIC.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(STATIC).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


STATIC_VERSION = _static_version()
NO_CACHE = {"Cache-Control": "no-cache"}


@app.get("/sw.js")
def service_worker():
    js = (STATIC / "sw.js").read_text().replace("__VERSION__", STATIC_VERSION)
    return Response(js, media_type="text/javascript",
                    headers={**NO_CACHE, "Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json", headers=NO_CACHE)


@app.get("/favicon.ico")
def favicon():
    return FileResponse(STATIC / "icons" / "favicon-32.png", media_type="image/png")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_icon():
    return FileResponse(STATIC / "icons" / "apple-touch-icon.png", media_type="image/png")


@app.get("/", dependencies=api)
def index():
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
