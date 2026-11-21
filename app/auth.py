"""Who is signed in.

Two kinds of people:
- Google users: sign in with Google; must be on the People allowlist. Each has their own sheet and folder.
- Built-in admin: username/password (admin / admin123 unless APP_USERNAME / APP_PASSWORD are set).
  A fallback for setup and recovery; uses the server-wide (1.0-style) Sheets connection.

Sessions are a signed cookie: "<subject>.<version>.<expiry>.<signature>".
- subject "a" = built-in admin; version is a fingerprint of the credentials (changing them signs out).
- subject "u<id>" = Google user; version is their session_version (bumped on remove / sign out everywhere).
HTTP Basic with the admin credentials also works, for curl/scripts.
"""
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict, deque

from . import config, db

log = logging.getLogger("rxscan.auth")

COOKIE = "rxscan_session"
STATE_COOKIE = "rxscan_oauth"
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "90"))
USERNAME = config.APP_USERNAME
PASSWORD = config.APP_PASSWORD
ENABLED = True  # sign-in is always required

PUBLIC_PREFIXES = ("/static/", "/auth/google/")
PUBLIC_PATHS = {"/login", "/logout", "/healthz", "/sw.js", "/manifest.webmanifest", "/favicon.ico",
                "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"}

MAX_FAILS, WINDOW = 8, 15 * 60
_fails: dict[str, deque] = defaultdict(deque)


def _load_key() -> bytes:
    path = config.DATA_DIR / "session.key"
    try:
        if path.exists():
            k = path.read_bytes()
            if len(k) >= 32:
                return k
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        k = secrets.token_bytes(32)
        path.write_bytes(k)
        os.chmod(path, 0o600)
        return k
    except OSError as e:
        log.warning("Can't persist session key (%s); sign-ins will reset when the container restarts.", e)
        return secrets.token_bytes(32)


_KEY = _load_key()
_FPR = hashlib.sha256(f"{USERNAME}\0{PASSWORD}".encode()).hexdigest()[:16]


def _sign(msg: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(_KEY, msg.encode(), hashlib.sha256).digest()).decode().rstrip("=")


# ---- sessions ----------------------------------------------------------------------
def make_session(user: dict | None = None) -> str:
    """user=None → built-in admin session."""
    sub, ver = ("a", _FPR) if user is None else (f"u{user['id']}", str(user["session_version"]))
    msg = f"{sub}.{ver}.{int(time.time()) + SESSION_DAYS * 86400}"
    return f"{msg}.{_sign(msg)}"


LOCAL_ADMIN = {"id": 0, "kind": "local", "email": "", "name": "Admin (password)", "picture": "", "role": "admin"}


def _from_session(token: str | None) -> dict | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 4:
        return None
    sub, ver, exp, sig = parts
    if not hmac.compare_digest(sig, _sign(f"{sub}.{ver}.{exp}")):
        return None
    try:
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    if sub == "a":
        return dict(LOCAL_ADMIN) if ver == _FPR else None
    if sub.startswith("u") and sub[1:].isdigit():
        u = db.get_user(int(sub[1:]))
        if u and str(u["session_version"]) == ver and u["status"] == "active":
            return {**u, "kind": "google"}
    return None


def check_credentials(username: str, password: str) -> bool:
    ok_u = hmac.compare_digest((username or "").encode(), USERNAME.encode())
    ok_p = hmac.compare_digest((password or "").encode(), PASSWORD.encode())
    return ok_u and ok_p


def _from_basic(header: str | None) -> dict | None:
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
    except Exception:
        return None
    return dict(LOCAL_ADMIN) if check_credentials(user, pw) else None


def current_user(request) -> dict | None:
    return _from_session(request.cookies.get(COOKIE)) or _from_basic(request.headers.get("authorization"))


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


# ---- OAuth state (CSRF) -------------------------------------------------------------------
def make_state(nxt: str) -> tuple[str, str]:
    """Returns (state for Google, cookie value). The cookie binds the state to this browser."""
    nonce = secrets.token_urlsafe(16)
    nxt = safe_next(nxt)
    raw = f"{nonce}|{int(time.time()) + 600}|{base64.urlsafe_b64encode(nxt.encode()).decode()}"
    return f"{raw}|{_sign(raw)}", nonce


def check_state(state: str, cookie_nonce: str | None) -> str | None:
    """Returns the 'next' path if valid."""
    try:
        nonce, exp, nxt_b64, sig = state.split("|")
    except (ValueError, AttributeError):
        return None
    if not hmac.compare_digest(sig, _sign(f"{nonce}|{exp}|{nxt_b64}")):
        return None
    if not cookie_nonce or not hmac.compare_digest(nonce, cookie_nonce) or int(exp) < time.time():
        return None
    return safe_next(base64.urlsafe_b64decode(nxt_b64).decode())


# ---- brute-force protection ------------------------------------------------------------------
def client_ip(request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "?")


def too_many_failures(ip: str) -> bool:
    q = _fails[ip]
    now = time.time()
    while q and q[0] < now - WINDOW:
        q.popleft()
    return len(q) >= MAX_FAILS


def record_failure(ip: str):
    _fails[ip].append(time.time())


def clear_failures(ip: str):
    _fails.pop(ip, None)


def safe_next(nxt: str | None) -> str:
    if nxt and nxt.startswith("/") and not nxt.startswith("//") and not nxt.startswith(("/login", "/auth/")):
        return nxt
    return "/"
