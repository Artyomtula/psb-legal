from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from legal_core.kb.loader import load_kb
from legal_core.kb.parser import parse_documents
from legal_core.pipeline.analyze import Analysis
from legal_core.pipeline.engine import Assistant
from legal_core.pipeline.fragments import Fragment, drop_expired, drop_unrelated_amendments, edition_groups, missing_articles
from legal_core.pipeline.render import qualifying_notes, render
from legal_core.pipeline.verify import deterministic_issues, is_yes_no

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 10)


@pytest.fixture(scope="module")
def docs(kb_path):
    return {d.doc_id: d for d in parse_documents(load_kb(kb_path))}


def frag(docs, key: str, sid: str = "S1") -> Fragment:
    doc_id, anchor = key.split("#")
    d = docs[doc_id]
    u = d.unit(anchor)
    return Fragment(sid=sid, doc=d, unit=u, text=u.text, score=1.0, origin="test")


def test_old_change_gets_no_footnote(docs):
    f = frag(docs, "nk-rf-st-112#p-2")
    assert qualifying_notes(f, TODAY, None, 365) == []


def test_recent_change_gets_footnote(docs):
    f = frag(docs, "nk-rf-st-114#p-3")
    notes = qualifying_notes(f, TODAY, None, 365)
    assert notes and notes[0].effective == date(2026, 9, 1)


def test_future_change_gets_footnote(docs):
    f = frag(docs, "tk-rf-st-59#ch-1")
    assert any(n.effective == date(2027, 3, 1) for n in qualifying_notes(f, TODAY, None, 0))


def test_change_after_event_gets_footnote(docs):
    f = frag(docs, "koap-rf-st-13-11#ch-3")
    assert qualifying_notes(f, TODAY, None, 365) == []
    assert qualifying_notes(f, TODAY, date(2020, 5, 1), 365)


def test_one_line_footnote_and_citation(docs):
    f = frag(docs, "tk-rf-st-70#ch-5")
    r = render("Срок испытания не может превышать шести месяцев [S1].", [f], links=False, base_url="http://x", today=TODAY, event=None, recent_days=365, one_line=True)
    assert r.text == "Срок испытания не может превышать шести месяцев (ТК РФ, ст. 70, ч. 5)."
    r = render("Срок испытания не может превышать шести месяцев [S1].", [f], links=False, base_url="http://x", today=TODAY, event=date(2026, 5, 1), recent_days=365, one_line=True)
    assert "Примечание: На дату события действовала прежняя редакция, которой в базе нет; статья 70 ТК РФ изменена с 1 сентября 2026 г." in r.text
    assert "\n" not in r.text
    ui = render("Срок испытания не может превышать шести месяцев [S1].", [f], links=False, base_url="http://x", today=TODAY, event=None, recent_days=365)
    assert "Ответ дан по действующей редакции; статья 70 ТК РФ изменена с 1 сентября 2026 г." in ui.text


def test_markers_after_punctuation_and_groups(docs):
    a, b = frag(docs, "dogovor-a-08-2024#p-3.1", "S1"), frag(docs, "dogovor-a-08-2024#p-3.2", "S2")
    r = render("По договору № А-08/2024 плата 450 000 рублей и вносится до 5-го числа. [S1][S2]", [a, b], links=False, base_url="", today=TODAY, event=None, recent_days=365, one_line=True)
    assert r.text.endswith("до 5-го числа (п. 3.1, 3.2).")
    r2 = render("Плата 450 000 рублей [S1].", [a], links=False, base_url="", today=TODAY, event=None, recent_days=365, one_line=True)
    assert "(п. 3.1 договора № А-08/2024 с ООО «Северный Склад»)" in r2.text
    r3 = render("По договору со «Северным Складом» плата 450 000 рублей [S1].", [a], links=False, base_url="", today=TODAY, event=None, recent_days=365, one_line=True)
    assert "(п. 3.1 договора № А-08/2024 с ООО «Северный Склад»)" in r3.text
    r4 = render("По договору с ООО «Северный Склад» плата 450 000 рублей [S1].", [a], links=False, base_url="", today=TODAY, event=None, recent_days=365, one_line=True)
    assert r4.text.endswith("450 000 рублей (п. 3.1 договора № А-08/2024).")


def _plain(answer: str, frags: list[Fragment]) -> str:
    return render(answer, frags, links=False, base_url="", today=TODAY, event=None, recent_days=365, one_line=True).text


