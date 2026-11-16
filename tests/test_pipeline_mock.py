from __future__ import annotations

import asyncio
import re
import json
from datetime import date
from pathlib import Path

import pytest

from legal_core.config import Settings
from legal_core.dialog import parse_dialog
from legal_core.index.store import KnowledgeIndex
from legal_core.kb.loader import load_kb
from legal_core.pipeline.engine import Assistant, rank_candidates
from legal_core.providers.llm import LLM, LLMResult

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.usefixtures("kb_path")


class FakeLLM(LLM):
    def __init__(self, s, script, delays=None):
        super().__init__(s)
        self.script = script
        self.seen = []
        self.delays = delays or {}

    async def _raw(self, payload, stage):
        self.seen.append(stage)
        await asyncio.sleep(self.delays.get(stage, 0))
        content = self.script[stage.split(":")[0]](payload)
        self.usage.add(stage, {}, 0.0)
        return LLMResult(content=content)


def _settings() -> Settings:
    s = Settings()
    s.today = date(2026, 11, 10)
    s.llm_base_url = "http://fake"
    s.reasoning = "off"
    s.candidates = 1
    s.complete = False
    return s


def _index() -> KnowledgeIndex:
    return asyncio.run(KnowledgeIndex.build(load_kb(ROOT / "data/kb.zip"), None))


def _rent_analyze(_):
    return json.dumps({"standalone_question": "Размер арендной платы и срок внесения по договору А-08/2024", "scope": "legal", "flags": [],
                       "documents": [{"doc_id": "dogovor-a-08-2024", "confidence": 0.95}], "mentions": [{"quote": "Северный Склад", "kind": "counterparty", "value": "ООО «Северный Склад»"}],
                       "references": [], "event_date": {"from": None, "to": None, "quote": ""}, "user_claims": [], "sub_questions": ["размер арендной платы", "срок внесения"],
                       "search_queries": ["арендная плата в месяц", "срок внесения арендной платы"], "hypothetical_provision": "Арендная плата вносится ежемесячно не позднее"})


def _rent_grade(payload):
    text = payload["messages"][1]["content"]
    items = []
    for line in text.split("\n"):
        if line.startswith("[S"):
            sid = line[1:line.index("]")]
            items.append({"id": sid, "relevance": 3 if "А-08" in line else 0})
    return json.dumps({"fragments": items, "coverage": [], "missing_info": [], "extra_queries": []})


def _rent_generate(payload):
    text = payload["messages"][1]["content"]
    sids = {}
    for sid, where in re.findall(r"^\[(S\d+)\] .*\nПоложение: (п\. \d+\.\d+)", text, re.M):
        sids.setdefault(where[3:], sid)
    return json.dumps({"analysis": "ok", "status": "answer", "answer": f"Арендная плата по договору аренды № А-08/2024 — 450 000 рублей в месяц, включая НДС [{sids['3.1']}]. Вносится не позднее 5-го числа текущего месяца [{sids['3.2']}].", "used": []})


def _ok_verify(_):
    return json.dumps({"verdict": "ok", "issues": []})


def _rent_script(**extra) -> dict:
    return {"analyze": _rent_analyze, "grade": _rent_grade, "generate": _rent_generate, "verify": _ok_verify, **extra}


def test_rent_question_end_to_end():
    s = _settings()
    idx = _index()
    llm = FakeLLM(s, _rent_script())
    a = Assistant(s, idx, llm, None, None)
    res = asyncio.run(a.answer(parse_dialog("Какая арендная плата по договору с ООО «Северный Склад» и до какого числа её нужно вносить?"), mode="submission"))
    assert res.status == "answer"
    assert "(п. 3.1)" in res.text and "(п. 3.2)" in res.text, res.text
    assert "\n" not in res.text
    assert llm.seen[:3] == ["analyze", "grade", "generate"]


def _cited(payload) -> list[str]:
    return re.findall(r"^\[(S\d+)\]", payload["messages"][1]["content"].split("Положения, на которые ссылается ответ:")[1], re.M)


def _answer_with_addition(addition) -> tuple:
    s = _settings()
    s.complete = True
    llm = FakeLLM(s, _rent_script(complete=lambda payload: json.dumps({"addition": addition(payload)}, ensure_ascii=False)))
    res = asyncio.run(Assistant(s, _index(), llm, None, None).answer(parse_dialog("Какая арендная плата по договору с ООО «Северный Склад» и до какого числа её нужно вносить?"), mode="submission"))
    return res, next(x for x in res.trace if x.get("step") == "complete")


