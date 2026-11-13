from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(run: Path) -> list[dict]:
    path = run / "answers.jsonl"
    if not path.exists():
        path = run / "answers.partial.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("run")
    p.add_argument("--ids")
    p.add_argument("--max-overall", type=float)
    p.add_argument("--timings", action="store_true")
    a = p.parse_args()
    run = Path(a.run)
    only = set(a.ids.split(",")) if a.ids else None
    judge = {}
    if (run / "judge.jsonl").exists():
        judge = {r["id"]: r for r in map(json.loads, (run / "judge.jsonl").read_text(encoding="utf-8").splitlines())}
    for r in load(run):
        j = judge.get(r["id"], {})
        if only and r["id"] not in only:
            continue
        if a.max_overall is not None and not (isinstance(j.get("overall"), (int, float)) and j["overall"] <= a.max_overall):
            continue
        steps = (r.get("trace") or {}).get("trace") or []
        cand = next((s.get("candidates") for s in steps if s.get("step") == "retrieve"), []) or []
        gold = set(r.get("gold") or [])
        m = r.get("metrics") or {}
        print(f"=== {r['id']} expect={r.get('expect')} status={r['status']} gold_cand={len(gold & set(cand))}/{len(gold)} gold_sel={m.get('gold_in_context')} cited={m.get('gold_cited')} {r['seconds']}s overall={j.get('overall')}")
        if a.timings:
            print("T:", [(s["step"], s.get("seconds")) for s in steps])
        print("Q:", r["question"][:500].replace("\n", " / "))
        print("A:", r["answer"])
        print("R:", r.get("reference"))
        if j.get("comment"):
            print("J:", j.get("comment"), j.get("critical") or "")
        print()


if __name__ == "__main__":
    main()
