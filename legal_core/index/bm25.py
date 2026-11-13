from __future__ import annotations

import math
from collections import Counter

import numpy as np


class BM25:
    def __init__(self, corpus: list[list[str]], k1: float = 1.4, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.n = len(corpus)
        self.lengths = np.array([len(t) for t in corpus], dtype=np.float32)
        self.avgdl = float(self.lengths.mean()) if self.n else 1.0
        postings: dict[str, tuple[list[int], list[float]]] = {}
        for i, tokens in enumerate(corpus):
            for term, tf in Counter(tokens).items():
                ids, tfs = postings.setdefault(term, ([], []))
                ids.append(i)
                tfs.append(float(tf))
        self.norm = (self.k1 * (1 - self.b + self.b * self.lengths / max(self.avgdl, 1e-6))).astype(np.float32)
        self.postings: dict[str, tuple[np.ndarray, np.ndarray, float]] = {}
        for term, (ids, tfs) in postings.items():
            df = len(ids)
            idf = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            self.postings[term] = (np.asarray(ids, dtype=np.int32), np.asarray(tfs, dtype=np.float32), idf)

    def scores(self, query: list[str]) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float32)
        for term, qtf in Counter(query).items():
            entry = self.postings.get(term)
            if entry is None:
                continue
            ids, tfs, idf = entry
            weight = idf * (1.0 + 0.5 * (qtf - 1))
            out[ids] += weight * tfs * (self.k1 + 1) / (tfs + self.norm[ids])
        return out
