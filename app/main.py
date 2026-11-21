import hashlib
import logging
import os
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import auth, config, db, google, receipts, sheets
from .config import PROVIDERS, settings
from .fields import ALL_HEADERS, ALL_KEYS, FIELDS
from .parser import ParseError, active_model, list_models, parse_receipt, test_connection

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("rxscan")

VERSION = "1.1.1"
STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = 25 * 1024 * 1024
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

db.init()
app = FastAPI(title="RxScan", docs_url=None, redoc_url=None, openapi_url=None)


# ---- auth middleware --------------------------------------------------------------
@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    user = auth.current_user(request)
    request.state.user = user
    if user or auth.is_public(path):
        resp = await call_next(request)
    elif path.startswith(("/api/", "/receipts/")) or request.method != "GET":
        resp = JSONResponse({"detail": "Signed out. Please sign in again.", "login": True}, status_code=401)
    else:
        nxt = path + (("?" + request.url.query) if request.url.query else "")
        resp = RedirectResponse(f"/login?next={quote(nxt, safe='')}", status_code=303)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


def user_of(request: Request) -> dict:
    return request.state.user


def admin_only(request: Request) -> dict:
    u = request.state.user
    if not u or u["role"] != "admin":
        raise HTTPException(403, "Only admins can do that.")
    return u


def google_user(request: Request) -> dict:
    u = request.state.user
    if u["kind"] != "google":
        raise HTTPException(400, "Sign in with Google to use your own sheet and folder.")
    return u


@app.exception_handler(sheets.SheetsError)
async def sheets_error(_req, exc: sheets.SheetsError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status if exc.status < 500 else 503)


@app.exception_handler(google.GoogleError)
async def google_error(_req, exc: google.GoogleError):
    return JSONResponse({"detail": str(exc), "reconnect": exc.reconnect}, status_code=exc.status)


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def base_url(request: Request) -> str:
    if PUBLIC_URL:
        return PUBLIC_URL
    scheme = "https" if _is_https(request) else request.url.scheme
    return f"{scheme}://{request.headers.get('host', request.url.netloc)}"


def redirect_uri(request: Request) -> str:
    return base_url(request) + "/auth/google/callback"


def _set_session(resp, request, user=None):
    resp.set_cookie(auth.COOKIE, auth.make_session(user), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=_is_https(request), path="/")


@app.get("/healthz")
def healthz():
    return {"ok": True, "version": VERSION}


# ---- sign in ------------------------------------------------------------------------
@app.get("/login")
def login_page(request: Request):
    if request.state.user:
        return RedirectResponse(auth.safe_next(request.query_params.get("next")), status_code=303)
    html = (STATIC / "login.html").read_text().replace("__GOOGLE_READY__", "true" if google.configured() else "false")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    nxt = auth.safe_next(next)
    ip = auth.client_ip(request)
    if auth.too_many_failures(ip):
        log.warning("login locked out for %s", ip)
        return RedirectResponse(f"/login?e=429&pw=1&next={quote(nxt, safe='')}", status_code=303)
    if not auth.check_credentials(username.strip(), password):
        auth.record_failure(ip)
        log.warning("failed admin password sign-in for %r from %s", username, ip)
        return RedirectResponse(f"/login?e=1&pw=1&u={quote(username.strip())}&next={quote(nxt, safe='')}", status_code=303)
    auth.clear_failures(ip)
    resp = RedirectResponse(nxt, status_code=303)
    _set_session(resp, request)
    return resp


