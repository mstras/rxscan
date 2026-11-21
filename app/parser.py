"""Turn a receipt photo into structured prescription rows using a vision model."""
import base64
import io
import json
import logging

import httpx
from PIL import Image, ImageOps

from . import config
from .config import settings
from .fields import FIELDS, gemini_schema, json_schema

log = logging.getLogger("rxscan.parser")

try:  # iPhone photos picked from the library may be HEIC
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # pragma: no cover
    pass

ANTHROPIC_URL = "https://api.anthropic.com"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"

PROMPT = """You are reading a photo of a pharmacy / prescription receipt (or a pharmacy bag label / Rx label).
Extract every prescription line item. A single receipt can contain several prescriptions: return one entry per prescription.

Rules:
- Copy values exactly as printed; do not guess. Use null for anything not visible or unreadable.
- Dates: YYYY-MM-DD. Money and counts: plain numbers without $ or commas (e.g. "12.50").
- If only a receipt total is shown for multiple prescriptions, put the total on the first one and explain in notes.
- Shared details (pharmacy, date, patient, payment) should be repeated on each prescription entry.
- Never output a full payment card number; at most the last 4 digits.
- In uncertain_fields, list the keys of any values you had to guess at because the print was blurry, faded, cut off or ambiguous. Leave it empty when everything was clear.
- If the image is not a prescription receipt or label, set is_prescription_receipt to false and return an empty list.
"""


