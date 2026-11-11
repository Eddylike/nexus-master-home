"""Kleine Vektor-Helfer ohne Zusatzabhaengigkeiten (Fallback fuer Embeddings)."""
from __future__ import annotations

import hashlib
import json
import math
import re

_TOKEN = re.compile(r"[a-z0-9äöüß_]+", re.IGNORECASE)


def normalize(vec):
    norm = math.sqrt(sum(x * x for x in vec))
    if not norm:
        return [0.0 for _ in vec]
    return [x / norm for x in vec]


def hashed_embedding(text: str, dim: int = 64):
    """Deterministisches Bag-of-Words-Embedding, wenn kein Modell erreichbar ist."""
    vec = [0.0] * dim
    for token in _TOKEN.findall((text or "").lower()):
        digest = hashlib.md5(token.encode("utf-8")).digest()
        vec[int.from_bytes(digest[:4], "big") % dim] += 1.0
    return normalize(vec)


def cosine(a, b) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    if not na or not nb:
        return 0.0
    return dot / (na * nb)


def dump_embedding(vec) -> str:
    return json.dumps([float(x) for x in vec])


def parse_embedding(raw):
    if not raw:
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return [float(x) for x in data]
    except (TypeError, ValueError):
        return []