@app.get("/auth/google/start")
def google_start(request: Request, next: str = "/", consent: int = 0, hint: str = ""):
    if not google.configured():
        return RedirectResponse("/login?e=nogoogle", status_code=303)
    state, nonce = auth.make_state(next)
    resp = RedirectResponse(google.auth_url(redirect_uri(request), state, hint or None, bool(consent)), status_code=303)
    resp.set_cookie(auth.STATE_COOKIE, nonce, max_age=600, httponly=True, samesite="lax",
                    secure=_is_https(request), path="/auth/google/")
    return resp


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        return RedirectResponse("/login?e=cancelled", status_code=303)
    nxt = auth.check_state(state, request.cookies.get(auth.STATE_COOKIE))
    if nxt is None:
        return RedirectResponse("/login?e=state", status_code=303)
    try:
        tok = google.exchange_code(code, redirect_uri(request))
        who = google.identity(tok.get("id_token", ""))
    except google.GoogleError as e:
        log.warning("google sign-in failed: %s", e)
        return RedirectResponse(f"/login?e=google&m={quote(str(e))}", status_code=303)
    email = who["email"].lower()
    user = db.get_user_by_email(email)
    if not user:
        log.warning("google sign-in refused for %s (not on the People list)", email)
        return RedirectResponse(f"/login?e=notallowed&email={quote(email)}", status_code=303)
    saved = db.save_tokens(user["id"], tok.get("refresh_token"), tok["access_token"], tok.get("expires_in", 3600),
                           tok.get("scope", ""))
    if not saved or "drive.file" not in tok.get("scope", "drive.file"):
        # No refresh token on file (or Drive access unticked): ask Google for consent explicitly.
        return RedirectResponse(f"/auth/google/start?consent=1&hint={quote(email)}&next={quote(nxt, safe='')}", status_code=303)
    db.update_user(user["id"], name=who.get("name") or user["name"], picture=who.get("picture", ""), status="active")
    db.touch(user["id"])
    resp = RedirectResponse(nxt, status_code=303)
    _set_session(resp, request, db.get_user(user["id"]))
    resp.delete_cookie(auth.STATE_COOKIE, path="/auth/google/")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login?e=out", status_code=303)
    resp.delete_cookie(auth.COOKIE, path="/")
    return resp


# ---- who am I / bootstrap --------------------------------------------------------------------
def _public_user(u: dict) -> dict:
    return {"id": u["id"], "kind": u["kind"], "email": u.get("email", ""), "name": u.get("name") or u.get("email", ""),
            "picture": u.get("picture", ""), "role": u["role"]}


@app.get("/api/me")
def me(request: Request):
    u = user_of(request)
    provider, model = active_model()
    out = {
        "user": _public_user(u),
        "fields": [{"key": k, "header": h} for k, h, *_ in FIELDS],
        "all_keys": ALL_KEYS, "all_headers": ALL_HEADERS,
        "vision": {"provider": provider, "model": model},
        "google": {"configured": google.configured(), "picker": google.picker_ready()},
        "version": VERSION, "static_version": STATIC_VERSION,
    }
    if u["kind"] == "google":
        st = db.get_settings(u["id"])
        out["settings"] = st
        out["connected"] = db.get_tokens(u["id"]) is not None
        out["needs_setup"] = not (st["sheet_id"] and st["folder_id"])
    else:  # built-in admin: the server-wide 1.0 connection
        out["legacy"] = {
            "sheets": sheets.capabilities(),
            "receipts": {"storage": receipts.mode(),
                         "folder": settings.get("receipts_folder_path") or f"My Drive › {settings.get('receipts_folder')}"},
            "default_sheet_url": settings.get("default_sheet_url"),
            "default_sheet_tab": settings.get("default_sheet_tab"),
        }
    return out


# ---- your sheet and folder (Google users) --------------------------------------------------------
def _first_name(u):
    n = (u.get("name") or u["email"].split("@")[0]).strip()
    return n.split()[0] if n else "Me"


@app.post("/api/me/setup")
def me_setup(request: Request):
    """First sign-in: create the person's sheet and receipt folder if they don't have them."""
    u = google_user(request)
    st = db.get_settings(u["id"])
    created = {}
    if not st["sheet_id"]:
        info = google.create_sheet(u["id"], f"RxScan – {_first_name(u)}", "Prescriptions", list(ALL_HEADERS))
        st = db.set_settings(u["id"], sheet_id=info["id"], sheet_title=info["title"], tab="Prescriptions")
        created["sheet"] = info["title"]
    if not st["folder_id"]:
        f = google.create_folder(u["id"], "RxScan Receipts")
        st = db.set_settings(u["id"], folder_id=f["id"], folder_name=f["name"])
        created["folder"] = f["name"]
    return {"settings": st, "created": created}


