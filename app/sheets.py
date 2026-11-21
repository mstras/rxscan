"""Google Sheets access.

Two backends behind one interface:
- AppsScriptBackend: a small Apps Script web app deployed in the user's own Google
  account (app/apps_script.gs). Runs as the user: no key file, no sharing, can list
  and create sheets. Recommended.
- ServiceAccountBackend: Sheets REST API with a service-account key. Each sheet must
  be shared with the service account. Can't list or create sheets.

Header mapping and value formatting live here, shared by both.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from . import config
from .config import settings
from .fields import ALL_HEADERS, ALL_KEYS, KEY_TO_HEADER, TEXT_ID_KEYS

SCRIPT_TEMPLATE = Path(__file__).parent / "apps_script.gs"


class SheetsError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def spreadsheet_id_from(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9-_]{20,}", s):
        return s
    raise SheetsError("That doesn't look like a Google Sheets URL or ID")


def sheet_url(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


# ---- Apps Script ----------------------------------------------------------
def apps_script_source(secret: str) -> str:
    return SCRIPT_TEMPLATE.read_text().replace("__RXSCAN_SECRET__", secret)


class AppsScriptBackend:
    name = "apps_script"
    can_list = True
    can_create = True

    def __init__(self, url: str | None = None, secret: str | None = None):
        self.url = (url or settings.get("apps_script_url")).strip()
        self.secret = secret or settings.get("apps_script_secret")
        if not self.url:
            raise SheetsError("Google Sheets isn't connected yet. Open Settings → Google Sheets.", 503)
        if not self.secret:
            raise SheetsError("No bridge secret saved. In Settings, tap 'Copy setup script' first.", 503)

    def call(self, action: str, **params) -> dict:
        try:
            r = httpx.post(self.url, json={"secret": self.secret, "action": action, **params},
                           follow_redirects=True, timeout=60)
        except httpx.HTTPError as e:
            raise SheetsError(f"Can't reach the Apps Script bridge: {e}", 503) from e
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or "json" not in ctype:
            text = r.text
            if "accounts.google.com" in str(r.url) or "ServiceLogin" in text:
                hint = "Google asked for a login. Redeploy the web app with 'Who has access: Anyone'."
            elif r.status_code == 404:
                hint = "Script not found. Check that you pasted the /exec URL of the current deployment."
            else:
                hint = f"HTTP {r.status_code}. Check the URL ends in /exec and the deployment is active."
            raise SheetsError(f"Apps Script bridge error: {hint}", 503)
        data = r.json()
        if "error" in data:
            msg = data["error"]
            if msg == "Bad secret":
                msg = "Secret mismatch. Re-copy the setup script from Settings and paste it into Apps Script again (then Deploy → Manage deployments → edit → New version)."
            elif "not have permission" in msg or "Requested entity was not found" in msg:
                msg = "Google says you don't have access to that sheet (or it doesn't exist)."
            raise SheetsError(msg, 400)
        return data

    def ping(self):
        return self.call("ping")

    def list_sheets(self):
        return self.call("list")["files"]

    def create(self, title: str, tab: str):
        return self.call("create", title=title, tab=tab)

    def info(self, sid):
        d = self.call("info", id=sid)
        d["url"] = sheet_url(sid)
        return d

    def get_header(self, sid, tab):
        return [str(h).strip() for h in self.call("header", id=sid, tab=tab)["header"]]

    def set_header(self, sid, tab, header):
        self.call("set_header", id=sid, tab=tab, header=header)

    def append_values(self, sid, tab, values):
        return self.call("append", id=sid, tab=tab, values=values)


# ---- Service account ------------------------------------------------------
SA_API = "https://sheets.googleapis.com/v4/spreadsheets"
_sa_session = None


def service_account_email() -> str | None:
    try:
        with open(config.GOOGLE_CREDENTIALS_FILE) as f:
            return json.load(f).get("client_email")
    except Exception:
        return None


def service_account_available() -> bool:
    return os.path.isfile(config.GOOGLE_CREDENTIALS_FILE)


def _a1_tab(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


class ServiceAccountBackend:
    name = "service_account"
    can_list = False
    can_create = False

    def _session(self):
        global _sa_session
        if _sa_session is None:
            path = config.GOOGLE_CREDENTIALS_FILE
            if os.path.isdir(path):
                raise SheetsError(
                    f"{path} is a folder, not a key file. Docker creates that when the file is missing. "
                    "Delete config/service-account.json on the host and put the JSON key there, "
                    "or use the Apps Script connection instead (Settings → Google Sheets).", 503)
            if not os.path.isfile(path):
                raise SheetsError("Google Sheets isn't connected yet. Open Settings → Google Sheets.", 503)
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account

            try:
                creds = service_account.Credentials.from_service_account_file(
                    path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            except Exception as e:
                raise SheetsError(f"Couldn't load the service account key: {e}", 503) from e
            _sa_session = AuthorizedSession(creds)
        return _sa_session

    def _req(self, method, url, **kw):
        r = self._session().request(method, url, timeout=30, **kw)
        if r.status_code in (403, 404):
            raise SheetsError(
                f"Google returned {r.status_code}. Share the sheet with {service_account_email() or 'the service account'} "
                "as an Editor and check the URL.", r.status_code)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            raise SheetsError(f"Google Sheets error {r.status_code}: {msg}", r.status_code)
        return r.json() if r.content else {}

    def ping(self):
        self._session()
        return {"ok": True, "user": service_account_email()}

    def list_sheets(self):
        raise SheetsError("Listing sheets needs the Apps Script connection.")

    def create(self, title, tab):
        raise SheetsError("Creating sheets needs the Apps Script connection.")

    def info(self, sid):
        d = self._req("GET", f"{SA_API}/{sid}", params={"fields": "properties.title,sheets.properties.title"})
        return {"id": sid, "title": d.get("properties", {}).get("title", ""),
                "tabs": [s["properties"]["title"] for s in d.get("sheets", [])], "url": sheet_url(sid)}

    def get_header(self, sid, tab):
        d = self._req("GET", f"{SA_API}/{sid}/values/{quote(_a1_tab(tab) + '!1:1', safe='')}")
        return [str(v).strip() for v in (d.get("values") or [[]])[0]]

    def set_header(self, sid, tab, header):
        self._req("PUT", f"{SA_API}/{sid}/values/{quote(_a1_tab(tab) + '!A1', safe='')}",
                  params={"valueInputOption": "RAW"}, json={"values": [header]})

    def append_values(self, sid, tab, values):
        d = self._req("POST", f"{SA_API}/{sid}/values/{quote(_a1_tab(tab) + '!A1', safe='')}:append",
                      params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                      json={"values": values})
        u = d.get("updates", {})
        return {"appended": u.get("updatedRows", len(values)), "range": u.get("updatedRange")}


# ---- selection + shared logic ---------------------------------------------
def active_backend_name() -> str | None:
    b = settings.get("sheets_backend")
    if b == "auto":
        if settings.get("apps_script_url"):
            return "apps_script"
        if service_account_available():
            return "service_account"
        return None
    return b


def backend():
    name = active_backend_name()
    if name == "apps_script":
        return AppsScriptBackend()
    if name == "service_account":
        return ServiceAccountBackend()
    raise SheetsError("Google Sheets isn't connected yet. Open Settings → Google Sheets.", 503)


def capabilities() -> dict:
    name = active_backend_name()
    cls = {"apps_script": AppsScriptBackend, "service_account": ServiceAccountBackend}.get(name)
    return {"backend": name, "can_list": bool(cls and cls.can_list), "can_create": bool(cls and cls.can_create),
            "service_account_email": service_account_email() if name == "service_account" else None}


def _cell(key: str, value) -> str:
    v = "" if value is None else str(value)
    if key == "receipt_url":
        if not v.startswith(("https://", "http://")):
            return ""
        return '=HYPERLINK("' + v.replace('"', "%22") + '","View receipt")'
    if v and key in TEXT_ID_KEYS and not v.startswith("'"):
        return "'" + v
    return v


def append_rows(sid: str, tab: str, rows: list[dict], be=None) -> dict:
    if not rows:
        raise SheetsError("No rows to append")
    be = be or backend()

    header = be.get_header(sid, tab)
    created_header = False
    if not any(header):
        header = list(ALL_HEADERS)
        be.set_header(sid, tab, header)
        created_header = True
    else:
        lower = {h.lower() for h in header}
        if settings.get("add_missing_columns"):
            missing = [h for h in ALL_HEADERS if h.lower() not in lower]
        else:
            # Always make room for receipt links, even on sheets with their own headers.
            has_links = any(r.get("receipt_url") for r in rows)
            missing = [KEY_TO_HEADER["receipt_url"]] if has_links and "receipt" not in lower and "receipt_url" not in lower else []
        if missing:
            header = header + missing
            be.set_header(sid, tab, header)

    lookup = {}
    for key in ALL_KEYS:
        lookup[KEY_TO_HEADER[key].lower()] = key
        lookup[key.lower()] = key
    col_keys = [lookup.get(h.lower()) for h in header]
    if not any(col_keys):
        raise SheetsError(
            "Row 1 of that tab has headers, but none match RxScan's columns. "
            "Pick an empty tab, or create a new sheet from the app.")

    now = datetime.now(ZoneInfo(config.TZ)).strftime("%Y-%m-%d %H:%M")
    values = []
    for row in rows:
        row = {**row, "added_at": row.get("added_at") or now}
        values.append([_cell(k, row.get(k)) if k else "" for k in col_keys])

    res = be.append_values(sid, tab, values)
    return {
        "appended": res.get("appended", len(values)),
        "range": res.get("range"),
        "created_header": created_header,
        "unmatched_columns": [h for h, k in zip(header, col_keys) if not k and h],
    }
