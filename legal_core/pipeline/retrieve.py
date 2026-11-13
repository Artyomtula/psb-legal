from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import numpy as np

from ..index.store import KnowledgeIndex
from ..providers.embed import Embedder
from ..providers.rerank import Reranker
from ..text import tokenize

RRF_K = 60
SIGNATURE_RE = re.compile(r"(?:^(?:Поставщик|Покупатель|Арендатор|Арендодатель|Исполнитель|Заказчик)\s*:)|(?:(?:директор|руководитель|начальник)\w*\s.*[А-ЯЁ]\.\s?[А-ЯЁ]\.\s?[А-ЯЁ][а-яё-]+\.?$)", re.I)


@dataclass
class Hit:
    idx: int
    score: float
    channels: dict[str, int] = field(default_factory=dict)
    rerank: float | None = None
    origin: str = "search"


@dataclass
class Plan:
    queries: list[str]
    hyde: list[str] = field(default_factory=list)
    doc_boost: dict[str, float] = field(default_factory=dict)
    pinned: list[tuple[str, str]] = field(default_factory=list)
    pinned_docs: list[str] = field(default_factory=list)
    rerank_query: str = ""


@dataclass
class SearchCache:
    embeddings: dict[str, np.ndarray] = field(default_factory=dict)
    rerank: dict[int, float] = field(default_factory=dict)
    rerank_query: str = ""


def _top(scores: np.ndarray, k: int) -> list[int]:
    if len(scores) == 0:
        return []
    k = min(k, len(scores))
    part = np.argpartition(-scores, k - 1)[:k]
    order = part[np.argsort(-scores[part])]
    return [int(i) for i in order if scores[i] > 0]


