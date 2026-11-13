from __future__ import annotations

import csv
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.usefixtures("kb_path")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_mock(env_extra: dict) -> tuple[subprocess.Popen, int]:
    port = _free_port()
    env = {**os.environ, **env_extra}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "mock_platform:app", "--port", str(port), "--log-level", "warning"], cwd=ROOT / "tests", env=env)
    for _ in range(60):
        try:
            httpx.post(f"http://127.0.0.1:{port}/rerank", json={"documents": []}, timeout=1)
            return proc, port
        except Exception:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("mock did not start")


def _run_solution(tmp: Path, port: int, extra: dict | None = None) -> list[dict]:
    work = tmp / "work"
    (work / "data").mkdir(parents=True)
    shutil.copy(ROOT / "data/kb.zip", work / "data/kb.zip")
    shutil.copy(ROOT / "data/questions.csv", work / "data/questions.csv")
    sol = tmp / "sol"
    shutil.copytree(ROOT / "legal_core", sol / "legal_core", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "submission/solution.py", sol / "solution.py")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "EMB", "RERANK", "OPENAI"))}
    env.update({"DATA_DIR": "data", "LLM_BASE_URL": f"http://127.0.0.1:{port}", "LLM_API_KEY": "test", "EMB_PROVIDER": "auto", "RERANK_PROVIDER": "auto", "MAX_WORKERS": "4", "INDEX_DIR": str(tmp / "idx"), "QUESTION_BUDGET": "60", "REQUEST_TIMEOUT": "10", **(extra or {})})
    proc = subprocess.run([sys.executable, str(sol / "solution.py")], cwd=work, env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    with (work / "submission.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    raw = (work / "submission.csv").read_text(encoding="utf-8").splitlines()
    assert raw[0] == '"id","answer"'
    assert len(raw) == len(rows) + 1
    return rows


@pytest.mark.slow
def test_solution_happy_path(tmp_path):
    proc, port = _start_mock({})
    try:
        rows = _run_solution(tmp_path, port)
    finally:
        proc.kill()
    assert len(rows) == 18
    assert all(r["answer"] and "\n" not in r["answer"] for r in rows)
    assert sum("(" in r["answer"] for r in rows) >= 15


@pytest.mark.slow
def test_solution_survives_aux_failures(tmp_path):
    proc, port = _start_mock({"MOCK_FAIL_AUX": "1", "MOCK_FAIL_LLM_EVERY": "7"})
    try:
        rows = _run_solution(tmp_path, port)
    finally:
        proc.kill()
    assert len(rows) == 18
    assert all(r["answer"].strip() for r in rows)
