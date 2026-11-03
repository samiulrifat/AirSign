"""Gemini rewrites a risk decision that is already made into one plain sentence.

The tier comes from the rules and the isolation forest. This only changes the wording,
and any failure keeps the template sentence the risk engine wrote.
"""

from __future__ import annotations

from ..config import get_settings
from . import gemini

TIMEOUT_S = 5
MAX_CHARS = 200
SCHEMA = {"type": "object", "properties": {"sentence": {"type": "string"}}, "required": ["sentence"]}
WHAT_HAPPENS = {2: "one signature, checked more strictly than usual", 3: "two signatures"}


def explain_risk(tier: int, reasons: list[str]) -> str | None:
    """One friendly sentence, or None to keep the template."""
    if tier not in WHAT_HAPPENS or not gemini.enabled():
        return None
    facts = "; ".join(reasons) or "this payment looks unusual"
    prompt = (
        "Write one short, calm sentence telling a shopper why this payment needs an extra check. "
        f"The check is: {WHAT_HAPPENS[tier]}. The reasons are: {facts}. "
        "Use only these facts, address the shopper as you, and do not mention scores, tiers or fraud."
    )
    try:
        out = gemini.generate_json(prompt, SCHEMA, models=[get_settings().gemini_fast_model], timeout=TIMEOUT_S)
    except gemini.GeminiUnavailable:
        return None
    sentence = out.get("sentence")
    if not isinstance(sentence, str):
        return None
    sentence = " ".join(sentence.split())
    return sentence if 0 < len(sentence) <= MAX_CHARS else None
