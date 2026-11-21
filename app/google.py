"""Sign in with Google, and act in each person's own Sheets/Drive.

Scopes: openid email profile + drive.file. drive.file only reaches files RxScan created or the
person picked with the Google Picker, nothing else in their Drive. It is a non-sensitive scope,
so the app can be published ("In production") without Google's verification review.
"""
import base64
import json
import time
from urllib.parse import quote, urlencode

import httpx

from . import db
from .config import settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
SCOPES = "openid email profile https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"

# One client for all Google calls (tests swap its transport).
client = httpx.Client(timeout=60)


class GoogleError(Exception):
    def __init__(self, message: str, status: int = 400, reconnect: bool = False):
        super().__init__(message)
        self.status = status
        self.reconnect = reconnect


# ---- configuration -------------------------------------------------------------
def config() -> dict:
    return {
        "client_id": settings.get("google_client_id"),
        "client_secret": settings.get("google_client_secret"),
        "api_key": settings.get("google_api_key"),
        "project_number": settings.get("google_project_number"),
    }


def configured() -> bool:
    c = config()
    return bool(c["client_id"] and c["client_secret"])


def picker_ready() -> bool:
    c = config()
    return configured() and bool(c["api_key"] and c["project_number"])


# ---- sign-in flow ----------------------------------------------------------------------
def auth_url(redirect_uri: str, state: str, login_hint: str | None = None, consent: bool = False) -> str:
    params = {
        "client_id": config()["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPES, "access_type": "offline", "include_granted_scopes": "true", "state": state,
        "prompt": "consent select_account" if consent else "select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_request(data: dict) -> dict:
    c = config()
    try:
        r = client.post(TOKEN_URL, data={**data, "client_id": c["client_id"], "client_secret": c["client_secret"]})
    except httpx.HTTPError as e:
        raise GoogleError(f"Couldn't reach Google: {e}", 503) from e
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        err = body.get("error", "")
        if err == "invalid_grant":
            raise GoogleError("Your Google access expired or was removed. Sign in with Google again.", 401, reconnect=True)
        if err in ("invalid_client", "unauthorized_client"):
            raise GoogleError("Google rejected RxScan's client ID or secret. Check Google setup.", 502)
        raise GoogleError(f"Google sign-in failed: {body.get('error_description') or err or r.status_code}", 502)
    return body


def exchange_code(code: str, redirect_uri: str) -> dict:
    return _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})