class ParseError(Exception):
    pass


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """Fix rotation, downscale, re-encode as JPEG. Returns (bytes, media_type)."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        raise ParseError("Could not read that file as an image (JPEG, PNG, HEIC or WebP)") from e
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    edge = settings.get("max_image_edge")
    img.thumbnail((edge, edge))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue(), "image/jpeg"


def _normalize(result: dict) -> list[dict]:
    rows = []
    for item in result.get("prescriptions") or []:
        row = {}
        for key, *_ in FIELDS:
            v = item.get(key)
            row[key] = "" if v is None else str(v).strip()
        if any(row.values()):
            known = {f[0] for f in FIELDS}
            row["_uncertain"] = [k for k in (item.get("uncertain_fields") or []) if k in known]
            rows.append(row)
    return rows


def _err_detail(r: httpx.Response) -> str:
    try:
        j = r.json()
        e = j.get("error", j)
        return e.get("message", str(e)) if isinstance(e, dict) else str(e)
    except Exception:
        return r.text[:400]


# ---- Gemini ---------------------------------------------------------------
async def _parse_gemini(img: bytes, media_type: str) -> dict:
    key = settings.get("gemini_api_key")
    if not key:
        raise ParseError("Gemini API key is not set (Settings)")
    model = settings.get("gemini_model")
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": media_type, "data": base64.b64encode(img).decode()}},
                {"text": PROMPT},
            ],
        }],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema(),
        },
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{GEMINI_URL}/models/{model}:generateContent",
                              json=body, headers={"x-goog-api-key": key})
    if r.status_code != 200:
        raise ParseError(f"Gemini error {r.status_code}: {_err_detail(r)}")
    data = r.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    except (KeyError, IndexError):
        reason = (data.get("promptFeedback") or {}).get("blockReason") or \
                 ((data.get("candidates") or [{}])[0].get("finishReason"))
        raise ParseError(f"Gemini returned no result ({reason or 'unknown reason'})")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ParseError(f"Gemini returned invalid JSON: {text[:300]}") from e


# ---- Anthropic ------------------------------------------------------------
async def _parse_anthropic(img: bytes, media_type: str) -> dict:
    key = settings.get("anthropic_api_key")
    if not key:
        raise ParseError("Anthropic API key is not set (Settings)")
    body = {
        "model": settings.get("anthropic_model"),
        "max_tokens": 4096,
        "tools": [{
            "name": "record_prescriptions",
            "description": "Record the prescriptions found on the receipt",
            "input_schema": json_schema(),
        }],
        "tool_choice": {"type": "tool", "name": "record_prescriptions"},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                             "data": base64.b64encode(img).decode()}},
                {"type": "text", "text": PROMPT},
            ],
        }],
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{ANTHROPIC_URL}/v1/messages", json=body, headers=_anthropic_headers(key))
    if r.status_code != 200:
        raise ParseError(f"Anthropic error {r.status_code}: {_err_detail(r)}")
    for block in r.json().get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input") or {}
    raise ParseError("Model returned no structured result")


def _anthropic_headers(key):
    return {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}


# ---- Ollama ---------------------------------------------------------------
async def _parse_ollama(img: bytes, media_type: str) -> dict:
    body = {
        "model": settings.get("ollama_model"),
        "stream": False,
        "format": json_schema(),
        "options": {"temperature": 0},
        "messages": [{
            "role": "user",
            "content": PROMPT + "\nRespond only with JSON matching the schema.",
            "images": [base64.b64encode(img).decode()],
        }],
    }
    async with httpx.AsyncClient(timeout=600) as client:
        r = await client.post(f"{settings.get('ollama_url').rstrip('/')}/api/chat", json=body)
    if r.status_code != 200:
        raise ParseError(f"Ollama error {r.status_code}: {_err_detail(r)}")
    content = r.json().get("message", {}).get("content", "")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ParseError(f"Ollama returned invalid JSON: {content[:300]}") from e


def _mock() -> dict:
    return {
        "is_prescription_receipt": True,
        "prescriptions": [{
            "date_filled": "2026-11-18", "patient_name": "Test Patient", "medication": "Atorvastatin",
            "strength": "20 mg", "dosage_form": "Tablet", "quantity": "90", "days_supply": "90",
            "refills_remaining": "3", "rx_number": "0123456", "ndc": "00093-5057-98",
            "prescriber": "Dr. Example", "pharmacy_name": "Sample Pharmacy #1234",
            "pharmacy_address": "1 Main St, Anytown, NY", "pharmacy_phone": "555-0100",
            "insurance_plan": "Sample Rx Plan", "retail_price": "64.99", "insurance_paid": "54.99",
            "savings": "54.99", "patient_paid": "10.00", "payment_method": "VISA x1111",
            "transaction_id": "000987", "notes": None, "uncertain_fields": ["refills_remaining"],
        }],
    }


PARSERS = {"gemini": _parse_gemini, "anthropic": _parse_anthropic, "ollama": _parse_ollama}


def active_model() -> tuple[str, str]:
    if config.MOCK_PARSER:
        return "mock", "mock"
    p = settings.get("vision_provider")
    return p, settings.get(f"{p}_model")


async def parse_receipt(data: bytes) -> dict:
    img, media_type = prepare_image(data)
    if config.MOCK_PARSER:
        result = _mock()
    else:
        provider = settings.get("vision_provider")
        if provider not in PARSERS:
            raise ParseError(f"Unknown vision provider: {provider}")
        result = await PARSERS[provider](img, media_type)
    return {
        "is_prescription_receipt": bool(result.get("is_prescription_receipt", True)),
        "rows": _normalize(result),
    }


# ---- Model lists / connection test (Settings panel) -----------------------
_SKIP = ("tts", "image", "live", "embedding", "audio", "transcribe", "robotics", "omni", "banana", "veo", "imagen", "lyria", "aqa")


async def list_models(provider: str, key: str | None = None, url: str | None = None) -> list[dict]:
    """Return [{id, name}] of models usable for this app. key/url override saved values."""
    async with httpx.AsyncClient(timeout=20) as client:
        if provider == "gemini":
            key = key or settings.get("gemini_api_key")
            if not key:
                raise ParseError("Enter a Gemini API key first")
            r = await client.get(f"{GEMINI_URL}/models", params={"pageSize": 1000}, headers={"x-goog-api-key": key})
            if r.status_code != 200:
                raise ParseError(f"Gemini error {r.status_code}: {_err_detail(r)}")
            out = []
            for m in r.json().get("models", []):
                mid = m.get("name", "").removeprefix("models/")
                if "generateContent" not in m.get("supportedGenerationMethods", []):
                    continue
                if not mid.startswith("gemini") or any(s in mid for s in _SKIP):
                    continue
                out.append({"id": mid, "name": m.get("displayName", mid)})
            return sorted(out, key=lambda m: m["id"], reverse=True)
        if provider == "anthropic":
            key = key or settings.get("anthropic_api_key")
            if not key:
                raise ParseError("Enter an Anthropic API key first")
            r = await client.get(f"{ANTHROPIC_URL}/v1/models", params={"limit": 100}, headers=_anthropic_headers(key))
            if r.status_code != 200:
                raise ParseError(f"Anthropic error {r.status_code}: {_err_detail(r)}")
            return [{"id": m["id"], "name": m.get("display_name", m["id"])} for m in r.json().get("data", [])]
        if provider == "ollama":
            base = (url or settings.get("ollama_url")).rstrip("/")
            try:
                r = await client.get(f"{base}/api/tags")
            except httpx.HTTPError as e:
                raise ParseError(f"Can't reach Ollama at {base}: {e}") from e
            if r.status_code != 200:
                raise ParseError(f"Ollama error {r.status_code}: {_err_detail(r)}")
            return [{"id": m["name"], "name": m["name"]} for m in r.json().get("models", [])]
    raise ParseError(f"Unknown provider: {provider}")


async def test_connection(provider: str, model: str, key: str | None = None, url: str | None = None) -> str:
    models = await list_models(provider, key, url)
    ids = {m["id"] for m in models}
    if model and model not in ids:
        return f"Connected, but '{model}' isn't in this account's model list. Check the name."
    return f"Connected. {model or 'Model'} is available."
