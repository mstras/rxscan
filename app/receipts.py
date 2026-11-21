"""Keep a copy of every appended receipt photo, and return a link for the sheet.

Storage modes (setting `receipt_storage`):
- drive: upload to Google Drive via the Apps Script bridge → the chosen folder [/<year>] (private to you)
- local: save on this server under receipts_local_dir [/<year>/<month>], served at /receipts/... (sign-in required)
- off:   don't keep copies
- auto:  drive when the Apps Script connection is active, otherwise local
"""
import base64
import io
import re
import uuid
from urllib.parse import quote
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageOps

from . import config, sheets
from .config import settings

ARCHIVE_EDGE = 3000  # keep archive copies sharper than what we send the model
MIN_BRIDGE_VERSION = 3
OUTDATED_MSG = ("Your Apps Script bridge is out of date. In Settings → Google Sheets, tap 'Copy setup script', "
                "paste it over the old code, then Deploy → Manage deployments → ✏️ → Version: New version → Deploy.")


def local_dir() -> Path:
    return Path(settings.get("receipts_local_dir") or (config.DATA_DIR / "receipts"))


def drive_call(action: str, **kw) -> dict:
    """Call a Drive action on the bridge, translating 'old script' errors."""
    try:
        be = sheets.AppsScriptBackend()
    except sheets.SheetsError as e:
        raise ReceiptError(f"Google Drive needs the Apps Script connection: {e}") from e
    try:
        return be.call(action, **kw)
    except sheets.SheetsError as e:
        if "Unknown action" in str(e):
            raise ReceiptError(OUTDATED_MSG) from e
        raise ReceiptError(str(e)) from e


def folder_id_from(s: str) -> str:
    s = (s or "").strip()
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", s) or re.search(r"[?&]id=([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,}", s):
        return s
    raise ReceiptError("That doesn't look like a Google Drive folder link")


class ReceiptError(Exception):
    pass


def mode() -> str:
    m = settings.get("receipt_storage")
    if m == "auto":
        return "drive" if sheets.active_backend_name() == "apps_script" else "local"
    return m


def _archive_jpeg(data: bytes) -> bytes:
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    except Exception as e:
        raise ReceiptError("Could not read that image") from e
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((ARCHIVE_EDGE, ARCHIVE_EDGE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=90)
    return out.getvalue()


def _slug(s: str, n: int = 40) -> str:
    s = re.sub(r"[^\w\s.-]", "", s or "").strip()
    return re.sub(r"\s+", " ", s)[:n].strip()


def filename(meta: dict) -> str:
    date = meta.get("date") or datetime.now(ZoneInfo(config.TZ)).strftime("%Y-%m-%d")
    parts = [_slug(date, 10), _slug(meta.get("pharmacy", ""), 30), _slug(meta.get("medication", ""), 40)]
    base = " - ".join(p for p in parts if p) or "receipt"
    return f"{base} - {uuid.uuid4().hex[:6]}.jpg"


def _year(meta: dict) -> str:
    d = meta.get("date") or ""
    return d[:4] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else datetime.now(ZoneInfo(config.TZ)).strftime("%Y")


def save(data: bytes, meta: dict, base_url: str) -> dict:
    m = mode()
    if m == "off":
        return {"storage": "off", "url": None}
    jpeg = _archive_jpeg(data)
    name = filename(meta)
    desc = "Saved by RxScan" + (f" · {meta.get('medication')}" if meta.get("medication") else "")

    years = settings.get("receipts_year_folders")
    if m == "drive":
        try:
            res = drive_call("upload", folderId=settings.get("receipts_folder_id") or None,
                             folder=settings.get("receipts_folder"), subfolder=_year(meta) if years else None,
                             name=name, mime="image/jpeg", description=desc,
                             data=base64.b64encode(jpeg).decode())
        except ReceiptError as e:
            if str(e) == OUTDATED_MSG:
                raise
            raise ReceiptError(f"Couldn't save the receipt to Drive: {e}") from e
        return {"storage": "drive", "url": res["url"], "name": res.get("name", name)}

    # local
    now = datetime.now(ZoneInfo(config.TZ))
    rel = (Path(_year(meta)) / now.strftime("%m") / name) if years else Path(name)
    dest = local_dir() / rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(jpeg)
    except OSError as e:
        raise ReceiptError(f"Couldn't save the receipt to {local_dir()} ({e}). Is that folder mounted and writable by UID 1000?") from e
    return {"storage": "local", "url": f"{base_url.rstrip('/')}/receipts/{quote(rel.as_posix())}", "name": name}


def save_for_user(uid: int, data: bytes, meta: dict) -> dict:
    """Upload to the person's own Drive folder (Google sign-in users)."""
    from . import db, google
    st = db.get_settings(uid)
    if not st["folder_id"]:
        raise ReceiptError("No receipt folder yet. Open Settings and choose or create one.")
    jpeg = _archive_jpeg(data)
    name = filename(meta)
    desc = "Saved by RxScan" + (f" · {meta.get('medication')}" if meta.get("medication") else "")
    try:
        dest = google.child_folder(uid, st["folder_id"], _year(meta)) if st["year_folders"] else st["folder_id"]
        res = google.upload(uid, dest, name, jpeg, "image/jpeg", desc)
    except google.GoogleError as e:
        raise ReceiptError(f"Couldn't save the receipt photo to Drive: {e}") from e
    return {"storage": "drive", "url": res["url"], "name": res["name"]}


def local_path(rel: str) -> Path | None:
    """Resolve /receipts/<rel> safely inside RECEIPTS_DIR."""
    base = local_dir()
    try:
        p = (base / rel).resolve()
        p.relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return p if p.is_file() else None


def bridge_status() -> dict | None:
    """For Settings: is the deployed bridge new enough for Drive uploads?"""
    if sheets.active_backend_name() != "apps_script":
        return None
    try:
        v = int(sheets.AppsScriptBackend().ping().get("version", 1))
    except Exception:
        return None
    return {"version": v, "outdated": v < MIN_BRIDGE_VERSION}
