from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from typing import Protocol

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class EmbeddingProvider(Protocol):
    dimension: int
    model_name: str
    external: bool

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    """Stable local embedding for tests and retrieval plumbing; not a scientific model."""

    dimension = 768
    model_name = "local-hash-embedding-v1"
    external = False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        values = [0.0] * self.dimension
        for token in _TOKEN.findall(text.casefold()):
            digest = hashlib.blake2b(token.encode(), digest_size=16).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            values[index] += sign
        norm = math.sqrt(sum(value * value for value in values))
        return [value / norm for value in values] if norm else values


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions must match")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