def test_completion_adds_sentence_from_cited_provision():
    res, step = _answer_with_addition(lambda payload: f"Арендная плата вносится ежемесячно авансом [{_cited(payload)[-1]}].")
    assert step["accepted"] and "авансом" in res.text, res.text


def test_completion_rejects_uncited_marker_and_repetition():
    for addition in (lambda _: "Коммунальные платежи оплачиваются отдельно [S99].", lambda payload: f"Арендная плата составляет 450 000 рублей в месяц [{_cited(payload)[0]}]."):
        res, step = _answer_with_addition(addition)
        assert step["addition"] == "" and not step["accepted"], step


def test_footnote_for_recent_law_change():
    def analyze(_):
        return json.dumps({"standalone_question": "Максимальный срок испытания главного бухгалтера", "documents": [{"doc_id": "tk-rf-st-70", "confidence": 0.9}], "sub_questions": ["срок испытания главбуха"], "search_queries": ["срок испытания главных бухгалтеров шесть месяцев"]})

    def grade(payload):
        text = payload["messages"][1]["content"]
        items = [{"id": line[1:line.index("]")], "relevance": 3 if "ст. 70" in line or "статья 70" in line else 0} for line in text.split("\n") if line.startswith("[S")]
        return json.dumps({"fragments": items})

    def generate(payload):
        text = payload["messages"][1]["content"]
        blocks = re.split(r"\n(?=\[S\d+\] )", text)
        sid = next(re.match(r"\[(S\d+)\]", b).group(1) for b in blocks if re.match(r"\[S\d+\]", b) and "Срок испытания не может превышать" in b)
        return json.dumps({"status": "answer", "answer": f"Для главных бухгалтеров срок испытания не может превышать шести месяцев, если иное не установлено федеральным законом [{sid}]."})

    s = _settings()
    llm = FakeLLM(s, {"analyze": analyze, "grade": grade, "generate": generate, "verify": lambda _: json.dumps({"verdict": "ok"})})
    a = Assistant(s, _index(), llm, None, None)
    res = asyncio.run(a.answer(parse_dialog("Какой максимальный срок испытания можно установить главному бухгалтеру?"), mode="ui"))
    assert "ТК РФ, ст. 70, ч. 5" in res.text, res.text
    assert "/kb/tk-rf-st-70" in res.text
    assert res.footnotes and "статья 70 ТК РФ изменена с 1 сентября 2026 г." in res.footnotes[0], res.footnotes
    assert "[^1]" in res.text


def test_invalid_marker_triggers_revision():
    calls = {"n": 0}

    def generate(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"status": "answer", "answer": "Срок — 99 дней [S77]."})
        return json.dumps({"status": "insufficient", "answer": "В базе знаний нет документов по этому вопросу."})

    s = _settings()
    llm = FakeLLM(s, {"analyze": lambda _: "{}", "grade": lambda _: "{}", "generate": generate, "verify": lambda _: json.dumps({"verdict": "ok"})})
    a = Assistant(s, _index(), llm, None, None)
    res = asyncio.run(a.answer(parse_dialog("Какой размер пособия по беременности и родам?"), mode="submission"))
    assert calls["n"] == 2
    assert res.status == "insufficient"
    revise = next(t for t in res.trace if t["step"] == "revise")
    assert revise["accepted"] and any("S77" in issue for issue in revise["issues"])


def test_llm_outage_gives_honest_fallback():
    def broken(_):
        raise RuntimeError("402 Insufficient Balance")

    s = _settings()
    llm = FakeLLM(s, {"analyze": broken, "grade": broken, "generate": broken, "verify": broken})
    a = Assistant(s, _index(), llm, None, None)
    res = asyncio.run(a.answer(parse_dialog("Какая арендная плата по договору с ООО «Северный Склад»?"), mode="submission"))
    assert res.text.startswith("Не удалось сформировать проверенный ответ: языковая модель сейчас недоступна.")
    assert res.citations and "(" in res.text
    assert res.error


