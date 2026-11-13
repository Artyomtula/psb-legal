from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(run: Path) -> tuple[dict, dict]:
    answers = {r["id"]: r for r in map(json.loads, (run / "answers.jsonl").read_text(encoding="utf-8").splitlines()) if r}
    judge_path = run / "judge.jsonl"
    judge = {r["id"]: r for r in map(json.loads, judge_path.read_text(encoding="utf-8").splitlines())} if judge_path.exists() else {}
    return answers, judge


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--all", action="store_true")
    a = p.parse_args()
    oa, oj = load(Path(a.old))
    na, nj = load(Path(a.new))
    better = worse = same = 0
    for qid in sorted(set(oa) & set(na), key=lambda x: (x.split("-")[0], int(x.split("-")[-1]) if x.split("-")[-1].isdigit() else 0)):
        o, n = oj.get(qid, {}).get("overall"), nj.get(qid, {}).get("overall")
        delta = (n - o) if isinstance(o, (int, float)) and isinstance(n, (int, float)) else None
        if delta is not None:
            better += delta > 0
            worse += delta < 0
            same += delta == 0
        changed = oa[qid].get("answer") != na[qid].get("answer")
        if a.all or (delta is not None and delta != 0) or oa[qid].get("status") != na[qid].get("status"):
            print(f"\n== {qid} [{', '.join(na[qid].get('category') or [])}] overall {o} → {n} | status {oa[qid].get('status')} → {na[qid].get('status')}{' (текст изменился)' if changed else ''}")
            print(f"  Q: {na[qid]['question'][:200]}")
            print(f"  REF: {na[qid].get('reference', '')[:300]}")
            print(f"  OLD: {oa[qid].get('answer', '')[:400]}")
            print(f"  NEW: {na[qid].get('answer', '')[:400]}")
            if nj.get(qid, {}).get("comment"):
                print(f"  JUDGE: {nj[qid]['comment']}")
    print(f"\nЛучше: {better}, хуже: {worse}, без изменений: {same}")


if __name__ == "__main__":
    main()
