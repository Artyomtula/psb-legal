from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legal_core.config import Settings, load_env_file  # noqa: E402
from legal_core.dialog import parse_dialog  # noqa: E402
from legal_core.pipeline.engine import build_assistant  # noqa: E402
from legal_core.providers.http import close_all  # noqa: E402
from eval import llm_cache  # noqa: E402

load_env_file(ROOT / ".env.dev")

REFUSAL = {"insufficient", "offtopic", "refusal_illegal"}
EXPECT_STATUS = {
    "answer": {"answer"},
    "refuse": {"insufficient"},
    "false_premise": {"false_premise"},
    "partial": {"partial"},
    "clarify": {"clarify"},
    "offtopic": {"offtopic"},
    "illegal": {"refusal_illegal"},
}


def load_rows(paths: list[str], ids: set[str] | None, split: str | None) -> list[dict]:
    rows = []
    for p in paths:
        for line in (ROOT / p).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if ids and row["id"] not in ids:
                continue
            if split and row.get("split") != split:
                continue
            rows.append(row)
    return rows


def git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "nogit"


def metrics(row: dict, res) -> dict:
    gold = set(row.get("gold") or [])
    ctx = {f.key for f in res.fragments}
    cited = {f"{c.doc_id}#{c.anchor}" for c in res.citations}
    expect = row.get("expect", "answer")
    refused = res.status in REFUSAL
    should_refuse = expect in ("refuse", "offtopic", "illegal")
    return {
        "gold_in_context": (len(gold & ctx) / len(gold)) if gold else None,
        "gold_cited": (len(gold & cited) / len(gold)) if gold else None,
        "any_gold_cited": (bool(gold & cited)) if gold else None,
        "refusal_correct": refused == should_refuse,
        "false_refusal": refused and not should_refuse,
        "missed_refusal": should_refuse and not refused,
        "status_match": res.status in EXPECT_STATUS.get(expect, {expect}),
        "n_citations": len(cited),
    }


async def run(args) -> Path:
    llm_cache.install()
    s = Settings.from_env()
    rows = load_rows(args.dataset, set(args.ids.split(",")) if args.ids else None, args.split)
    assistant = await build_assistant(s, ROOT / "data/kb.zip", [s.index_dir], save_dir=s.index_dir)
    out_dir = ROOT / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}_{git_sha()}_{args.tag}"
    out_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = [None] * len(rows)
    partial = (out_dir / "answers.partial.jsonl").open("a", encoding="utf-8")

    async def one(i: int, row: dict) -> None:
        async with sem:
            t = time.monotonic()
            try:
                res = await asyncio.wait_for(assistant.answer(parse_dialog(row["question"]), mode=args.mode), timeout=s.question_budget + 90)
                m = metrics(row, res)
                results[i] = {**row, "answer": res.text, "status": res.status, "metrics": m, "seconds": round(time.monotonic() - t, 2), "trace": res.trace_dict()}
            except Exception as exc:
                results[i] = {**row, "answer": "", "status": "error", "metrics": {}, "seconds": round(time.monotonic() - t, 2), "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
            partial.write(json.dumps(results[i], ensure_ascii=False) + "\n")
            partial.flush()
            print(f"[{sum(r is not None for r in results)}/{len(rows)}] {row['id']} {results[i]['status']} {results[i]['seconds']}s", flush=True)

    await asyncio.gather(*(one(i, r) for i, r in enumerate(rows)))
    partial.close()
    with (out_dir / "answers.jsonl").open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = summarize(results)
    summary["usage"] = {"calls": assistant.llm.usage.calls, "prompt": assistant.llm.usage.prompt, "completion": assistant.llm.usage.completion, "by_stage": assistant.llm.usage.by_stage, "cache": dict(llm_cache.stats)}
    summary["config"] = {"model": s.llm_model, "emb": assistant.retriever.embedder.name if assistant.retriever.embedder else None, "rerank": assistant.retriever.reranker.name if assistant.retriever.reranker else None, "verify": s.verify, "grade": s.grade, "profile": s.profile, "mode": args.mode, "tag": args.tag, "thinking": assistant.llm.thinking_modes}
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("by_category", "usage")}, ensure_ascii=False, indent=1))
    print(f"→ {out_dir}")
    await close_all()
    return out_dir


def _mean(vals: list) -> float | None:
    vals = [float(v) for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def summarize(results: list[dict]) -> dict:
    ms = [r.get("metrics") or {} for r in results]
    out = {
        "n": len(results),
        "errors": sum(r["status"] == "error" for r in results),
        "gold_in_context": _mean([m.get("gold_in_context") for m in ms]),
        "gold_cited": _mean([m.get("gold_cited") for m in ms]),
        "refusal_accuracy": _mean([m.get("refusal_correct") for m in ms if m]),
        "false_refusals": sum(bool(m.get("false_refusal")) for m in ms),
        "missed_refusals": sum(bool(m.get("missed_refusal")) for m in ms),
        "status_match": _mean([m.get("status_match") for m in ms if m]),
        "latency_mean": _mean([r["seconds"] for r in results]),
        "latency_p95": sorted(r["seconds"] for r in results)[max(0, int(len(results) * 0.95) - 1)] if results else None,
    }
    cats: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        for c in r.get("category") or ["other"]:
            cats[c].append(r)
    out["by_category"] = {c: {"n": len(rs), "gold_cited": _mean([(r.get("metrics") or {}).get("gold_cited") for r in rs]), "refusal_accuracy": _mean([(r.get("metrics") or {}).get("refusal_correct") for r in rs])} for c, rs in sorted(cats.items())}
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", nargs="+", default=["eval/datasets/demo.jsonl"])
    p.add_argument("--ids")
    p.add_argument("--split")
    p.add_argument("--tag", default="run")
    p.add_argument("--mode", default="submission", choices=["submission", "ui"])
    p.add_argument("--concurrency", type=int, default=4)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
