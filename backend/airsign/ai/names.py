"""Match a spoken name to the customers nearby, for the cashier's AI auto mode.

Runs on our server, so names never go to Gemini. Speech recognition spells names
in many ways (Rahim, Raheem, Rohim), so names are also compared by their consonants.
A send only happens on one clear match; anything else goes back to the cashier.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

CLEAR_MATCH = 0.8
CLEAR_LEAD = 0.12
SHOW_FROM = 0.5
SAME_SOUND = 0.92
FILLER = {
    "my", "name", "names", "is", "i", "am", "im", "it", "its", "this", "the", "for", "to", "send", "please", "me", "and",
    "hello", "hi", "mister", "mr", "mrs", "ms", "miss", "sir", "madam", "amar", "nam", "naam", "ami", "bhai", "vai", "apu", "apa",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", text.lower())


def _consonants(word: str) -> str:
    """First letter plus the later consonants, with doubles collapsed: raheem and rohim both become rhm."""
    word = word.replace("ph", "f").replace("v", "b")
    out = word[:1] + re.sub(r"[aeiouy]", "", word[1:])
    return re.sub(r"(.)\1+", r"\1", out)


def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    plain = SequenceMatcher(None, a, b).ratio()
    ca, cb = _consonants(a), _consonants(b)
    if len(ca) >= 2 and ca == cb:
        return max(plain, SAME_SOUND)
    return max(plain, SequenceMatcher(None, ca, cb).ratio() * 0.85)


def score(heard: list[str], name: str) -> float:
    """Best similarity between any word heard and any word of the name."""
    words = {w for text in heard for w in _words(text) if w not in FILLER}
    parts = _words(name)
    return max((_similarity(w, p) for w in words for p in parts), default=0.0)


def match_name(heard: list[str], candidates: list[tuple[str, str]]) -> tuple[str | None, list[str]]:
    """(id of the one clear match or None, ids to offer the cashier, best first). Candidates are (id, name)."""
    ranked = sorted(((score(heard, name), cid) for cid, name in candidates), key=lambda r: -r[0])
    if not ranked:
        return None, []
    top = ranked[0][0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if top >= CLEAR_MATCH and top - runner_up >= CLEAR_LEAD:
        return ranked[0][1], [ranked[0][1]]
    likely = [cid for s, cid in ranked if s >= SHOW_FROM]
    return None, (likely or [cid for _, cid in ranked])[:5]
