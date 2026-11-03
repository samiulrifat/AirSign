"""Rate limits (technical report, section 13.3).

A sliding-window counter per (rule, IP) and per (rule, user), kept in memory.
That is enough for the single instance the demo runs on; several instances would
need a shared store such as Redis.

Brute-force targets matter most: the cashier PIN, the six-digit check-in code
and the lab code.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .auth import SESSION_COOKIE
from .config import get_settings
from .security import unsign_value


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_s: int
    per_user: bool = True  # also count per signed-in customer


RULES = {
    "session": Rule("session", 20, 3600, per_user=False),
    "enroll": Rule("enroll", 60, 600),
    "intent": Rule("intent", 30, 600),
    "signoff": Rule("signoff", 30, 600),
    "checkin": Rule("checkin", 20, 600),
    "pos_login": Rule("pos_login", 10, 600, per_user=False),
    "lab_login": Rule("lab_login", 10, 600, per_user=False),
    "lab_summary": Rule("lab_summary", 6, 600, per_user=False),
    "sim": Rule("sim", 120, 600),
    "ai_bill": Rule("ai_bill", 30, 600, per_user=False),
    "bills": Rule("bills", 300, 600),
    "wallet": Rule("wallet", 20, 600),
    "send_bill": Rule("send_bill", 120, 600, per_user=False),
}


class Limiter:
    def __init__(self) -> None:
        self.enabled: bool | None = None  # None: follow settings
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def hit(self, rule: Rule, keys: list[str]) -> float | None:
        """Record a hit for each key. Returns seconds to wait if any key is over the limit."""
        now = time.monotonic()
        wait = None
        with self._lock:
            self._calls += 1
            if self._calls % 1000 == 0:
                self._prune(now)
            for key in keys:
                q = self._hits.setdefault((rule.name, key), deque())
                while q and q[0] <= now - rule.window_s:
                    q.popleft()
                if len(q) >= rule.limit:
                    w = q[0] + rule.window_s - now
                    wait = w if wait is None else max(wait, w)
            if wait is None:
                for key in keys:
                    self._hits[(rule.name, key)].append(now)
        return wait

    def _prune(self, now: float) -> None:
        longest = max(r.window_s for r in RULES.values())
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - longest]:
            del self._hits[k]


limiter = Limiter()


def client_ip(request: Request) -> str:
    # Behind Render's proxy uvicorn runs with --proxy-headers, so this is the real client.
    return request.client.host if request.client else "unknown"


def rate_limit(name: str):
    """FastAPI dependency: `dependencies=[Depends(rate_limit("signoff"))]`."""
    rule = RULES[name]

    def dep(request: Request) -> None:
        enabled = limiter.enabled if limiter.enabled is not None else get_settings().rate_limits
        if not enabled:
            return
        keys = [f"ip:{client_ip(request)}"]
        if rule.per_user:
            session = unsign_value(request.cookies.get(SESSION_COOKIE), "session")
            if session:
                keys.append(f"user:{session.split('|', 1)[0]}")
        wait = limiter.hit(rule, keys)
        if wait is not None:
            seconds = max(1, math.ceil(wait))
            raise HTTPException(
                429,
                {"code": "rate_limited", "message": f"Too many attempts. Try again in {seconds} seconds."},
                headers={"Retry-After": str(seconds)},
            )

    return dep
