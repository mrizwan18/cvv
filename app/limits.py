"""In-memory abuse guards for a single-process public deployment:
a per-IP sliding-window rate limit and a global daily budget for Claude calls."""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import date

from fastapi import HTTPException, Request

from . import config

_lock = threading.Lock()
_hits: dict[str, deque] = {}
_llm_day, _llm_used = date.today(), 0


def client_ip(request: Request) -> str:
    # Render sits behind a proxy: prefer the header it sets, then the last
    # X-Forwarded-For hop (the one the proxy appended, not client-supplied).
    ip = request.headers.get("true-client-ip")
    if not ip:
        xff = request.headers.get("x-forwarded-for", "")
        ip = xff.split(",")[-1].strip() if xff else ""
    return ip or (request.client.host if request.client else "unknown")


def rate_limit(request: Request) -> None:
    if config.RATE_LIMIT_PER_MIN <= 0:
        return
    ip, now = client_ip(request), time.monotonic()
    with _lock:
        q = _hits.setdefault(ip, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= config.RATE_LIMIT_PER_MIN:
            raise HTTPException(429, "Too many requests. Wait a minute and try again.",
                                headers={"Retry-After": str(int(60 - (now - q[0])) + 1)})
        q.append(now)
        if len(_hits) > 10000:  # drop idle clients so memory stays bounded
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > 60]:
                del _hits[k]


def take_llm(n: int = 1) -> bool:
    """Reserve n Claude calls from today's budget; False when it's spent."""
    global _llm_day, _llm_used
    if config.LLM_DAILY_LIMIT <= 0:
        return True
    with _lock:
        if date.today() != _llm_day:
            _llm_day, _llm_used = date.today(), 0
        if _llm_used + n > config.LLM_DAILY_LIMIT:
            return False
        _llm_used += n
        return True


def llm_left() -> int | None:
    if config.LLM_DAILY_LIMIT <= 0:
        return None
    with _lock:
        return config.LLM_DAILY_LIMIT - (_llm_used if date.today() == _llm_day else 0)