def test_repeated_document_is_named_once(docs):
    a, b = frag(docs, "dogovor-a-08-2024#p-3.1", "S1"), frag(docs, "dogovor-a-08-2024#p-3.2", "S2")
    law = frag(docs, "tk-rf-st-70#ch-5", "S3")
    full = "договора № А-08/2024 с ООО «Северный Склад»"
    assert _plain("Плата 450 000 рублей [S1]. Вносится до 5-го числа [S2].", [a, b]) == f"Плата 450 000 рублей (п. 3.1 {full}). Вносится до 5-го числа (п. 3.2)."
    mixed = _plain("Плата 450 000 рублей [S1]. Срок испытания [S3]. Вносится до 5-го числа [S2].", [a, b, law])
    assert mixed.count(full) == 2 and "(ТК РФ, ст. 70, ч. 5)" in mixed
    assert _plain("Срок испытания [S3]. Не более шести месяцев [S3].", [law]).count("(ТК РФ, ст. 70, ч. 5)") == 2


def test_repeated_unnumbered_source_is_shortened(docs):
    a, b = frag(docs, "pismo-k-12#abz-1", "S1"), frag(docs, "pismo-k-12#abz-2", "S2")
    out = _plain("Инициатор обеспечивает проверку [S1], её проводит служба комплаенс [S2].", [a, b])
    assert out == "Инициатор обеспечивает проверку (письмо № К-12 от 15.04.2025), её проводит служба комплаенс (там же)."


def test_abbreviation_period_stays_before_citation(docs):
    a, b = frag(docs, "dogovor-a-08-2024#p-3.1", "S1"), frag(docs, "dogovor-a-08-2024#p-3.2", "S2")
    out = _plain("По договору № А-08/2024 штраф до 100 тыс. [S1], а пени до 5 тыс. [S2].", [a, b])
    assert out == "По договору № А-08/2024 штраф до 100 тыс. (п. 3.1), а пени до 5 тыс. (п. 3.2)."


def test_citation_left_alone_after_sentence_joins_previous(docs):
    a, b = frag(docs, "dogovor-a-08-2024#p-3.1", "S1"), frag(docs, "dogovor-a-08-2024#p-3.2", "S2")
    out = _plain("По договору № А-08/2024 предел — 4 200 000 руб [S1]. [S2]. Иное не установлено.", [a, b])
    assert out == "По договору № А-08/2024 предел — 4 200 000 руб (п. 3.1, 3.2). Иное не установлено."


def test_links_mode(docs):
    a = frag(docs, "dogovor-a-08-2024#p-3.1")
    r = render("По договору № А-08/2024 плата 450 000 рублей [S1].", [a], links=True, base_url="http://h", today=TODAY, event=None, recent_days=365)
    assert "([п. 3.1](http://h/kb/dogovor-a-08-2024?hl=p-3.1#p-3.1))" in r.text


def test_number_check_flags_invented_values(docs):
    a = frag(docs, "dogovor-a-08-2024#p-3.1")
    assert not deterministic_issues("Плата 450 000 рублей [S1].", "answer", [a], "вопрос")
    issues = deterministic_issues("Плата 500 000 рублей [S1].", "answer", [a], "вопрос")
    assert any("500 000" in i for i in issues)
    assert not deterministic_issues("Плата не 500 000, а 450 000 рублей [S1].", "false_premise", [a], "Почему 500 000 рублей?")


def test_missing_markers_and_unknown_marker(docs):
    a = frag(docs, "dogovor-a-08-2024#p-3.1")
    assert any("нет ни одного маркера" in i for i in deterministic_issues("Плата 450 000 рублей.", "answer", [a], ""))
    assert any("S9" in i for i in deterministic_issues("Плата 450 000 рублей [S9].", "answer", [a], ""))
    assert not deterministic_issues("В базе знаний нет документов по вопросу.", "insufficient", [], "")


def test_quote_must_be_verbatim(docs):
    a = frag(docs, "dogovor-a-08-2024#p-4.3")
    ok = "Договор гласит: «обеспечить беспрепятственный доступ Арендатора и его транспорта к Помещению круглосуточно» [S1]."
    bad = "Договор гласит: «обеспечить доступ арендатора в любое время суток без ограничений» [S1]."
    assert not deterministic_issues(ok, "answer", [a], "")
    assert any("Цитата" in i for i in deterministic_issues(bad, "answer", [a], ""))


