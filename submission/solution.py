from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

FALLBACK = "Не удалось сформировать ответ по базе знаний. Пожалуйста, переформулируйте вопрос."
DEFAULT_TIME_BUDGET = "1200"
ECONOMY_AFTER = 0.5


def find_file(data_dir: Path, *names: str) -> Path:
    for root in (data_dir, HERE / data_dir, Path.cwd() / data_dir, Path.cwd(), HERE):
        for name in names:
            p = root / name
            if p.is_file():
                return p
    raise FileNotFoundError(f"Не найден ни один из файлов: {', '.join(names)}")


def load_questions(path: Path) -> list[tuple[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or not {"id", "question"}.issubset(reader.fieldnames):
            raise ValueError("В questions.csv нужны колонки id и question")
        return [(str(r["id"]).strip(), str(r["question"] or "").strip()) for r in reader]


def write_submission(path: Path, rows: list[tuple[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        w.writerow(["id", "answer"])
        for qid, ans in rows:
            w.writerow([qid, " ".join((ans or FALLBACK).split())])


async def run() -> None:
    from legal_core.config import Settings
    from legal_core.dialog import parse_dialog
    from legal_core.pipeline.engine import build_assistant
    from legal_core.providers.http import close_all

    s = Settings.from_env()
    questions = load_questions(find_file(s.data_dir, "questions.csv"))
    kb = find_file(s.data_dir, "kb.zip", "knowledge-base.zip", "knowledge_base.zip")
    out_path = Path(os.getenv("OUTPUT_PATH", "submission.csv"))
    answers = [FALLBACK] * len(questions)
    write_submission(out_path, list(zip([q for q, _ in questions], answers)))
    print(f"Вопросов: {len(questions)}; база: {kb.name}; модель: {s.llm_model}; потоков: {s.max_workers}", flush=True)
    cache_dirs = [HERE / "artifacts" / "index", s.index_dir]
    writable = Path(tempfile.gettempdir()) / "legal_index"
    assistant = await build_assistant(s, kb, cache_dirs, save_dir=writable)
    sem = asyncio.Semaphore(s.max_workers)
    traces: list[dict] = []
    done = 0
    time_budget = float(os.getenv("TIME_BUDGET", DEFAULT_TIME_BUDGET))
    t0 = time.monotonic()

    async def work(i: int, qid: str, text: str) -> None:
        nonlocal done
        async with sem:
            try:
                economy = time.monotonic() - t0 > time_budget * ECONOMY_AFTER
                res = await asyncio.wait_for(assistant.answer(parse_dialog(text), mode="submission", economy=economy), timeout=s.question_budget + 60)
                answers[i] = res.text or FALLBACK
                traces.append({"id": qid, **res.trace_dict()})
            except Exception as exc:
                print(f"Вопрос {qid}: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
                traces.append({"id": qid, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            done += 1
            if done % 5 == 0 or done == len(questions):
                print(f"Обработано {done}/{len(questions)} за {time.monotonic() - t0:.0f}с", flush=True)
                write_submission(out_path, list(zip([q for q, _ in questions], answers)))

    await asyncio.gather(*(work(i, qid, text) for i, (qid, text) in enumerate(questions)))
    write_submission(out_path, list(zip([q for q, _ in questions], answers)))
    trace_path = os.getenv("TRACE_PATH")
    if trace_path:
        Path(trace_path).write_text(json.dumps({"traces": traces, "usage": assistant.llm.usage.__dict__}, ensure_ascii=False, indent=1), encoding="utf-8")
    u = assistant.llm.usage
    print(f"Готово: {out_path} ({len(questions)} ответов) за {time.monotonic() - t0:.0f}с; LLM-вызовов {u.calls}, токенов {u.prompt}+{u.completion}", flush=True)
    await close_all()


def main() -> None:
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"Критическая ошибка: {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            s_dir = Path(os.getenv("DATA_DIR", "data"))
            qs = load_questions(find_file(s_dir, "questions.csv"))
            out = Path(os.getenv("OUTPUT_PATH", "submission.csv"))
            if not out.exists():
                write_submission(out, [(q, FALLBACK) for q, _ in qs])
        except Exception as inner:
            print(f"Не удалось записать запасной submission.csv: {inner}", file=sys.stderr)


if __name__ == "__main__":
    main()