class SheetPick(BaseModel):
    id: str


@app.post("/api/me/sheet")
def me_pick_sheet(request: Request, body: SheetPick):
    u = google_user(request)
    sid = sheets.spreadsheet_id_from(body.id)
    info = google.sheet_info(u["id"], sid)
    cur = db.get_settings(u["id"])
    tab = cur["tab"] if cur["sheet_id"] == sid and cur["tab"] in info["tabs"] else (
        "Prescriptions" if "Prescriptions" in info["tabs"] else info["tabs"][0])
    st = db.set_settings(u["id"], sheet_id=sid, sheet_title=info["title"], tab=tab)
    return {"settings": st, "tabs": info["tabs"]}


@app.get("/api/me/sheet/tabs")
def me_tabs(request: Request):
    u = google_user(request)
    st = db.get_settings(u["id"])
    if not st["sheet_id"]:
        return {"tabs": []}
    return {"tabs": google.sheet_info(u["id"], st["sheet_id"])["tabs"]}


class NewName(BaseModel):
    name: str = ""


@app.post("/api/me/sheet/new")
def me_new_sheet(request: Request, body: NewName):
    u = google_user(request)
    title = body.name.strip() or f"RxScan – {_first_name(u)}"
    info = google.create_sheet(u["id"], title, "Prescriptions", list(ALL_HEADERS))
    st = db.set_settings(u["id"], sheet_id=info["id"], sheet_title=info["title"], tab="Prescriptions")
    return {"settings": st, "tabs": info["tabs"]}


class FolderPick(BaseModel):
    id: str


@app.post("/api/me/folder")
def me_pick_folder(request: Request, body: FolderPick):
    u = google_user(request)
    try:
        fid = receipts.folder_id_from(body.id)
    except receipts.ReceiptError as e:
        raise HTTPException(400, str(e))
    f = google.file_info(u["id"], fid)
    if f.get("mimeType") != google.FOLDER_MIME:
        raise HTTPException(400, "That isn't a folder. Pick a folder for receipt photos.")
    return {"settings": db.set_settings(u["id"], folder_id=f["id"], folder_name=f["name"])}


@app.post("/api/me/folder/new")
def me_new_folder(request: Request, body: NewName):
    u = google_user(request)
    f = google.create_folder(u["id"], body.name.strip() or "RxScan Receipts")
    return {"settings": db.set_settings(u["id"], folder_id=f["id"], folder_name=f["name"])}


class MyPrefs(BaseModel):
    tab: str | None = None
    year_folders: bool | None = None


@app.put("/api/me/settings")
def me_prefs(request: Request, body: MyPrefs):
    u = google_user(request)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"settings": db.set_settings(u["id"], **changes)}


@app.get("/api/me/picker")
def me_picker(request: Request):
    """Short-lived token + public IDs for the Google Picker in the browser."""
    u = google_user(request)
    if not google.picker_ready():
        raise HTTPException(400, "The Google Picker isn't set up yet. An admin needs to add the API key and project number in Google setup.")
    c = google.config()
    return {"token": google.access_token(u["id"]), "api_key": c["api_key"], "app_id": c["project_number"],
            "client_id": c["client_id"]}


# ---- parse, save, append (everyone) ----------------------------------------------------------------
@app.post("/api/parse")
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


@app.post("/api/receipts")
async def save_receipt(request: Request, file: UploadFile = File(...), date: str = Form(""),
                       pharmacy: str = Form(""), medication: str = Form("")):
    u = user_of(request)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "Image too large (25 MB max)")
    meta = {"date": date, "pharmacy": pharmacy, "medication": medication}
    try:
        if u["kind"] == "google":
            return await run_in_threadpool(receipts.save_for_user, u["id"], data, meta)
        return await run_in_threadpool(receipts.save, data, meta, base_url(request))
    except receipts.ReceiptError as e:
        raise HTTPException(502, str(e))