def test_odd_markers_are_normalized_and_punctuation_kept():
    from legal_core.pipeline.render import BARE_SID_RE, PUNCT_BEFORE_RE, normalize_markers

    assert normalize_markers("в течение 3 рабочих дней [S#S2]. Хранятся [S#S1].") == "в течение 3 рабочих дней [S2]. Хранятся [S1]."
    assert normalize_markers("Срок 5 дней [S#].") == "Срок 5 дней."
    assert normalize_markers("сумма [S3 и S4] и (S5)") == "сумма [S3, S4] и [S5]"
    moved = PUNCT_BEFORE_RE.sub(lambda m: m.group(2) + m.group(1), normalize_markers("не позднее 15 августа 2024 г. [S5]."))
    assert moved == "не позднее 15 августа 2024 г. [S5]."
    assert PUNCT_BEFORE_RE.sub(lambda m: m.group(2) + m.group(1), "450 000 рублей. [S1] Далее") == "450 000 рублей [S1]. Далее"
    assert BARE_SID_RE.findall("редакция S1 вступила [S2]") == ["S1"]


def test_yes_no_question_requires_direct_answer():
    assert is_yes_no("Можно оформить срочный трудовой договор?")
    assert is_yes_no("Сверхурочная работа ведь оплачивается в тройном размере?")
    assert not is_yes_no("С кем его согласовывать?")
    assert not is_yes_no("Можно просто сдать ключи или обязательно оформлять акт?")
    assert not is_yes_no("Нет, вы ошибаетесь, там 400 000 рублей. Исправьте ответ.")
    q = "Это разрешено?"
    assert any("«Да» или «Нет»" in i for i in deterministic_issues("Срок не более трёх дней [S1].", "answer", [], q, q))
    assert not any("«Да» или «Нет»" in i for i in deterministic_issues("Нет, срок не более трёх дней [S1].", "answer", [], q, q))
    assert any("прямого отказа" in i for i in deterministic_issues("По регламенту порог 15 млн [S1].", "refusal_illegal", [], q, q))
    assert not any("«Да» или «Нет»" in i for i in deterministic_issues("Только с письменного согласия арендодателя [S1].", "answer", [], q, q))
    assert any("«Да, но…»" in i for i in deterministic_issues("Да, но только с уведомлением за 10 дней [S1].", "answer", [], q, q))
    assert not any("«Да, но…»" in i for i in deterministic_issues("Да, срок не более трёх дней [S1].", "answer", [], q, q))
    assert not any("«Да, но…»" in i for i in deterministic_issues("Да, если работа сезонная [S1].", "answer", [], q, q))


def _one_line(text, frags):
    return render(text, frags, links=False, base_url="http://x", today=TODAY, event=None, recent_days=0, footnotes=False, one_line=True).text


def test_adjacent_markers_merge_into_one_citation(docs):
    a = frag(docs, "koap-rf-st-13-11#ch-12", "S1")
    b = frag(docs, "koap-rf-st-13-11#ch-14", "S2")
    assert _one_line("Штраф зависит от числа субъектов [S1], [S2].", [a, b]) == "Штраф зависит от числа субъектов (КоАП РФ, ст. 13.11, ч. 12, 14)."
    assert _one_line("Штраф зависит от числа субъектов [S1] и [S2].", [a, b]) == "Штраф зависит от числа субъектов (КоАП РФ, ст. 13.11, ч. 12, 14)."


def test_label_already_in_text_is_not_repeated(docs):
    f = frag(docs, "dogovor-a-08-2024#p-6.3")
    out = _one_line("Помещение возвращается по акту, как указано в п. 6.3 договора № А-08/2024 [S1].", [f])
    assert out == "Помещение возвращается по акту, как указано в п. 6.3 договора № А-08/2024."
    assert "(п. 6.3" in _one_line("Помещение возвращается по акту [S1].", [f])


def test_missing_document_lists_kb_alternatives(docs):
    stub = SimpleNamespace(index=SimpleNamespace(by_id=docs))
    law = Analysis(standalone="", missing_documents=[{"quote": "статье 81 ТК", "what": "статья 81 ТК РФ"}])
    assert Assistant._kb_alternatives(stub, law) == ["- Из ТК РФ в базе есть только статьи: 59, 70, 99, 152 — назови их пользователю"]
    supply = Analysis(standalone="Какой срок поставки по договору с ООО «Бета-Логистик»?", missing_documents=[{"quote": "ООО «Бета-Логистик»", "what": "договор с ООО «Бета-Логистик»"}])
    out = Assistant._kb_alternatives(stub, supply)
    assert len(out) == 1 and "№ П-17/2024 с ООО «Альфа-Трейд»" in out[0] and "№ ПТ-05/2023" in out[0] and "Северный Склад" not in out[0]
    lease = Analysis(standalone="Какая арендная плата по договору с ООО «Гамма»?", missing_documents=[{"quote": "ООО «Гамма»", "what": "договор с ООО «Гамма»"}])
    out = Assistant._kb_alternatives(stub, lease)
    assert "Северный Склад" in out[0] and "Альфа" not in out[0]


