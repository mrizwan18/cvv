"""Thin Anthropic client wrapper that returns parsed JSON, or None when unavailable."""
from __future__ import annotations

import json
import logging
import re

from . import config, limits

log = logging.getLogger(__name__)
_client = None


def available() -> bool:
    return bool(config.ANTHROPIC_API_KEY)


def _get():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=90, max_retries=2)
    return _client


def ask_json(prompt: str, max_tokens: int = 4000):
    if not available():
        return None
    if not limits.take_llm():
        log.warning("Daily Claude budget spent; using rules")
        return None
    try:
        msg = _get().messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=0)
        return json.loads(text[start:])
    except Exception as e:  # network, auth, bad JSON: fall back to rules
        log.warning("LLM call failed: %s", e)
        return None
