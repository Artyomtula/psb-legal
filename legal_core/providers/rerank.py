from __future__ import annotations

import asyncio
from typing import Protocol

from ..config import Settings
from .embed import LocalModel, local_available
from .http import post_json


class Reranker(Protocol):
    name: str

    async def rerank(self, query: str, docs: list[str]) -> list[float]: ...


class ApiReranker:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self.name = f"api:{s.rerank_model}"

    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        data = await post_json(
            self.s.rerank_base_url,
            "/rerank",
            self.s.rerank_api_key,
            {"model": self.s.rerank_model, "query": query, "documents": docs, "top_n": len(docs)},
            self.s.request_timeout,
            attempts=3,
        )
        items = data.get("results", data.get("data", []))
        scores = [float("-inf")] * len(docs)
        for rank, it in enumerate(items):
            idx = int(it["index"])
            if 0 <= idx < len(docs):
                score = it.get("relevance_score", it.get("score"))
                scores[idx] = float(score) if score is not None else -float(rank)
        return scores


class LocalReranker(LocalModel):
    def _load(self):
        from sentence_transformers import CrossEncoder

        return CrossEncoder(self.model_name, device=self.device, max_length=1024, model_kwargs=self._model_kwargs())

    def _score(self, query: str, docs: list[str]) -> list[float]:
        with self._lock:
            scores = self._model().predict([(query, d) for d in docs], batch_size=8, show_progress_bar=False)
            self._release()
        return [float(x) for x in scores]

    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        return await asyncio.to_thread(self._score, query, docs)


def make_reranker(s: Settings) -> Reranker | None:
    mode = s.rerank_provider
    if mode == "off":
        return None
    if mode == "local":
        return LocalReranker(s.local_rerank_model, s.local_device)
    if mode == "api":
        return ApiReranker(s)
    if s.rerank_base_url and s.rerank_api_key and "deepseek.com" not in s.rerank_base_url:
        return ApiReranker(s)
    if local_available():
        return LocalReranker(s.local_rerank_model, s.local_device)
    return None