def test_offtopic_flag_keeps_only_relevant_fragments():
    def generate(payload):
        content = payload["messages"][1]["content"]
        assert "не юридическим" in content
        assert "(фрагменты не найдены)" in content
        return json.dumps({"status": "insufficient", "answer": "В базе знаний нет документов о пособиях по беременности и родам."})

    s = _settings()
    llm = FakeLLM(s, {
        "analyze": lambda _: json.dumps({"standalone_question": "Размер пособия по беременности и родам", "scope": "offtopic"}),
        "grade": lambda _: json.dumps({"relevance": {f"S{i}": 1 for i in range(1, 9)}}),
        "generate": generate,
        "verify": lambda _: json.dumps({"verdict": "ok"}),
    })
    a = Assistant(s, _index(), llm, None, None)
    res = asyncio.run(a.answer(parse_dialog("Какой размер пособия по беременности и родам?"), mode="submission"))
    assert res.status == "insufficient"
    assert "grade" in llm.seen
    assert res.text.startswith("В базе знаний нет документов")



def test_typical_candidate_wins_majority_vote():
    cands = [
        {"status": "answer", "answer": "Да, но только при условии уведомления [S1][S2]. Договор продлевается автоматически [S3]."},
        {"status": "answer", "answer": "Нет, без уведомления за 10 дней нельзя [S1]."},
        {"status": "answer", "answer": "Нет, поставщик обязан уведомить за 10 дней [S1], а цена определяется на дату заявки [S2]."},
        {"status": "partial", "answer": "Нет [S1]."},
        {"status": "answer", "answer": "Нет [S1]."},
        {"status": "answer", "answer": "Нет, поставщик обязан уведомить покупателя за 10 дней [S1], цена определяется на дату заявки [S2], а договор продлевается автоматически [S3]."},
    ]
    ranked = rank_candidates(cands)
    assert [cands.index(c) for c in ranked] == [1, 4, 2, 5]
    assert rank_candidates(cands[:1]) == cands[:1]
    paired = [{"status": "answer", "answer": "Да, порог 10 млн [S1]."}, {"status": "answer", "answer": "Да, порог 10 млн [S1]. В действующей редакции — 15 млн [S2]."}]
    assert rank_candidates(paired, [["S1"], ["S2"]])[0] is paired[1]
    assert rank_candidates(paired)[0] is paired[0]


def test_candidates_are_generated_in_parallel_and_one_is_used():
    def analyze(_):
        return json.dumps({"standalone_question": "Арендная плата по договору А-08/2024", "scope": "legal", "documents": [{"doc_id": "dogovor-a-08-2024", "confidence": 0.95}]})

    def grade(payload):
        sids = re.findall(r"^\[(S\d+)\] .*А-08", payload["messages"][1]["content"], re.M)
        return json.dumps({"relevance": {sid: 3 for sid in sids}})

    def generate(payload):
        sid = re.search(r"^\[(S\d+)\]", payload["messages"][1]["content"], re.M).group(1)
        return json.dumps({"status": "answer", "answer": f"Арендная плата вносится ежемесячно [{sid}]."})

    s = _settings()
    s.candidates = 3
    s.verify = False
    llm = FakeLLM(s, {"analyze": analyze, "grade": grade, "generate": generate}, delays={"generate:2": 5.0})
    res = asyncio.run(Assistant(s, _index(), llm, None, None).answer(parse_dialog("Какая арендная плата по договору с ООО «Северный Склад»?"), mode="submission"))
    assert res.text.startswith("Арендная плата вносится ежемесячно")
    assert sorted(x for x in llm.seen if x.startswith("generate")) == ["generate", "generate:1", "generate:2"]
    assert any(step.get("step") == "candidates" and step.get("count") == 2 and step.get("early") for step in res.trace)
    assert res.seconds < 2


