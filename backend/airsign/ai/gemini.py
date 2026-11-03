"""Google Gemini over REST (generateContent with structured JSON output).

Design rule: Gemini drafts and explains, it never decides. No output of this module
can set a price, a risk tier or a signature result, or move money. Callers validate
what comes back and have a fallback for when Gemini is missing or over its quota.

Only shop items, typed bill text, counter photos and computed risk reasons are sent.
Motion recordings, signature templates, names and locations never are. On the free
tier Google may use requests to improve its products, so nothing personal belongs here.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx

from ..config import get_settings

log = logging.getLogger("airsign.gemini")
API = "https://generativelanguage.googleapis.com/v1beta/models"
RETRY_ON_NEXT_MODEL = {404, 429, 500, 502, 503, 504}


class GeminiUnavailable(RuntimeError):
    pass


def enabled() -> bool:
    return bool(get_settings().gemini_api_key)


def _text_of(data: dict) -> str:
    candidates = data.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts") or []) if candidates else []
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def generate_json(
    prompt: str,
    schema: dict,
    *,
    image: tuple[str, bytes] | None = None,
    models: list[str] | None = None,
    timeout: float = 20,
) -> dict:
    """The model's JSON answer. Tries each model in turn; raises GeminiUnavailable if none answers."""
    s = get_settings()
    if not s.gemini_api_key:
        raise GeminiUnavailable("AIRSIGN_GEMINI_API_KEY is not set")
    parts: list[dict] = [{"text": prompt}]
    if image:
        parts.append({"inlineData": {"mimeType": image[0], "data": base64.b64encode(image[1]).decode()}})
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": schema},
    }
    for model in models or [s.gemini_model, s.gemini_fast_model]:
        try:
            r = httpx.post(
                f"{API}/{model}:generateContent",
                headers={"x-goog-api-key": s.gemini_api_key, "Content-Type": "application/json"},
                json=body,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            log.warning("Gemini %s unreachable: %s", model, type(e).__name__)
            continue
        if r.status_code in RETRY_ON_NEXT_MODEL:
            log.warning("Gemini %s returned %s", model, r.status_code)
            continue
        if r.status_code != 200:
            # 400 and 403 are our request or key, so another model would not help.
            log.error("Gemini %s returned %s", model, r.status_code)
            break
        try:
            out = json.loads(_text_of(r.json()))
        except (ValueError, AttributeError, TypeError):
            out = None
        if isinstance(out, dict):
            return out
        log.warning("Gemini %s did not return a JSON object", model)
    raise GeminiUnavailable("no Gemini model answered")
