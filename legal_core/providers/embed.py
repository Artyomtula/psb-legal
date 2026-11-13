from __future__ import annotations

import asyncio
import sys
import threading
from typing import Protocol

import numpy as np

from ..config import Settings
from .http import post_json


class Embedder(Protocol):
    name: str

    async def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalize(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class ApiEmbedder:
    def __init__(self, s: Settings, batch: int = 32) -> None:
        self.s = s
        self.batch = batch
        self.name = f"api:{s.emb_model}"

    async def _one(self, texts: list[str]) -> np.ndarray:
        data = await post_json(self.s.emb_base_url, "/embeddings", self.s.emb_api_key, {"model": self.s.emb_model, "input": texts, "encoding_format": "float"}, self.s.request_timeout)
        items = sorted(data["data"], key=lambda it: int(it.get("index", 0)))
        if len(items) != len(texts):
            raise ValueError("embeddings: count mismatch")
        return np.asarray([it["embedding"] for it in items], dtype=np.float32)

    async def embed(self, texts: list[str]) -> np.ndarray:
        parts = [texts[i:i + self.batch] for i in range(0, len(texts), self.batch)]
        sem = asyncio.Semaphore(4)

        async def run(p: list[str]) -> np.ndarray:
            async with sem:
                return await self._one(p)

        mats = await asyncio.gather(*(run(p) for p in parts))
        return _normalize(np.vstack(mats)) if mats else np.zeros((0, 1024), dtype=np.float32)


def resolve_device(pref: str = "auto") -> str:
    if pref and pref != "auto":
        return pref
    try:
        import torch
    except Exception:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class LocalModel:
    _models: dict[tuple[str, str, str], object] = {}
    _locks: dict[tuple[str, str, str], threading.Lock] = {}

    def __init__(self, model: str, device: str = "auto") -> None:
        self.model_name = model
        self.device = resolve_device(device)
        self.name = f"local:{model}"
        self._key = (type(self).__name__, model, self.device)
        self._lock = self._locks.setdefault(self._key, threading.Lock())

    def _load(self):
        raise NotImplementedError

    def _model(self):
        if self._key not in self._models:
            self._models[self._key] = self._load()
        return self._models[self._key]

    def _model_kwargs(self) -> dict:
        if self.device == "cpu":
            return {}
        import torch

        return {"dtype": torch.float16}

    def _release(self) -> None:
        if self.device == "mps":
            import torch

            torch.mps.empty_cache()


class LocalEmbedder(LocalModel):
    def _load(self):
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.model_name, device=self.device, model_kwargs=self._model_kwargs())

    def _encode(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            vecs = self._model().encode(texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
            self._release()
        return np.asarray(vecs, dtype=np.float32)

    async def embed(self, texts: list[str]) -> np.ndarray:
        return await asyncio.to_thread(self._encode, texts)


def local_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return False
    return True


def make_embedder(s: Settings) -> Embedder | None:
    mode = s.emb_provider
    if mode == "off":
        return None
    if mode == "local":
        return LocalEmbedder(s.local_emb_model, s.local_device)
    if mode == "api":
        return ApiEmbedder(s)
    if s.emb_base_url and s.emb_api_key and "deepseek.com" not in s.emb_base_url:
        return ApiEmbedder(s)
    if local_available():
        return LocalEmbedder(s.local_emb_model, s.local_device)
    print("Эмбеддер не настроен: используется только лексический поиск", file=sys.stderr)
    return None