def test_ui_waits_for_agreeing_candidates():
    def analyze(_):
        return json.dumps({"standalone_question": "Арендная плата по договору А-08/2024", "scope": "legal", "documents": [{"doc_id": "dogovor-a-08-2024", "confidence": 0.95}]})

    def grade(payload):
        sids = re.findall(r"^\[(S\d+)\] .*А-08", payload["messages"][1]["content"], re.M)
        return json.dumps({"relevance": {sid: 3 for sid in sids}})

    def generate(payload):
        sid = re.search(r"^\[(S\d+)\]", payload["messages"][1]["content"], re.M).group(1)
        return json.dumps({"status": "answer", "answer": f"Арендная плата вносится ежемесячно [{sid}]."})

    s = _settings()
    s.candidates = 3
    llm = FakeLLM(s, {"analyze": analyze, "grade": grade, "generate": generate, "verify": _ok_verify}, delays={"generate:1": 1.0, "generate:2": 5.0})
    res = asyncio.run(Assistant(s, _index(), llm, None, None).answer(parse_dialog("Какая арендная плата по договору с ООО «Северный Склад»?"), mode="ui"))
    assert res.text.startswith("Арендная плата вносится ежемесячно")
    assert any(step.get("step") == "candidates" and step.get("count") == 2 and step.get("early") for step in res.trace)
    assert 1.0 <= res.seconds < 2


def test_disagreeing_candidates_wait_for_the_third():
    def analyze(_):
        return json.dumps({"standalone_question": "Арендная плата по договору А-08/2024", "scope": "legal", "documents": [{"doc_id": "dogovor-a-08-2024", "confidence": 0.95}]})

    def grade(payload):
        sids = re.findall(r"^\[(S\d+)\] .*А-08", payload["messages"][1]["content"], re.M)
        return json.dumps({"relevance": {sid: 3 for sid in sids}})

    made = {"n": 0}

    def generate(payload):
        made["n"] += 1
        sid = re.search(r"^\[(S\d+)\]", payload["messages"][1]["content"], re.M).group(1)
        if made["n"] == 2:
            return json.dumps({"status": "insufficient", "answer": "В базе знаний нет документов об этом."})
        return json.dumps({"status": "answer", "answer": f"Арендная плата вносится ежемесячно [{sid}]."})

    s = _settings()
    s.candidates = 3
    s.verify = False
    llm = FakeLLM(s, {"analyze": analyze, "grade": grade, "generate": generate})
    res = asyncio.run(Assistant(s, _index(), llm, None, None).answer(parse_dialog("Какая арендная плата по договору с ООО «Северный Склад»?"), mode="submission"))
    assert res.status == "answer"
    assert any(step.get("step") == "candidates" and step.get("count") == 3 and not step.get("early") for step in res.trace)


def test_verification_rejects_candidate_and_takes_next_one():
    def analyze(_):
        return json.dumps({"standalone_question": "Арендная плата по договору А-08/2024", "scope": "legal", "documents": [{"doc_id": "dogovor-a-08-2024", "confidence": 0.95}]})

    def grade(payload):
        sids = re.findall(r"^\[(S\d+)\] .*А-08", payload["messages"][1]["content"], re.M)
        return json.dumps({"relevance": {sid: 3 for sid in sids}})

    made = {"n": 0}

    def generate(payload):
        made["n"] += 1
        sid = re.search(r"^\[(S\d+)\]", payload["messages"][1]["content"], re.M).group(1)
        text = "Плата вносится ежемесячно" if made["n"] == 1 else "Арендная плата вносится ежемесячно, не позднее установленного числа"
        return json.dumps({"status": "answer", "answer": f"{text} [{sid}]."})

    def verify(payload):
        rejected = "Ответ ассистента:\nПлата вносится ежемесячно [" in payload["messages"][1]["content"]
        return json.dumps({"verdict": "fix", "issues": [{"type": "unsupported", "detail": "x", "fix": "y"}]} if rejected else {"verdict": "ok", "issues": []})

    s = _settings()
    s.candidates = 2
    llm = FakeLLM(s, {"analyze": analyze, "grade": grade, "generate": generate, "verify": verify})
    res = asyncio.run(Assistant(s, _index(), llm, None, None).answer(parse_dialog("Какая арендная плата по договору с ООО «Северный Склад»?"), mode="submission"))
    assert res.text.startswith("Арендная плата вносится ежемесячно, не позднее установленного числа"), res.text
    assert [x for x in llm.seen if x == "verify"] == ["verify", "verify"]
    assert made["n"] == 2


