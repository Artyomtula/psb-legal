from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np

from legal_core.index.store import vectors_match


class FakeEmbedder:
    name = "api:baai/bge-m3"

    def __init__(self, vectors: np.ndarray | None) -> None:
        self.vectors = vectors

    async def embed(self, texts: list[str]) -> np.ndarray:
        if self.vectors is None:
            raise RuntimeError("embeddings are down")
        return self.vectors[: len(texts)]


def _index(emb: np.ndarray) -> SimpleNamespace:
    return SimpleNamespace(chunks=[SimpleNamespace(index_text=f"фрагмент {i}") for i in range(len(emb))], emb=emb)


def test_cached_vectors_are_checked_against_the_embedder():
    emb = np.eye(3, dtype=np.float32)
    idx = _index(emb)
    assert asyncio.run(vectors_match(idx, FakeEmbedder(emb * 0.999)))
    assert not asyncio.run(vectors_match(idx, FakeEmbedder(np.roll(emb, 1, axis=1))))
    assert not asyncio.run(vectors_match(idx, FakeEmbedder(np.ones((3, 5), dtype=np.float32))))
    assert asyncio.run(vectors_match(idx, FakeEmbedder(None)))