def identity(id_token: str) -> dict:
    """Claims from an ID token received directly from Google's token endpoint over TLS."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except Exception as e:
        raise GoogleError("Google returned an unreadable sign-in token.", 502) from e
    if claims.get("aud") != config()["client_id"] or claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        raise GoogleError("Sign-in token wasn't issued for RxScan.", 401)
    if claims.get("exp", 0) < time.time() - 60:
        raise GoogleError("Sign-in token expired. Try again.", 401)
    if not claims.get("email") or not claims.get("email_verified", False):
        raise GoogleError("Your Google account's email isn't verified.", 403)
    return claims


def revoke(uid: int):
    t = db.get_tokens(uid)
    if t and t["refresh_token"]:
        try:
            client.post(REVOKE_URL, params={"token": t["refresh_token"]})
        except httpx.HTTPError:
            pass
    db.delete_tokens(uid)


# ---- per-user access ----------------------------------------------------------------------
def access_token(uid: int) -> str:
    t = db.get_tokens(uid)
    if not t or not t["refresh_token"]:
        raise GoogleError("RxScan isn't connected to your Google account yet. Sign in with Google again.", 401, reconnect=True)
    if t["access_token"] and t["expires_at"] > time.time():
        return t["access_token"]
    try:
        body = _token_request({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
    except GoogleError as e:
        if e.reconnect:
            db.delete_tokens(uid)
        raise
    db.update_access_token(uid, body["access_token"], body.get("expires_in", 3600))
    return body["access_token"]


def call(uid: int, method: str, url: str, **kw):
    headers = {**kw.pop("headers", {}), "Authorization": f"Bearer {access_token(uid)}"}
    try:
        r = client.request(method, url, headers=headers, **kw)
    except httpx.HTTPError as e:
        raise GoogleError(f"Couldn't reach Google: {e}", 503) from e
    if r.status_code == 401:
        db.update_access_token(uid, "", 0)
        raise GoogleError("Google asked RxScan to sign in again. Sign in with Google again.", 401, reconnect=True)
    if r.status_code in (403, 404):
        msg = ""
        try:
            msg = r.json().get("error", {}).get("message", "")
        except Exception:
            pass
        if "has not been used" in msg or "is disabled" in msg:
            raise GoogleError("An API isn't turned on in Google Cloud yet (Sheets, Drive or Picker). See Google setup.", 502)
        raise GoogleError("RxScan can't open that file. Pick it again with Change, or create a new one.", r.status_code)
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise GoogleError(f"Google error {r.status_code}: {msg[:300]}", 502)
    return r.json() if r.content else {}


# ---- Sheets ---------------------------------------------------------------------------------
def _a1(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


def sheet_info(uid: int, sid: str) -> dict:
    d = call(uid, "GET", f"{SHEETS}/{sid}", params={"fields": "properties.title,sheets.properties(sheetId,title)"})
    return {"id": sid, "title": d.get("properties", {}).get("title", ""),
            "tabs": [s["properties"]["title"] for s in d.get("sheets", [])],
            "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit"}


def create_sheet(uid: int, title: str, tab: str, header: list[str]) -> dict:
    d = call(uid, "POST", SHEETS, json={
        "properties": {"title": title},
        "sheets": [{"properties": {"title": tab, "gridProperties": {"frozenRowCount": 1}}}],
    })
    sid = d["spreadsheetId"]
    gid = d["sheets"][0]["properties"]["sheetId"]
    be = SheetsBackend(uid)
    be.set_header(sid, tab, header)
    call(uid, "POST", f"{SHEETS}/{sid}:batchUpdate", json={"requests": [{"repeatCell": {
        "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
        "fields": "userEnteredFormat.textFormat.bold"}}]})
    return {"id": sid, "title": title, "tabs": [tab], "url": f"https://docs.google.com/spreadsheets/d/{sid}/edit"}


class SheetsBackend:
    """Same interface as sheets.AppsScriptBackend / ServiceAccountBackend, acting as one person."""
    name = "google_user"
    can_list = False
    can_create = True

    def __init__(self, uid: int):
        self.uid = uid

    def info(self, sid):
        return sheet_info(self.uid, sid)

    def get_header(self, sid, tab):
        d = call(self.uid, "GET", f"{SHEETS}/{sid}/values/{quote(_a1(tab) + '!1:1', safe='')}")
        return [str(v).strip() for v in (d.get("values") or [[]])[0]]

    def set_header(self, sid, tab, header):
        call(self.uid, "PUT", f"{SHEETS}/{sid}/values/{quote(_a1(tab) + '!A1', safe='')}",
             params={"valueInputOption": "RAW"}, json={"values": [header]})

    def append_values(self, sid, tab, values):
        d = call(self.uid, "POST", f"{SHEETS}/{sid}/values/{quote(_a1(tab) + '!A1', safe='')}:append",
                 params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"}, json={"values": values})
        u = d.get("updates", {})
        return {"appended": u.get("updatedRows", len(values)), "range": u.get("updatedRange")}


# ---- Drive -----------------------------------------------------------------------------------
def file_info(uid: int, fid: str) -> dict:
    return call(uid, "GET", f"{DRIVE}/{fid}", params={"fields": "id,name,mimeType,trashed", "supportsAllDrives": "true"})


def create_folder(uid: int, name: str, parent: str | None = None) -> dict:
    body = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        body["parents"] = [parent]
    d = call(uid, "POST", DRIVE, params={"fields": "id,name", "supportsAllDrives": "true"}, json=body)
    return {"id": d["id"], "name": d["name"]}


def child_folder(uid: int, parent: str, name: str) -> str:
    q = f"'{parent}' in parents and name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    d = call(uid, "GET", DRIVE, params={"q": q, "fields": "files(id)", "pageSize": 1,
                                         "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"})
    files = d.get("files") or []
    return files[0]["id"] if files else create_folder(uid, name, parent)["id"]


def upload(uid: int, folder_id: str, name: str, data: bytes, mime: str = "image/jpeg", description: str = "") -> dict:
    meta = {"name": name, "parents": [folder_id], "description": description}
    boundary = "rxscan" + str(int(time.time() * 1000))
    body = (f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{boundary}\r\nContent-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{boundary}--".encode()
    d = call(uid, "POST", UPLOAD, params={"uploadType": "multipart", "fields": "id,name,webViewLink", "supportsAllDrives": "true"},
             headers={"Content-Type": f"multipart/related; boundary={boundary}"}, content=body)
    return {"id": d["id"], "name": d.get("name", name),
            "url": d.get("webViewLink") or f"https://drive.google.com/file/d/{d['id']}/view"}
