"""Small shared helpers: ids, time, text normalisation, embeddings, stats."""

from __future__ import annotations

import hashlib
import math
import re
import time
import unicodedata
import uuid

_ARABIC_DIACRITICS = re.compile(r"[ً-ْٰـ]")
_TOKEN = re.compile(r"\w+", re.UNICODE)


def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def normalize(text: str) -> str:
    """Lowercase, strip diacritics/tatweel, unify Arabic letter variants."""
    text = unicodedata.normalize("NFKC", str(text)).lower().strip()
    text = _ARABIC_DIACRITICS.sub("", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"\s+", " ", text)


_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ب", "ل", "ك", "ف")


def stem(tok: str) -> str:
    """Very light Arabic prefix stripping (no external stemmer)."""
    for p in _AR_PREFIXES:
        if tok.startswith(p) and len(tok) - len(p) >= 3:
            return tok[len(p):]
    return tok


STOPWORDS = {normalize(w) for w in (
    "ما ماذا ماهي هي هو من في على عن الى إلى هل كم اين أين متى كيف لماذا اي أي التي الذي هذا هذه "
    "اخبرني أخبرني قل لي عرف اذكر أعطني اعطني يا رجاء من فضلك "
    "what which who is are the a an of in on to for tell me please give name").split()}


def tokens(text: str, drop_stopwords: bool = False) -> list[str]:
    toks = [stem(t) for t in _TOKEN.findall(normalize(text))]
    return [t for t in toks if t not in STOPWORDS] if drop_stopwords else toks


def stable_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


EMBED_DIM = 256


def embed(text: str) -> list[float]:
    """Deterministic hashed bag of words + char trigrams (offline, no model).

    Replaceable through the Embedder interface by a neural embedder later.
    """
    vec = [0.0] * EMBED_DIM
    norm = normalize(text)
    feats = tokens(norm)
    padded = f"  {norm}  "
    feats += [padded[i:i + 3] for i in range(len(padded) - 2)]
    for f in feats:
        h = int(hashlib.md5(f.encode("utf-8")).hexdigest(), 16)
        vec[h % EMBED_DIM] += 1.0 if (h >> 8) & 1 else -1.0
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def diff_is_significant(succ_a: int, n_a: int, succ_b: int, n_b: int, z: float = 1.645) -> bool:
    """One-sided two-proportion z-test: is B better than A?"""
    if n_a == 0 or n_b == 0:
        return False
    pa, pb = succ_a / n_a, succ_b / n_b
    p = (succ_a + succ_b) / (n_a + n_b)
    se = math.sqrt(max(p * (1 - p) * (1 / n_a + 1 / n_b), 1e-12))
    return (pb - pa) / se > z


def answers_match(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return False
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    try:
        return abs(float(na) - float(nb)) < 1e-6
    except ValueError:
        return False
