from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    questions = {r["id"]: r["question"] for r in csv.DictReader((ROOT / "data/questions.csv").open(encoding="utf-8-sig"))}
    refs = {r["id"]: r["answer"] for r in csv.DictReader((ROOT / "data/demo_reference.csv").open(encoding="utf-8-sig"))}
    gold = json.loads((ROOT / "eval/datasets/demo_gold.json").read_text(encoding="utf-8"))
    out = ROOT / "eval/datasets/demo.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for qid, q in questions.items():
            g = gold.get(qid, {})
            row = {"id": f"demo-{qid}", "question": q, "reference": refs.get(qid, ""), "gold": g.get("gold", []), "expect": g.get("expect", "answer"), "category": g.get("category", []), "split": "demo"}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{out}: {len(questions)}")


if __name__ == "__main__":
    main()