def _liability_script(standalone: str, quote: str) -> dict:
    def analyze(_):
        return json.dumps({"standalone_question": standalone, "scope": "legal", "flags": [],
                           "documents": [{"doc_id": "dogovor-p-17-2024", "confidence": 0.9}, {"doc_id": "ds-1-k-dogovoru-p-17-2024", "confidence": 0.6}],
                           "ambiguous": [{"quote": quote, "candidates": ["dogovor-p-17-2024", "dogovor-pt-05-2023"], "why": "похожие названия"}],
                           "event_date": {"from": None, "to": None, "quote": ""}, "sub_questions": ["предел ответственности"],
                           "search_queries": ["ограничение ответственности сторон", "дополнительное соглашение адрес доставки"]})

    def grade(payload):
        units = re.findall(r"^\[(S\d+)\] .*\nПоложение: ([^\n(]+)", payload["messages"][1]["content"], re.M)
        return json.dumps({"relevance": {sid: 0 if where.strip() == "п. 3.2" else 3 for sid, where in units}, "coverage": [], "missing_info": [], "extra_queries": []})

    def generate(_):
        return json.dumps({"analysis": "ok", "status": "answer", "answer": "Ответственность каждой стороны ограничена 50% от общей цены договора [S1].", "used": ["S1"]})

    return {"analyze": analyze, "grade": grade, "generate": generate, "verify": lambda _: json.dumps({"verdict": "ok", "issues": []})}


def _liability_answer(question: str, quote: str = "Альфой"):
    s = _settings()
    llm = FakeLLM(s, _liability_script("Предел ответственности по договору № П-17/2024 (с учётом дополнительного соглашения № 1)", quote))
    return asyncio.run(Assistant(s, _index(), llm, None, None).answer(parse_dialog(question), mode="submission"))


def test_amendment_named_only_by_analyzer_does_not_keep_unrelated_supplement():
    res = _liability_answer("Какой максимальный размер ответственности сторон по договору № П-17/2024?")
    retrieved = next(step["candidates"] for step in res.trace if step["step"] == "retrieve")
    assert any(key.startswith("ds-1-k-dogovoru-p-17-2024") for key in retrieved)
    assert all(f.doc.doc_type != "supplement" for f in res.fragments)
    asked = _liability_answer("Какой предел ответственности по договору № П-17/2024 с учётом дополнительного соглашения?")
    assert any(f.doc.doc_type == "supplement" for f in asked.fragments)


def test_explicit_contract_number_removes_ambiguity():
    assert _liability_answer("Какой предел ответственности по договору № П-17/2024 с «Альфой»?").analysis.ambiguous == []
    assert _liability_answer("Какой предел ответственности по договору с «Альфой»?").analysis.ambiguous


def test_ambiguity_between_contracts_needs_a_shared_name():
    from legal_core.pipeline.analyze import analyze

    def run(quote: str, question: str):
        script = {"analyze": lambda _: json.dumps({"standalone_question": question, "ambiguous": [{"quote": quote, "candidates": ["dogovor-p-17-2024", "dogovor-pt-05-2023"]}]})}
        s = _settings()
        return asyncio.run(analyze(FakeLLM(s, script), _index(), "", parse_dialog(question), s.today))

    described = run("Договор на 12 млн рублей заключали в августе 2024 года", "Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли было одобрение?")
    assert described.ambiguous == [] and described.dropped
    assert run("с «Альфой»", "Какой срок оплаты по договору с «Альфой»?").ambiguous
    assert run("с «Альфа-Трейд»", "Какой срок оплаты по договору с «Альфа-Трейд»?").ambiguous == []
    assert run("«Альфа-Трейдинг»", "Какой срок оплаты по договору с «Альфа-Трейдинг»?").ambiguous == []
    assert run("договору поставки", "Какой срок оплаты по договору поставки?").ambiguous


def test_relative_event_date_is_not_offered_as_calendar_date():
    from datetime import date as day
    from legal_core.pipeline.analyze import Analysis
    from legal_core.pipeline.engine import RELATIVE_DATE_NOTE

    s = _settings()
    assistant = Assistant(s, _index(), FakeLLM(s, {}), None, None)
    relative = Analysis(standalone="x", event_from=day(2026, 11, 11), event_to=day(2026, 11, 11), event_quote="в этом году")
    explicit = Analysis(standalone="x", event_from=day(2024, 8, 1), event_to=day(2024, 8, 31), event_quote="в августе 2024 года")
    assert RELATIVE_DATE_NOTE in assistant._analysis_block(relative)
    assert RELATIVE_DATE_NOTE not in assistant._analysis_block(explicit)
