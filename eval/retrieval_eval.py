from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legal_core.config import Settings, load_env_file  # noqa: E402
from legal_core.dialog import parse_dialog  # noqa: E402
from legal_core.index.store import KnowledgeIndex  # noqa: E402
from legal_core.pipeline.fragments import build_fragments  # noqa: E402
from legal_core.pipeline.retrieve import Plan, Retriever  # noqa: E402
from legal_core.providers.embed import make_embedder  # noqa: E402
from legal_core.providers.rerank import make_reranker  # noqa: E402

load_env_file(ROOT / ".env.dev")


def unit_ranking(index: KnowledgeIndex, hits) -> list[str]:
    out: list[str] = []
    for h in hits:
        c = index.chunks[h.idx]
        key = f"{c.doc_id}#{c.anchor}"
        if key not in out:
            out.append(key)
    return out


async def run(args) -> None:
    s = Settings.from_env()
    embedder = None if args.no_dense else make_embedder(s)
    reranker = make_reranker(s) if args.rerank else None
    index = await KnowledgeIndex.open(ROOT / "data/kb.zip", [s.index_dir], embedder, save_dir=s.index_dir)
    retriever = Retriever(index, embedder, reranker)
    rows = [json.loads(l) for l in (ROOT / args.dataset).read_text(encoding="utf-8").splitlines() if l.strip()]
    ks = (1, 3, 5, 10, 20)
    agg = {k: 0.0 for k in ks}
    mrr = 0.0
    ctx_recall = 0.0
    n = 0
    for row in rows:
        if not row["gold"]:
            continue
        turns = parse_dialog(row["question"])
        last = turns[-1].content
        queries = [last] if args.only_last else [last, " ".join(t.content for t in turns)]
        hits = await retriever.search(Plan(queries=queries), k_channel=40, k_out=60)
        if args.rerank:
            hits = await retriever.rerank(last, hits, top_n=40)
        ranking = unit_ranking(index, hits)
        gold = set(row["gold"])
        ctx = {f.key for f in build_fragments(index, retriever.expand(hits, limit=12), limit=24)}
        ctx_recall += len(gold & ctx) / len(gold)
        n += 1
        for k in ks:
            agg[k] += len(gold & set(ranking[:k])) / len(gold)
        first = next((r for r, u in enumerate(ranking) if u in gold), None)
        mrr += 1.0 / (first + 1) if first is not None else 0.0
        if args.verbose:
            ranks = {g: (ranking.index(g) + 1 if g in ranking else None) for g in gold}
            print(f"{row['id']:8s} ranks={ranks} top3={ranking[:3]}")
    print(" ".join(f"R@{k}={agg[k] / n:.3f}" for k in ks), f"MRR={mrr / n:.3f}", f"ctx@24={ctx_recall / n:.3f}", f"n={n}", f"dense={'on' if retriever.embedder else 'off'}", f"rerank={'on' if reranker else 'off'}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="eval/datasets/demo.jsonl")
    p.add_argument("--no-dense", action="store_true")
    p.add_argument("--rerank", action="store_true")
    p.add_argument("--only-last", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