class AppendBody(BaseModel):
    rows: list[dict]
    sheet: str = ""   # built-in admin only (Google users append to their own sheet)
    tab: str = ""


@app.post("/api/append")
def append(request: Request, body: AppendBody):
    u = user_of(request)
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in body.rows]
    if u["kind"] == "google":
        st = db.get_settings(u["id"])
        if not st["sheet_id"]:
            raise HTTPException(400, "You don't have a sheet yet. Open Settings and create or choose one.")
        res = sheets.append_rows(st["sheet_id"], st["tab"] or "Prescriptions", rows, be=google.SheetsBackend(u["id"]))
        res.update(url=f"https://docs.google.com/spreadsheets/d/{st['sheet_id']}/edit", sheet=st["sheet_title"], tab=st["tab"])
        db.touch(u["id"])
        return res
    if not body.sheet or not body.tab:
        raise HTTPException(400, "Choose a sheet and tab first.")
    sid = sheets.spreadsheet_id_from(body.sheet)
    res = sheets.append_rows(sid, body.tab, rows)
    res["url"] = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    return res


# ---- admin: server settings ----------------------------------------------------------------------
@app.get("/api/settings")
def get_settings(request: Request):
    admin_only(request)
    return {"settings": settings.public(), "providers": PROVIDERS, "mock": config.MOCK_PARSER,
            "username": auth.USERNAME, "default_password": config.APP_PASSWORD == config.DEFAULT_PASSWORD}


class SettingsBody(BaseModel):
    values: dict = {}
    clear: list[str] = []


@app.put("/api/settings")
def put_settings(request: Request, body: SettingsBody):
    admin_only(request)
    try:
        settings.update(body.values, body.clear)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_settings(request)


class ProbeBody(BaseModel):
    provider: str
    model: str = ""
    api_key: str = ""
    url: str = ""


@app.post("/api/settings/models")
async def models(request: Request, body: ProbeBody):
    admin_only(request)
    try:
        return {"models": await list_models(body.provider, body.api_key or None, body.url or None)}
    except ParseError as e:
        raise HTTPException(400, str(e))


@app.post("/api/settings/test")
async def test(request: Request, body: ProbeBody):
    admin_only(request)
    try:
        return {"message": await test_connection(body.provider, body.model, body.api_key or None, body.url or None)}
    except ParseError as e:
        raise HTTPException(400, str(e))


# ---- admin: Google setup ---------------------------------------------------------------------------
@app.get("/api/admin/google")
def admin_google(request: Request):
    admin_only(request)
    pub = settings.public()
    return {"configured": google.configured(), "picker": google.picker_ready(), "redirect_uri": redirect_uri(request),
            "origin": base_url(request), "https": base_url(request).startswith("https://"),
            "values": {k: pub[k] for k in ("google_client_id", "google_client_secret", "google_api_key", "google_project_number")}}


@app.post("/api/admin/google/test")
def admin_google_test(request: Request):
    """Checks the client ID/secret by redeeming a dummy code: Google says invalid_grant if the client is valid."""
    admin_only(request)
    if not google.configured():
        raise HTTPException(400, "Add the client ID and client secret first.")
    try:
        google.exchange_code("rxscan-test", redirect_uri(request))
    except google.GoogleError as e:
        if e.reconnect:  # invalid_grant → the client itself was accepted
            return {"ok": True, "message": "Google accepted the client ID and secret."}
        raise HTTPException(400, str(e))
    return {"ok": True, "message": "Google accepted the client ID and secret."}


# ---- admin: people --------------------------------------------------------------------------------------
@app.get("/api/admin/users")
def admin_users(request: Request):
    admin_only(request)
    return {"users": [{k: u.get(k) for k in ("id", "email", "name", "picture", "role", "status", "last_seen",
                                               "sheet_title", "connected")} for u in db.list_users()]}


