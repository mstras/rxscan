"""SQLite storage for people, their sheet/folder choices, and their Google access.

Lives in DATA_DIR/rxscan.db. Google refresh tokens are encrypted at rest with a key in
DATA_DIR/secret.key (Fernet). Everything else here is non-secret.
"""
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken

from . import config

DB_PATH = config.DATA_DIR / "rxscan.db"
KEY_PATH = config.DATA_DIR / "secret.key"
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT DEFAULT '',
  picture TEXT DEFAULT '',
  role TEXT NOT NULL DEFAULT 'member',           -- admin | member
  status TEXT NOT NULL DEFAULT 'invited',        -- invited | active
  session_version INTEGER NOT NULL DEFAULT 1,    -- bump to sign someone out everywhere
  created_at REAL NOT NULL,
  last_seen REAL
);
CREATE TABLE IF NOT EXISTS user_settings (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  sheet_id TEXT DEFAULT '', sheet_title TEXT DEFAULT '', tab TEXT DEFAULT '',
  folder_id TEXT DEFAULT '', folder_name TEXT DEFAULT '',
  year_folders INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS google_tokens (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  refresh_token TEXT NOT NULL,                   -- encrypted
  access_token TEXT DEFAULT '', expires_at REAL DEFAULT 0, scope TEXT DEFAULT ''
);
"""


def _fernet() -> Fernet:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not KEY_PATH.exists():
        KEY_PATH.write_bytes(Fernet.generate_key())
        os.chmod(KEY_PATH, 0o600)
    return Fernet(KEY_PATH.read_bytes().strip())


_F = None


def encrypt(s: str) -> str:
    global _F
    _F = _F or _fernet()
    return _F.encrypt(s.encode()).decode()


def decrypt(s: str) -> str | None:
    global _F
    _F = _F or _fernet()
    try:
        return _F.decrypt(s.encode()).decode()
    except (InvalidToken, ValueError):
        return None


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    # Seed admins from ADMIN_EMAILS (comma-separated) so the first Google sign-in works.
    for email in [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]:
        if not get_user_by_email(email):
            add_user(email, role="admin")


def _row(r):
    return dict(r) if r else None


# ---- users -------------------------------------------------------------------
def get_user(uid: int):
    with conn() as c:
        return _row(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


def get_user_by_email(email: str):
    with conn() as c:
        return _row(c.execute("SELECT * FROM users WHERE email=?", (email.strip(),)).fetchone())


def list_users():
    with conn() as c:
        rows = c.execute("""SELECT u.*, s.sheet_title, s.folder_name,
                                   (SELECT 1 FROM google_tokens t WHERE t.user_id=u.id) AS connected
                            FROM users u LEFT JOIN user_settings s ON s.user_id=u.id
                            ORDER BY u.role='admin' DESC, u.status='active' DESC, lower(u.name || u.email)""").fetchall()
        return [dict(r) for r in rows]


def count_admins() -> int:
    with conn() as c:
        return c.execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0]


def add_user(email: str, role: str = "member"):
    email = email.strip().lower()
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO users(email, role, created_at) VALUES (?,?,?)", (email, role, time.time()))
    return get_user_by_email(email)


def update_user(uid: int, **fields):
    allowed = {"name", "picture", "role", "status", "last_seen"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    with conn() as c:
        c.execute(f"UPDATE users SET {', '.join(k + '=?' for k in sets)} WHERE id=?", (*sets.values(), uid))


def bump_session(uid: int):
    with conn() as c:
        c.execute("UPDATE users SET session_version = session_version + 1 WHERE id=?", (uid,))


def delete_user(uid: int):
    with conn() as c:
        c.execute("DELETE FROM users WHERE id=?", (uid,))


def touch(uid: int):
    with conn() as c:
        c.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), uid))


# ---- per-user settings ---------------------------------------------------------
SETTING_KEYS = ("sheet_id", "sheet_title", "tab", "folder_id", "folder_name", "year_folders")


def get_settings(uid: int) -> dict:
    with conn() as c:
        r = c.execute("SELECT * FROM user_settings WHERE user_id=?", (uid,)).fetchone()
    d = dict(r) if r else {"sheet_id": "", "sheet_title": "", "tab": "", "folder_id": "", "folder_name": "", "year_folders": 1}
    d["year_folders"] = bool(d.get("year_folders", 1))
    d.pop("user_id", None)
    return d


def set_settings(uid: int, **fields):
    cur = get_settings(uid)
    cur.update({k: v for k, v in fields.items() if k in SETTING_KEYS})
    with conn() as c:
        c.execute("""INSERT INTO user_settings(user_id, sheet_id, sheet_title, tab, folder_id, folder_name, year_folders)
                     VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET sheet_id=excluded.sheet_id, sheet_title=excluded.sheet_title,
                       tab=excluded.tab, folder_id=excluded.folder_id, folder_name=excluded.folder_name,
                       year_folders=excluded.year_folders""",
                  (uid, cur["sheet_id"], cur["sheet_title"], cur["tab"], cur["folder_id"], cur["folder_name"],
                   int(bool(cur["year_folders"]))))
    return get_settings(uid)


# ---- Google tokens -----------------------------------------------------------------
def save_tokens(uid: int, refresh_token: str | None, access_token: str, expires_in: int, scope: str):
    with conn() as c:
        existing = c.execute("SELECT refresh_token FROM google_tokens WHERE user_id=?", (uid,)).fetchone()
        rt = encrypt(refresh_token) if refresh_token else (existing["refresh_token"] if existing else None)
        if not rt:
            return False
        c.execute("""INSERT INTO google_tokens(user_id, refresh_token, access_token, expires_at, scope) VALUES (?,?,?,?,?)
                     ON CONFLICT(user_id) DO UPDATE SET refresh_token=excluded.refresh_token,
                       access_token=excluded.access_token, expires_at=excluded.expires_at, scope=excluded.scope""",
                  (uid, rt, encrypt(access_token), time.time() + int(expires_in) - 60, scope))
    return True


def get_tokens(uid: int):
    with conn() as c:
        r = c.execute("SELECT * FROM google_tokens WHERE user_id=?", (uid,)).fetchone()
    if not r:
        return None
    return {"refresh_token": decrypt(r["refresh_token"]), "access_token": decrypt(r["access_token"]) if r["access_token"] else "",
            "expires_at": r["expires_at"], "scope": r["scope"]}


def update_access_token(uid: int, access_token: str, expires_in: int):
    with conn() as c:
        c.execute("UPDATE google_tokens SET access_token=?, expires_at=? WHERE user_id=?",
                  (encrypt(access_token), time.time() + int(expires_in) - 60, uid))


def delete_tokens(uid: int):
    with conn() as c:
        c.execute("DELETE FROM google_tokens WHERE user_id=?", (uid,))
