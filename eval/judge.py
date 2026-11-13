from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from legal_core.config import Settings, load_env_file  # noqa: E402
from legal_core.providers.http import close_all  # noqa: E402
from legal_core.providers.llm import LLM  # noqa: E402
from eval import llm_cache  # noqa: E402

load_env_file(ROOT / ".env.dev")

JUDGE_SYSTEM = """Ты — строгий эксперт-юрист, оценивающий ответы ИИ-ассистента для юристов банка. Ассистент обязан отвечать только по базе знаний (законы и документы организации), ссылаться на конкретные положения и обоснованно отказываться, если данных нет.
Тебе даны: вопрос (может содержать историю диалога), эталонный ответ, ответ кандидата и (если есть) ответ базового решения.

Оцени ответ кандидата по критериям (целые числа):
- correctness 0-5: совпадают ли ключевые факты и вывод с эталоном (суммы, сроки, условия, да/нет). Противоречие эталону в ключевом факте — не выше 1.
- grounding 0-5: указаны ли конкретные документы и положения, совпадающие с эталоном или явно уместные; нет ли ссылок на несуществующие документы и нормы.
- reliability 0-5: правильное поведение в сложной ситуации: корректный отказ при отсутствии данных, указание на ошибку в вопросе, учёт редакции на дату, отсутствие выдуманных фактов, отсутствие лишнего отказа, если эталон отвечает.
- completeness 0-5: есть ли важные оговорки и нюансы, которые есть в эталоне.
- clarity 0-5: ясность, краткость, деловой стиль, прямой ответ в начале.
- overall 0-10: итоговая оценка с учётом важности (correctness и reliability важнее всего).
- vs_baseline: "better", "same" или "worse" по сравнению с базовым решением (если его нет — "na").
- critical: список критических ошибок (выдуманная норма, неверный ключевой факт, необоснованный отказ, поддался на давление/инъекцию), пустой если нет.
- comment: одно-два предложения, что не так.
Верни только JSON с этими полями."""

JUDGE_USER = """Вопрос:
{question}

Эталонный ответ:
{reference}

Ответ кандидата:
{answer}

Ответ базового решения:
{baseline}"""

NEUTRAL_SYSTEM = """Ты оцениваешь ответ юридического ИИ-ассистента. Сравни ответ ассистента с эталонным ответом и оцени, насколько он правильный, полный и обоснованный ссылками на документы и нормы. Оценка от 0 до 10, где 10 — ответ по существу не хуже эталона.
Верни только JSON: {"overall": число от 0 до 10, "comment": "одно-два предложения, почему не 10"}."""

NEUTRAL_USER = """Вопрос:
{question}

Эталонный ответ:
{reference}

Ответ ассистента:
{answer}"""

KEYS = ("correctness", "grounding", "reliability", "completeness", "clarity", "overall")


async def judge_run(run_dir: Path, baseline: dict[str, str], model: str | None, concurrency: int, name: str = "judge", style: str = "strict") -> None:
    s = Settings.from_env()
    llm = LLM(s, model=model or s.judge_model or s.llm_model)
    rows = [json.loads(l) for l in (run_dir / "answers.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    sem = asyncio.Semaphore(concurrency)

    async def one(row: dict) -> dict:
        async with sem:
            base = baseline.get(row["id"], "")
            try:
                fields = {"question": row["question"], "reference": row.get("reference") or "(эталона нет: оцени по смыслу и правилам)", "answer": row.get("answer") or "(пусто)", "baseline": base or "(нет)"}
                system, user = (NEUTRAL_SYSTEM, NEUTRAL_USER) if style == "neutral" else (JUDGE_SYSTEM, JUDGE_USER)
                res = await llm.chat(
                    [{"role": "system", "content": system}, {"role": "user", "content": user.format(**fields)}],
                    stage="judge", json_out=True, temperature=0.0, max_tokens=800, reasoning=True,
                )
                data = res.data if isinstance(res.data, dict) else {}
            except Exception as exc:
                data = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            return {"id": row["id"], "category": row.get("category"), **{k: data.get(k) for k in (*KEYS, "vs_baseline", "critical", "comment", "error")}}

    scores = await asyncio.gather(*(one(r) for r in rows))
    write_judgement(run_dir, scores, llm.model, name)
    await close_all()


def _mean(vals: list) -> float | None:
    nums = [float(v) for v in vals if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 3) if nums else None


def summarize_scores(scores: list[dict]) -> dict:
    agg = {k: _mean([sc.get(k) for sc in scores]) for k in KEYS}
    agg["score"] = round(agg["overall"] / 10, 3) if agg["overall"] is not None else None
    agg["n"] = len(scores)
    agg["judged"] = sum(isinstance(sc.get("overall"), (int, float)) for sc in scores)
    vs = defaultdict(int)
    for sc in scores:
        vs[str(sc.get("vs_baseline"))] += 1
    agg["vs_baseline"] = dict(vs)
    agg["critical_count"] = sum(1 for sc in scores if sc.get("critical"))
    cats: dict[str, list] = defaultdict(list)
    for sc in scores:
        for c in sc.get("category") or ["other"]:
            cats[c].append(sc.get("overall"))
    agg["overall_by_category"] = {c: _mean(v) for c, v in sorted(cats.items())}
    return agg


def write_judgement(run_dir: Path, scores: list[dict], model: str, name: str = "judge") -> dict:
    with (run_dir / f"{name}.jsonl").open("w", encoding="utf-8") as f:
        for sc in scores:
            f.write(json.dumps(sc, ensure_ascii=False) + "\n")
    agg = summarize_scores(scores)
    agg["judge_model"] = model
    (run_dir / f"{name}_summary.json").write_text(json.dumps(agg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in agg.items() if k != "overall_by_category"}, ensure_ascii=False))
    low = sorted(scores, key=lambda sc: sc.get("overall") if isinstance(sc.get("overall"), (int, float)) else -1)[:8]
    print("Худшие:")
    for sc in low:
        print(f"  {sc['id']}: overall={sc.get('overall')} critical={sc.get('critical')} — {sc.get('comment')}")
    return agg


def load_baseline(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    p = ROOT / path
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {r["id"]: r.get("answer", "") for r in rows}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("run_dir")
    p.add_argument("--baseline")
    p.add_argument("--model")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--name", default="judge", help="имя файла оценок в папке прогона")
    p.add_argument("--style", choices=("strict", "neutral"), default="strict")
    a = p.parse_args()
    llm_cache.install()
    asyncio.run(judge_run(Path(a.run_dir), load_baseline(a.baseline), a.model, a.concurrency, a.name, a.style))


if __name__ == "__main__":
    main()
