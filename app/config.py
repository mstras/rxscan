"""Configuration.

Two layers:
- Env-only settings (secrets for the app itself, paths): read once at startup.
- Runtime settings (vision provider, API keys, models, sheet defaults): env vars give
  the defaults, and values saved from the in-app Settings panel (DATA_DIR/settings.json)
  override them. Read with settings.get(name).
"""
import json
import os
import threading
from pathlib import Path


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# ---- env-only -------------------------------------------------------------
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "/config/service-account.json")
# Built-in login (admin / admin123). APP_USERNAME / APP_PASSWORD in .env override these.
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin123"
APP_USERNAME = os.getenv("APP_USERNAME") or DEFAULT_USERNAME
APP_PASSWORD = os.getenv("APP_PASSWORD") or DEFAULT_PASSWORD
MOCK_PARSER = _bool(os.getenv("MOCK_PARSER", "false"))
TZ = os.getenv("TZ", "America/New_York")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SETTINGS_FILE = DATA_DIR / "settings.json"

PROVIDERS = ["gemini", "anthropic", "ollama"]
SHEETS_BACKENDS = ["auto", "apps_script", "service_account"]
RECEIPT_STORAGES = ["auto", "drive", "local", "off"]

# ---- runtime settings -----------------------------------------------------
# name: (env var, default, type, is_secret)
SPEC = {
    "vision_provider": ("VISION_PROVIDER", "gemini", str, False),
    "gemini_api_key": ("GEMINI_API_KEY", "", str, True),
    "gemini_model": ("GEMINI_MODEL", "gemini-3.8-flash", str, False),
    "anthropic_api_key": ("ANTHROPIC_API_KEY", "", str, True),
    "anthropic_model": ("ANTHROPIC_MODEL", "claude-sonnet-5-5", str, False),
    "ollama_url": ("OLLAMA_URL", "http://ollama:11434", str, False),
    "ollama_model": ("OLLAMA_MODEL", "qwen2.5vl:7b", str, False),
    "sheets_backend": ("SHEETS_BACKEND", "auto", str, False),
    "apps_script_url": ("APPS_SCRIPT_URL", "", str, False),
    "apps_script_secret": ("APPS_SCRIPT_SECRET", "", str, True),
    "receipt_storage": ("RECEIPT_STORAGE", "auto", str, False),
    "receipts_folder": ("RECEIPTS_FOLDER", "RxScan Receipts", str, False),
    "receipts_folder_id": ("RECEIPTS_FOLDER_ID", "", str, False),
    "receipts_folder_path": ("RECEIPTS_FOLDER_PATH", "", str, False),
    "receipts_year_folders": ("RECEIPTS_YEAR_FOLDERS", True, bool, False),
    "receipts_local_dir": ("RECEIPTS_LOCAL_DIR", "/data/receipts", str, False),
    "default_sheet_url": ("DEFAULT_SHEET_URL", "", str, False),
    "default_sheet_tab": ("DEFAULT_SHEET_TAB", "", str, False),
    "add_missing_columns": ("ADD_MISSING_COLUMNS", False, bool, False),
    "max_image_edge": ("MAX_IMAGE_EDGE", 2000, int, False),
}
SECRETS = {k for k, s in SPEC.items() if s[3]}


def _coerce(name, value):
    typ = SPEC[name][2]
    if typ is bool:
        return _bool(value)
    if typ is int:
        return int(value)
    return str(value).strip()


class Settings:
    def __init__(self):
        self._lock = threading.Lock()
        self._saved = {}
        self._load()

    def _load(self):
        try:
            self._saved = json.loads(SETTINGS_FILE.read_text())
        except FileNotFoundError:
            self._saved = {}
        except Exception:
            self._saved = {}

    def get(self, name):
        env, default, _t, _s = SPEC[name]
        if name in self._saved and self._saved[name] not in (None, ""):
            return _coerce(name, self._saved[name])
        if os.getenv(env) not in (None, ""):
            return _coerce(name, os.getenv(env))
        return default

    def source(self, name) -> str:
        if name in self._saved and self._saved[name] not in (None, ""):
            return "app"
        if os.getenv(SPEC[name][0]) not in (None, ""):
            return "env"
        return "default"

    def public(self) -> dict:
        """All settings for the UI; secrets are masked."""
        out = {}
        for name in SPEC:
            v = self.get(name)
            if name in SECRETS:
                out[name] = {"set": bool(v), "hint": ("…" + v[-4:]) if v and len(v) > 8 else ("set" if v else ""),
                             "source": self.source(name)}
            else:
                out[name] = {"value": v, "source": self.source(name)}
        return out

    def update(self, changes: dict, clear: list[str] | None = None):
        """Save changes. Empty string for a secret means 'keep current'. `clear` removes app overrides."""
        with self._lock:
            data = dict(self._saved)
            for name, value in changes.items():
                if name not in SPEC:
                    raise ValueError(f"Unknown setting: {name}")
                if name in SECRETS and (value is None or str(value).strip() == ""):
                    continue
                if name == "vision_provider" and value not in PROVIDERS:
                    raise ValueError(f"Provider must be one of {', '.join(PROVIDERS)}")
                if name == "sheets_backend" and value not in SHEETS_BACKENDS:
                    raise ValueError(f"Sheets connection must be one of {', '.join(SHEETS_BACKENDS)}")
                if name == "receipt_storage" and value not in RECEIPT_STORAGES:
                    raise ValueError(f"Receipt storage must be one of {', '.join(RECEIPT_STORAGES)}")
                if name == "receipts_local_dir" and value and not str(value).strip().startswith("/"):
                    raise ValueError("The server folder must be an absolute path inside the container, e.g. /receipts")
                if name == "apps_script_url" and value and not str(value).strip().startswith("https://script.google.com/"):
                    raise ValueError("The Apps Script URL should start with https://script.google.com/ and end with /exec")
                data[name] = _coerce(name, value) if value not in (None, "") else ""
            for name in clear or []:
                data.pop(name, None)
            try:
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                tmp = SETTINGS_FILE.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2))
                os.chmod(tmp, 0o600)
                tmp.replace(SETTINGS_FILE)
            except PermissionError as e:
                raise ValueError(
                    f"Can't write {SETTINGS_FILE}. Make the data folder writable by UID 1000 "
                    f"(chown 1000:1000 ./data)."
                ) from e
            self._saved = data


settings = Settings()