class NewUser(BaseModel):
    email: str
    role: str = "member"


@app.post("/api/admin/users")
def admin_add_user(request: Request, body: NewUser):
    admin_only(request)
    email = body.email.strip().lower()
    if "@" not in email or " " in email or len(email) > 254:
        raise HTTPException(400, "Enter a Google account email, like name@gmail.com.")
    if body.role not in ("admin", "member"):
        raise HTTPException(400, "Role must be admin or member.")
    if db.get_user_by_email(email):
        raise HTTPException(400, f"{email} is already on the list.")
    db.add_user(email, body.role)
    return admin_users(request)


class UserPatch(BaseModel):
    role: str


@app.patch("/api/admin/users/{uid}")
def admin_patch_user(request: Request, uid: int, body: UserPatch):
    me_ = admin_only(request)
    u = db.get_user(uid)
    if not u:
        raise HTTPException(404, "No such person.")
    if body.role not in ("admin", "member"):
        raise HTTPException(400, "Role must be admin or member.")
    if u["role"] == "admin" and body.role == "member" and db.count_admins() <= 1:
        raise HTTPException(400, "Keep at least one admin who signs in with Google.")
    if me_["kind"] == "google" and me_["id"] == uid and body.role == "member":
        raise HTTPException(400, "You can't remove your own admin role. Ask another admin.")
    db.update_user(uid, role=body.role)
    return admin_users(request)


@app.delete("/api/admin/users/{uid}")
def admin_remove_user(request: Request, uid: int):
    me_ = admin_only(request)
    u = db.get_user(uid)
    if not u:
        raise HTTPException(404, "No such person.")
    if me_["kind"] == "google" and me_["id"] == uid:
        raise HTTPException(400, "You can't remove yourself.")
    if u["role"] == "admin" and db.count_admins() <= 1:
        raise HTTPException(400, "Keep at least one admin who signs in with Google.")
    google.revoke(uid)
    db.delete_user(uid)
    return admin_users(request)


# ---- built-in admin: server-wide 1.0 connection (Apps Script / service account) ------------------------
@app.post("/api/settings/apps-script")
def apps_script(request: Request, regenerate: bool = False):
    admin_only(request)
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


@app.post("/api/settings/sheets-test")
def sheets_test(request: Request, body: BridgeTest):
    admin_only(request)
    be = sheets.AppsScriptBackend(url=body.url.strip()) if body.url.strip() else sheets.backend()
    res = be.ping()
    who = res.get("user")
    msg = f"Connected{' as ' + who if who else ''}."
    outdated = isinstance(be, sheets.AppsScriptBackend) and int(res.get("version", 1)) < receipts.MIN_BRIDGE_VERSION
    if outdated:
        msg += " Your bridge script is an older version: re-copy the setup script and deploy a new version."
    return {"message": msg, "outdated": outdated}


class SheetRef(BaseModel):
    sheet: str


@app.post("/api/sheet")
def sheet_info(request: Request, ref: SheetRef):
    admin_only(request)
    return sheets.backend().info(sheets.spreadsheet_id_from(ref.sheet))


@app.get("/api/sheets")
def list_sheets(request: Request):
    admin_only(request)
    return {"files": sheets.backend().list_sheets()}


@app.get("/receipts/{rel:path}")
def get_receipt(request: Request, rel: str):
    admin_only(request)
    p = receipts.local_path(rel)
    if not p:
        raise HTTPException(404, "Receipt not found")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ---- PWA ---------------------------------------------------------------------------------------------------
def _static_version() -> str:
    h = hashlib.sha256(VERSION.encode())
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
    return Response(js, media_type="text/javascript", headers={**NO_CACHE, "Service-Worker-Allowed": "/"})


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


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers=NO_CACHE)


app.mount("/static", StaticFiles(directory=STATIC), name="static")
