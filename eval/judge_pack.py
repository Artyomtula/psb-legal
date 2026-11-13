from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.judge import JUDGE_SYSTEM, KEYS, write_judgement  # noqa: E402

CACHE = ROOT / "runs" / "judge_cache.jsonl"
JUDGE_NAME = "claude-blind"
FIELDS = (*KEYS, "critical", "comment")


def item_key(row: dict) -> str:
    raw = "\x00".join((row["question"], row.get("reference") or "", row.get("answer") or ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def load_rows(run_dir: Path) -> list[dict]:
    path = run_dir / "answers.jsonl"
    if not path.exists():
        path = run_dir / "answers.partial.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    out = {}
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["key"]] = r
    return out


def pack(run_dirs: list[Path], out_dir: Path, batch_size: int, seed: int) -> None:
    cache = load_cache()
    items: dict[str, dict] = {}
    for d in run_dirs:
        for row in load_rows(d):
            k = item_key(row)
            if k not in cache and k not in items:
                items[k] = {"key": k, "question": row["question"], "reference": row.get("reference") or "(эталона нет)", "answer": row.get("answer") or "(пусто)"}
    todo = list(items.values())
    random.Random(seed).shuffle(todo)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rubric.md").write_text(JUDGE_SYSTEM + "\n\nПоле vs_baseline не заполняй: базового ответа нет.\n", encoding="utf-8")
    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    for n, batch in enumerate(batches, 1):
        with (out_dir / f"batch_{n}.jsonl").open("w", encoding="utf-8") as f:
            for it in batch:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
    print(json.dumps({"new_items": len(todo), "batches": len(batches), "dir": str(out_dir)}, ensure_ascii=False))


def _valid(r: dict) -> bool:
    return all(isinstance(r.get(k), (int, float)) for k in KEYS)


def ingest(results: list[Path], run_dirs: list[Path]) -> None:
    cache = load_cache()
    added = 0
    with CACHE.open("a", encoding="utf-8") as f:
        for path in results:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("key") in cache or not _valid(r):
                    continue
                rec = {"key": r["key"], **{k: r.get(k) for k in FIELDS}}
                cache[rec["key"]] = rec
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                added += 1
    print(f"оценок добавлено: {added}, всего в кэше: {len(cache)}")
    for d in run_dirs:
        scores = []
        for row in load_rows(d):
            j = cache.get(item_key(row), {})
            scores.append({"id": row["id"], "category": row.get("category"), "status": row.get("status"), **{k: j.get(k) for k in FIELDS}, "vs_baseline": "na"})
        print(f"== {d.name}")
        write_judgement(d, scores, JUDGE_NAME)


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("pack")
    a.add_argument("runs", nargs="+")
    a.add_argument("--out", required=True)
    a.add_argument("--batch", type=int, default=20)
    a.add_argument("--seed", type=int, default=7)
    b = sub.add_parser("ingest")
    b.add_argument("--results", nargs="+", required=True)
    b.add_argument("--runs", nargs="*", default=[])
    args = p.parse_args()
    if args.cmd == "pack":
        pack([Path(r) for r in args.runs], Path(args.out), args.batch, args.seed)
    else:
        ingest([Path(r) for r in args.results], [Path(r) for r in args.runs])


if __name__ == "__main__":
    main()
