from __future__ import annotations

import re
from datetime import date

import pytest

from legal_core.kb.loader import load_kb
from legal_core.kb.parser import is_note, parse_documents
from legal_core.pipeline.units import resolve_unit

@pytest.fixture(scope="module")
def docs(kb_path):
    return {d.doc_id: d for d in parse_documents(load_kb(kb_path))}


def test_all_documents_parsed(docs):
    assert len(docs) == 21
    assert {d.category for d in docs.values()} == {"law", "company"}


def test_tk70_parts(docs):
    d = docs["tk-rf-st-70"]
    assert [u.anchor for u in d.units] == [f"ch-{i}" for i in range(1, 8)]
    assert d.unit("ch-5").text.startswith("Срок испытания не может превышать трех месяцев")
    assert d.unit("ch-3").text.startswith("В период испытания")
    assert d.notes[0].effective == date(2026, 9, 1)


def test_repealed_part_keeps_numbering(docs):
    d = docs["tk-rf-st-152"]
    assert d.unit("ch-2").repealed
    assert d.unit("ch-3").text.startswith("Работа, произведенная сверх нормы")


def test_subpoints_inside_parts(docs):
    d = docs["tk-rf-st-99"]
    assert d.unit("ch-2-p-1").label == "п. 1 ч. 2"
    assert d.unit("ch-3-p-3").notes[0].effective == date(2022, 7, 25)


def test_garant_notes_never_leak_into_text(docs):
    leak = re.compile(r"^(Статья|Часть|Пункт|Подпункт|Абзац)\s+[\d.]+\S*\s+(изменен|дополнен|утратил)")
    for d in docs.values():
        for u in d.units:
            if u.repealed:
                continue
            for line in u.text.split("\n"):
                assert not leak.match(line), (d.doc_id, u.anchor, line)


def test_note_detection_keeps_law_text():
    assert is_note("Часть 1.1 изменена с 30 мая 2025 г. - Федеральный закон от 30 ноября 2024 г. N 420-ФЗ")
    assert is_note("Положения статьи 152 (в редакции Федерального закона от 22 апреля 2024 г. № 91-ФЗ) не являются основанием")
    assert not is_note("Положения настоящего подпункта применяются при условии представления документов")
    assert not is_note("Часть вторая утратила силу.")


def test_koap_prim_and_parts(docs):
    d = docs["koap-rf-st-13-11"]
    assert d.unit("ch-5.1") is not None
    assert d.unit("prim-1").label == "примечание 1"


def test_vat_rate_current(docs):
    assert "22 процента" in docs["nk-rf-st-164"].unit("p-3").text


def test_company_docs(docs):
    old = docs["reglament-soglasovaniya-dogovorov-red-10-01-2023"]
    new = docs["reglament-soglasovaniya-dogovorov-red-01-03-2025"]
    assert old.valid_to == date(2025, 2, 28) and new.valid_from == date(2025, 3, 1)
    assert "15 000 000" in new.unit("p-2.4").text and "10 000 000" in old.unit("p-2.4").text
    a08 = docs["dogovor-a-08-2024"]
    assert a08.counterparty == "ООО «Северный Склад»" and a08.our_party == "ООО «Меридиан»"
    p17 = docs["dogovor-p-17-2024"]
    assert any(r.kind == "amended_by" and "p-3.2" in r.target_anchors for r in p17.relations)
    assert docs["dogovor-pt-05-2023"].counterparty == "ООО «Альфа-Трейдинг»"
    k12 = docs["pismo-k-12"]
    assert any(r.kind == "supplements" and r.target_doc == new.doc_id and r.target_anchors == ["p-5.4"] for r in k12.relations)


def test_injection_flagged(docs):
    memo = docs["sluzhebnaya-zapiska-yuo-7"]
    flagged = [u for u in memo.units if u.injection]
    assert len(flagged) == 1 and "ИИ" in flagged[0].text
    assert not any(u.injection for d in docs.values() for u in d.units if d.doc_id != memo.doc_id)


def test_resolve_unit(docs):
    assert resolve_unit(docs["dogovor-a-08-2024"], "п. 3.1") == "p-3.1"
    assert resolve_unit(docs["tk-rf-st-59"], "часть первая") == "ch-1"
    assert resolve_unit(docs["tk-rf-st-99"], "п. 1 ч. 2") == "ch-2-p-1"
    assert resolve_unit(docs["152-fz-st-9"], "ч. 2") == "ch-2"
    assert resolve_unit(docs["nk-rf-st-164"], "пп. 1 п. 2") == "p-2-pp-1"
