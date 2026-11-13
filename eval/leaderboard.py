from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLS = ("score", "correctness", "grounding", "reliability", "completeness", "clarity", "critical_count")


def row(run: Path, ids: set[str] | None, judge_name: str = "judge") -> dict | None:
    summary = run / "summary.json"
    judge = run / f"{judge_name}.jsonl"
    if not summary.exists():
        return None
    s = json.loads(summary.read_text(encoding="utf-8"))
    out = {"run": run.name, "n": s.get("n"), "status": s.get("status_match"), "cited": s.get("gold_cited"), "lat": s.get("latency_mean")}
    if judge.exists():
        js = [json.loads(l) for l in judge.read_text(encoding="utf-8").splitlines() if l.strip()]
        if ids:
            js = [j for j in js if j["id"] in ids]
        nums = lambda k: [j[k] for j in js if isinstance(j.get(k), (int, float))]
        ov = nums("overall")
        out["score"] = round(sum(ov) / len(ov) / 10, 3) if ov else None
        for k in ("correctness", "grounding", "reliability", "completeness", "clarity"):
            v = nums(k)
            out[k] = round(sum(v) / len(v), 2) if v else None
        out["critical_count"] = sum(1 for j in js if j.get("critical"))
        out["judged"] = len(ov)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("runs", nargs="*")
    p.add_argument("--ids", help="файл или список id через запятую, чтобы сравнивать на одном наборе")
    p.add_argument("--judge", default="judge", help="имя файла оценок: judge (Claude) или judge_ds (DeepSeek)")
    a = p.parse_args()
    ids = None
    if a.ids:
        raw = Path(a.ids).read_text(encoding="utf-8") if Path(a.ids).exists() else a.ids
        ids = {x.strip() for x in raw.split(",") if x.strip()}
    runs = [Path(r) for r in a.runs] or sorted(d for d in (ROOT / "runs").iterdir() if d.is_dir() and (d / "summary.json").exists())
    rows = [r for r in (row(d, ids, a.judge) for d in runs) if r]
    head = ["run", "n", "judged", *COLS, "status", "cited", "lat"]
    print(" | ".join(head))
    for r in rows:
        print(" | ".join(str(r.get(h, "")) for h in head))


if __name__ == "__main__":
    main()