class Retriever:
    def __init__(self, index: KnowledgeIndex, embedder: Embedder | None, reranker: Reranker | None) -> None:
        self.index = index
        self.embedder = embedder if index.dense_ready else None
        self.reranker = reranker
        self.dense_failed = False
        self.rerank_failed = False
        self.prior = [self._prior(c) for c in index.chunks]
        self.incoming: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for d in index.docs:
            for rel in d.relations:
                if rel.kind == "refers" and rel.source_anchor:
                    for a in rel.target_anchors:
                        self.incoming.setdefault((rel.target_doc, a), []).append((d.doc_id, rel.source_anchor))

    @staticmethod
    def _prior(chunk) -> float:
        if chunk.anchor in ("preamble", "header"):
            return 0.6
        if "реквизит" in chunk.header.lower() or chunk.anchor.startswith("s-"):
            return 0.6
        if chunk.anchor.startswith("abz-"):
            text = chunk.text.strip()
            if SIGNATURE_RE.search(text) or len(text) < 45:
                return 0.4
        return 1.0

    async def _dense(self, texts: list[str], cache: SearchCache | None) -> np.ndarray | None:
        if self.embedder is None or not texts:
            return None
        known = cache.embeddings if cache is not None else {}
        missing = [t for t in dict.fromkeys(texts) if t not in known]
        try:
            if missing:
                vecs = await self.embedder.embed(missing)
                known.update(zip(missing, vecs))
        except Exception as exc:
            self.dense_failed = True
            print(f"Векторный поиск недоступен ({type(exc).__name__}): только BM25", file=sys.stderr)
            return None
        return np.stack([known[t] for t in texts])

    async def search(self, plan: Plan, k_channel: int = 40, k_out: int = 40, cache: SearchCache | None = None) -> list[Hit]:
        idx = self.index
        fused: dict[int, Hit] = {}

        def add(ranking: list[int], channel: str, weight: float) -> None:
            for rank, i in enumerate(ranking):
                h = fused.get(i)
                if h is None:
                    h = fused[i] = Hit(idx=i, score=0.0)
                h.score += weight / (RRF_K + rank + 1)
                h.channels[channel] = min(h.channels.get(channel, rank), rank)

        queries = [q for q in dict.fromkeys(q.strip() for q in plan.queries) if q]
        for qi, q in enumerate(queries):
            add(_top(idx.bm25.scores(tokenize(q)), k_channel), f"bm25:{qi}", 1.0 if qi == 0 else 0.8)
        dense_texts = queries + [h for h in plan.hyde if h.strip()]
        vecs = await self._dense(dense_texts, cache)
        if vecs is not None and idx.emb is not None:
            sims = vecs @ idx.emb.T
            for qi in range(len(dense_texts)):
                is_hyde = qi >= len(queries)
                add(_top(sims[qi], k_channel), f"{'hyde' if is_hyde else 'dense'}:{qi}", 0.7 if is_hyde else (1.0 if qi == 0 else 0.8))
        for h in fused.values():
            h.score *= self.prior[h.idx]
            b = plan.doc_boost.get(idx.chunks[h.idx].doc_id) if plan.doc_boost else None
            if b:
                h.score *= 1.0 + b
        top_score = max((h.score for h in fused.values()), default=1.0)
        for doc_id, anchor in plan.pinned:
            for i in idx.unit_chunks.get((doc_id, anchor), []):
                h = fused.setdefault(i, Hit(idx=i, score=0.0))
                h.score += top_score * 1.5
                h.channels["pinned"] = 0
                h.origin = "pinned"
        hits = sorted(fused.values(), key=lambda h: -h.score)
        return hits[:k_out]

    async def rerank(self, query: str, hits: list[Hit], top_n: int = 40, cache: SearchCache | None = None) -> list[Hit]:
        if not hits or self.reranker is None or self.rerank_failed:
            return hits
        head, tail = hits[:top_n], hits[top_n:]
        known = cache.rerank if cache is not None and cache.rerank_query == query else {}
        todo = [h for h in head if h.idx not in known]
        try:
            fresh = await self.reranker.rerank(query, [self.index.chunks[h.idx].index_text[:2000] for h in todo]) if todo else []
        except Exception as exc:
            self.rerank_failed = True
            print(f"Реранкер недоступен ({type(exc).__name__}): порядок RRF", file=sys.stderr)
            return hits
        known.update((h.idx, score) for h, score in zip(todo, fresh))
        if cache is not None:
            cache.rerank, cache.rerank_query = known, query
        scores = [known[h.idx] for h in head]
        order = sorted(range(len(head)), key=lambda i: -scores[i])
        rr_rank = {head[i].idx: r for r, i in enumerate(order)}
        for pos, h in enumerate(head):
            h.rerank = scores[pos]
            pinned_bonus = 1.0 if h.origin == "pinned" else 0.0
            h.score = 1.0 / (RRF_K + pos + 1) + 2.0 / (RRF_K + rr_rank[h.idx] + 1) + pinned_bonus
        head.sort(key=lambda h: -h.score)
        return head + tail

    def expand(self, hits: list[Hit], limit: int = 14) -> list[Hit]:
        idx = self.index
        seen = {h.idx for h in hits[:limit]}
        out: list[Hit] = []
        extra: list[Hit] = []

        def add_unit(doc_id: str, anchor: str, origin: str, base: float) -> None:
            for i in idx.unit_chunks.get((doc_id, anchor), []):
                if i not in seen:
                    seen.add(i)
                    extra.append(Hit(idx=i, score=base, origin=origin))

        def add_doc(doc_id: str, origin: str, base: float) -> None:
            for i in idx.doc_chunks.get(doc_id, [])[:12]:
                if i not in seen and self.prior[i] >= 1.0:
                    seen.add(i)
                    extra.append(Hit(idx=i, score=base, origin=origin))

        for pos, h in enumerate(hits[:limit]):
            out.append(h)
            extra = []
            c = idx.chunks[h.idx]
            doc = idx.by_id.get(c.doc_id)
            if doc is None:
                continue
            base = h.score * 0.9
            if pos < 6 and doc.category == "company" and doc.doc_type in ("supplement", "letter", "memo") and len(idx.doc_chunks.get(doc.doc_id, [])) <= 10:
                add_doc(doc.doc_id, "same_doc", base * 0.95)
            refs: list[tuple[str, str]] = []
            for rel in doc.relations:
                if rel.kind in ("amended_by", "supplemented_by") and (not rel.target_anchors or c.anchor in rel.target_anchors):
                    add_doc(rel.target_doc, f"related:{rel.kind}", base)
                elif rel.kind in ("amends", "supplements"):
                    for a in rel.target_anchors:
                        add_unit(rel.target_doc, a, f"related:{rel.kind}", base)
                elif rel.kind in ("replaces", "replaced_by"):
                    add_unit(rel.target_doc, c.anchor, "edition", base * 0.95)
                elif rel.kind == "refers" and rel.source_anchor == c.anchor:
                    refs += [(rel.target_doc, a) for a in rel.target_anchors]
            if pos < 8:
                refs += self.incoming.get((c.doc_id, c.anchor), [])
                for doc_id, anchor in list(dict.fromkeys(refs))[:3]:
                    add_unit(doc_id, anchor, "reference", base * 0.85)
            out.extend(extra)
        out.extend(h for h in hits[limit:] if h.idx not in seen)
        return out
