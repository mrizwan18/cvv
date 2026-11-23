"""Optional sentence embeddings (fastembed / ONNX, CPU). Falls back to a
bag-of-words cosine when the model can't load, so the API always works."""
from __future__ import annotations

import logging
import math
import re
from collections import Counter

from . import config

log = logging.getLogger(__name__)
_model = None
_failed = False


def _load():
    global _model, _failed
    if _model is None and not _failed and config.EMBEDDINGS:
        try:
            from fastembed import TextEmbedding
            _model = TextEmbedding(config.EMBED_MODEL)
        except Exception as e:
            log.warning("Embeddings unavailable, using lexical similarity: %s", e)
            _failed = True
    return _model


def mode() -> str:
    return "embeddings" if _load() is not None else "lexical"


_STOP = set("a an the and or of to in for on with at by from as is are be we you our your will this that it its into across using use".split())


def _bow(t: str) -> Counter:
    toks = [w for w in re.findall(r"[a-z][a-z0-9+#.]*", t.lower()) if w not in _STOP and len(w) > 1]
    toks = [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in toks]
    return Counter(toks)


def _cos_bow(a: Counter, b: Counter) -> float:
    dot = sum(a[k] * b[k] for k in a if k in b)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def vectors(texts: list[str]):
    m = _load()
    if m is None:
        return [_bow(t) for t in texts]
    return [list(v) for v in m.embed(texts)]


def cosine(a, b) -> float:
    if isinstance(a, Counter):
        return _cos_bow(a, b)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def implied_threshold() -> float:
    # bag-of-words scores run much lower than dense-embedding scores
    return config.IMPLIED_THRESHOLD if mode() == "embeddings" else 0.45


def doc_score(sim: float) -> float:
    """Map raw document similarity to 0..1."""
    if mode() == "embeddings":
        return max(0.0, min(1.0, (sim - 0.55) / 0.33))
    return max(0.0, min(1.0, (sim - 0.05) / 0.30))
