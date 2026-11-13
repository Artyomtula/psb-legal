from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legal_core.config import load_env_file  # noqa: E402

load_env_file(ROOT / ".env.dev")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", nargs="+", default=["eval/datasets/demo.jsonl"])
    p.add_argument("--split")
    a = p.parse_args()
    rows = []
    for ds in a.dataset:
        for line in (ROOT / ds).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if not a.split or r.get("split") == a.split:
                    rows.append(r)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "data").mkdir()
        shutil.copy(ROOT / "data/kb.zip", work / "data/kb.zip")
        with (work / "data/questions.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, quoting=csv.QUOTE_ALL)
            w.writerow(["id", "question"])
            for r in rows:
                w.writerow([r["id"], r["question"]])
        env = dict(os.environ)
        key_file = env.get("LLM_API_KEY_FILE")
        if key_file and not env.get("LLM_API_KEY"):
            env["LLM_API_KEY"] = Path(key_file).read_text(encoding="utf-8").strip()
        env.setdefault("MAX_WORKERS", "4")
        t = time.monotonic()
        subprocess.run([sys.executable, str(ROOT / "baseline/solution.py")], cwd=work, env=env, check=True)
        answers = {r["id"]: r["answer"] for r in csv.DictReader((work / "submission.csv").open(encoding="utf-8"))}
    out = ROOT / "runs" / f"{time.strftime('%Y%m%d-%H%M%S')}_baseline"
    out.mkdir(parents=True)
    with (out / "answers.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({**r, "answer": answers.get(r["id"], ""), "status": "baseline", "metrics": {}, "seconds": 0}, ensure_ascii=False) + "\n")
    print(f"→ {out} ({len(rows)} ответов, {time.monotonic() - t:.0f}с)")


if __name__ == "__main__":
    main()