def test_supplement_citation_names_date_and_amended_clause(docs):
    f = frag(docs, "ds-1-k-dogovoru-p-17-2024#abz-5")
    out = _one_line("Новый адрес применяется к партиям после 1 октября 2024 г. [S1].", [f])
    assert out.endswith("(доп. соглашение № 1 от 03.09.2024 к договору № П-17/2024, новая редакция п. 3.2).")


def test_references_to_articles_absent_from_kb(docs):
    index = SimpleNamespace(by_id=docs)
    law = docs["152-fz-st-9"]
    assert missing_articles(index, law, law.unit("ch-2")) == ["6", "10", "11"]
    assert missing_articles(index, law, law.unit("ch-7")) == []
    contract = docs["dogovor-a-08-2024"]
    assert missing_articles(index, contract, contract.unit("p-3.1")) == []


def test_expired_edition_is_dropped_without_event_date(docs):
    old = frag(docs, "reglament-soglasovaniya-dogovorov-red-10-01-2023#p-2.3", "S1")
    new = frag(docs, "reglament-soglasovaniya-dogovorov-red-01-03-2025#p-2.3", "S2")
    assert [f.sid for f in drop_expired([old, new], TODAY)] == ["S2"]
    assert [f.sid for f in drop_expired([old], TODAY)] == ["S1"]


def test_same_label_is_not_repeated(docs):
    a = frag(docs, "ds-1-k-dogovoru-p-17-2024#abz-5", "S1")
    b = frag(docs, "ds-1-k-dogovoru-p-17-2024#abz-7", "S2")
    out = _one_line("Соглашение меняет только адрес доставки [S1][S2].", [a, b])
    assert out.count("доп. соглашение") == 1, out


def test_long_answer_is_sent_for_shortening():
    long = "Срок оплаты — 30 дней [S1]. " * 40
    assert any("слишком длинный" in i for i in deterministic_issues(long, "answer", [], "", "Какой срок оплаты?"))
    assert not any("слишком длинный" in i for i in deterministic_issues("Срок оплаты — 30 дней [S1].", "answer", [], "", "Какой срок оплаты?"))


def test_company_name_is_flagged_outside_document_titles(docs):
    reg = frag(docs, "reglament-soglasovaniya-dogovorov-red-01-03-2025#p-1.3")
    flagged = deterministic_issues("Вопрос не относится к документам ООО «Меридиан».", "offtopic", [], "", "", TODAY, "ООО «Меридиан»")
    assert any("организация пользователя" in i for i in flagged)
    title_only = deterministic_issues(f"По документу «{reg.doc.title}» инициатор — руководитель подразделения [S1].", "answer", [reg], "", "", TODAY, "ООО «Меридиан»")
    assert not any("организация пользователя" in i for i in title_only)


def test_other_edition_must_be_mentioned_when_event_date_is_known(docs):
    old = frag(docs, "reglament-soglasovaniya-dogovorov-red-10-01-2023#p-2.4", "S1")
    new = frag(docs, "reglament-soglasovaniya-dogovorov-red-01-03-2025#p-2.4", "S2")
    groups = edition_groups([old, new])
    assert sorted(groups) == [["S1"], ["S2"]]
    assert edition_groups([old]) == []
    one = deterministic_issues("Да, порог 10 000 000 рублей [S1].", "answer", [old, new], "12 млн 10 000 000", "", TODAY, "", groups)
    assert any("другая редакция" in i for i in one)
    both = deterministic_issues("Да, порог 10 000 000 рублей [S1]. В действующей редакции — 15 000 000 [S2].", "answer", [old, new], "12 млн", "", TODAY, "", groups)
    assert not any("другая редакция" in i for i in both)


def test_amendment_is_kept_only_with_the_clause_it_changes(docs):
    ds = frag(docs, "ds-1-k-dogovoru-p-17-2024#abz-5", "S1")
    warranty = frag(docs, "dogovor-p-17-2024#p-5.2", "S2")
    delivery = frag(docs, "dogovor-p-17-2024#p-3.2", "S3")
    assert [f.sid for f in drop_unrelated_amendments([ds, warranty])] == ["S2"]
    assert [f.sid for f in drop_unrelated_amendments([ds, delivery])] == ["S1", "S3"]
    assert [f.sid for f in drop_unrelated_amendments([ds])] == ["S1"]
