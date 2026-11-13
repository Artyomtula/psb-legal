from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

from ..kb.loader import RawDoc, kb_fingerprint, load_kb
from ..kb.models import Document, doc_from_dict, doc_to_dict
from ..kb.parser import parse_documents
from ..providers.embed import Embedder
from ..text import tokenize
from .bm25 import BM25
from .chunks import Chunk, build_chunks

INDEX_VERSION = 3


PROBE_CHUNKS = 3
MIN_PROBE_SIMILARITY = 0.95
KNOWN_EMB = ("bge-m3", "multilingual-e5-large", "multilingual-e5-base", "e5-mistral", "frida", "giga-embeddings")


async def vectors_match(idx: "KnowledgeIndex", embedder: Embedder) -> bool:
    step = max(1, len(idx.chunks) // PROBE_CHUNKS)
    picks = list(range(0, len(idx.chunks), step))[:PROBE_CHUNKS]
    try:
        vecs = await embedder.embed([idx.chunks[i].index_text for i in picks])
    except Exception:
        return True
    if vecs.shape[1:] != idx.emb.shape[1:]:
        return False
    return min(float(vecs[k] @ idx.emb[i]) for k, i in enumerate(picks)) >= MIN_PROBE_SIMILARITY


def emb_family(name: str) -> str:
    low = name.lower()
    for key in KNOWN_EMB:
        if key in low:
            return key
    for prefix in ("api:", "local:"):
        if low.startswith(prefix):
            low = low[len(prefix):]
    return low.rstrip("/").split("/")[-1]


class KnowledgeIndex:
    def __init__(self, docs: list[Document], chunks: list[Chunk], tokens: list[list[str]], emb: np.ndarray | None, emb_name: str, fingerprint: str) -> None:
        self.docs = docs
        self.by_id = {d.doc_id: d for d in docs}
        self.chunks = chunks
        self.cid_pos = {c.cid: i for i, c in enumerate(chunks)}
        self.tokens = tokens
        self.bm25 = BM25(tokens)
        self.emb = emb
        self.emb_name = emb_name
        self.fingerprint = fingerprint
        self.unit_chunks: dict[tuple[str, str], list[int]] = {}
        for i, c in enumerate(chunks):
            self.unit_chunks.setdefault((c.doc_id, c.anchor), []).append(i)
        self.doc_chunks: dict[str, list[int]] = {}
        for i, c in enumerate(chunks):
            self.doc_chunks.setdefault(c.doc_id, []).append(i)

    @property
    def dense_ready(self) -> bool:
        return self.emb is not None and len(self.emb) == len(self.chunks)

    @classmethod
    async def build(cls, raws: list[RawDoc], embedder: Embedder | None) -> "KnowledgeIndex":
        t = time.monotonic()
        docs = parse_documents(raws)
        chunks = build_chunks(docs)
        tokens = [tokenize(c.index_text) for c in chunks]
        emb, name = None, ""
        if embedder is not None:
            try:
                emb = await embedder.embed([c.index_text for c in chunks])
                name = embedder.name
            except Exception as exc:
                print(f"Эмбеддинги базы не построены ({type(exc).__name__}: {str(exc)[:160]}); только BM25", file=sys.stderr)
                emb = None
        idx = cls(docs, chunks, tokens, emb, name, kb_fingerprint(raws))
        print(f"Индекс: документов {len(docs)}, фрагментов {len(chunks)}, векторы: {'да' if idx.dense_ready else 'нет'} ({time.monotonic() - t:.1f}с)", file=sys.stderr)
        return idx

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        meta = {"version": INDEX_VERSION, "fingerprint": self.fingerprint, "emb_name": self.emb_name, "chunks": len(self.chunks)}
        (path / "docs.json").write_text(json.dumps([doc_to_dict(d) for d in self.docs], ensure_ascii=False), encoding="utf-8")
        (path / "chunks.json").write_text(json.dumps([c.__dict__ for c in self.chunks], ensure_ascii=False), encoding="utf-8")
        (path / "tokens.json").write_text(json.dumps(self.tokens, ensure_ascii=False), encoding="utf-8")
        if self.dense_ready:
            np.save(path / "emb.npy", self.emb.astype(np.float16))
        elif (path / "emb.npy").exists():
            (path / "emb.npy").unlink()
        (path / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "KnowledgeIndex":
        meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
        if meta.get("version") != INDEX_VERSION:
            raise ValueError("index version mismatch")
        docs = [doc_from_dict(d) for d in json.loads((path / "docs.json").read_text(encoding="utf-8"))]
        chunks = [Chunk(**c) for c in json.loads((path / "chunks.json").read_text(encoding="utf-8"))]
        tokens = json.loads((path / "tokens.json").read_text(encoding="utf-8"))
        emb_path = path / "emb.npy"
        emb = np.load(emb_path).astype(np.float32) if emb_path.exists() else None
        return cls(docs, chunks, tokens, emb, meta.get("emb_name", ""), meta["fingerprint"])

    @classmethod
    async def open(cls, kb_path: Path, cache_dirs: list[Path], embedder: Embedder | None, save_dir: Path | None = None) -> "KnowledgeIndex":
        raws = load_kb(kb_path)
        fp = kb_fingerprint(raws)
        for cache in cache_dirs:
            meta_path = cache / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("fingerprint") != fp or meta.get("version") != INDEX_VERSION:
                    continue
                idx = cls.load(cache)
            except Exception as exc:
                print(f"Кэш индекса {cache} не загружен: {type(exc).__name__}", file=sys.stderr)
                continue
            if idx.dense_ready and embedder is not None and emb_family(idx.emb_name) != emb_family(embedder.name):
                print(f"Модель эмбеддингов индекса ({idx.emb_name}) не совпадает с {embedder.name}: пересчёт", file=sys.stderr)
                break
            if not idx.dense_ready and embedder is not None:
                break
            if idx.dense_ready and embedder is not None and not await vectors_match(idx, embedder):
                print("Векторы индекса не согласуются с эмбеддером: пересчёт", file=sys.stderr)
                break
            print(f"Индекс загружен из кэша {cache} ({len(idx.chunks)} фрагментов)", file=sys.stderr)
            return idx
        idx = await cls.build(raws, embedder)
        if save_dir is not None:
            try:
                idx.save(save_dir)
            except OSError as exc:
                print(f"Индекс не сохранён: {exc}", file=sys.stderr)
        return idx
