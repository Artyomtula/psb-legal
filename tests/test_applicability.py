from datetime import date

from legal_core.kb.models import Document, Note, Unit
from legal_core.pipeline.analyze import Analysis
from legal_core.pipeline.engine import grade_scores
from legal_core.pipeline.fragments import Fragment, change_applicability, cover_documents, edition_applicability, notes_applicability, pair_editions
from legal_core.pipeline.render import FUTURE_REASON, note_reason

TODAY = date(2026, 11, 12)
DEC_2024 = (date(2024, 12, 1), date(2024, 12, 31))


def _reg(valid_from, valid_to=None, family="reg", doc_id="r") -> Document:
    return Document(doc_id=doc_id, source_name=doc_id, category="company", doc_type="regulation", title="Регламент", short="Регламент", act="", valid_from=valid_from, valid_to=valid_to, family=family)


def test_edition_applicability_by_event_date():
    old = _reg(date(2023, 1, 10), date(2025, 2, 28))
    new = _reg(date(2025, 3, 1))
    assert "применяй её" in edition_applicability(old, DEC_2024, TODAY)
    assert "ещё не действовала" in edition_applicability(new, DEC_2024, TODAY)
    assert "уже утратила силу" in edition_applicability(old, (date(2025, 6, 1), date(2025, 6, 1)), TODAY)
    assert "частично" in edition_applicability(old, (date(2025, 1, 1), date(2025, 12, 31)), TODAY)


def test_edition_applicability_without_event_date():
    assert "утратила силу" in edition_applicability(_reg(date(2023, 1, 10), date(2025, 2, 28)), None, TODAY)
    assert edition_applicability(_reg(date(2025, 3, 1)), None, TODAY) == ""
    assert edition_applicability(_reg(None), None, TODAY) == ""
    assert edition_applicability(_reg(date(2024, 8, 1), family=None), DEC_2024, TODAY) == ""


def test_pair_editions_adds_same_clause_of_other_edition():
    def fr(doc, anchor, sid):
        return Fragment(sid=sid, doc=doc, unit=Unit(anchor=anchor, label=anchor, text="", order=0), text="", score=1.0, origin="t")

    old, new = _reg(date(2023, 1, 10), date(2025, 2, 28), doc_id="old"), _reg(date(2025, 3, 1), doc_id="new")
    picked = [fr(old, "p-2.4", "S1")]
    pool = picked + [fr(new, "p-2.3", "S2"), fr(new, "p-2.4", "S3")]
    assert [f.key for f in pair_editions(picked, pool, 12)] == ["old#p-2.4", "new#p-2.4"]
    assert len(pair_editions(picked, pool, 1)) == 1


def test_change_applicability():
    eff = date(2026, 9, 1)
    assert "редакции на дату события в базе нет" in change_applicability(eff, "changed", (date(2026, 5, 1), date(2026, 5, 31)))
    assert "изменилось" in change_applicability(eff, "changed", (date(2026, 1, 1), date(2026, 12, 31)))
    assert change_applicability(eff, "changed", (date(2026, 10, 1), date(2026, 10, 1))) == ""
    assert "утратило силу" in change_applicability(eff, "repealed", (date(2026, 10, 1), date(2026, 10, 1)))
    assert change_applicability(date(2028, 1, 1), "changed", None) == ""


def test_notes_applicability_skips_info_and_dedupes():
    notes = [
        Note(text="a", kind="changed", effective=date(2026, 9, 1)),
        Note(text="b", kind="changed", effective=date(2026, 9, 1)),
        Note(text="c", kind="info", effective=date(2026, 9, 1)),
    ]
    out = notes_applicability(notes, (date(2026, 5, 1), date(2026, 5, 31)))
    assert len(out) == 1


def test_grade_scores_accepts_both_formats():
    assert grade_scores({"relevance": {"S1": 3, "S2": "2", "[S3]": 9, "S4": "x"}}) == {"S1": 3, "S2": 2, "S3": 3}
    assert grade_scores({"fragments": [{"id": "S1", "relevance": 1}, "junk"]}) == {"S1": 1}
    assert grade_scores({}) == {}


def test_note_reason():
    eff = Note(text="x", kind="changed", effective=date(2026, 9, 1))
    assert note_reason(eff, TODAY, date(2026, 5, 1)).startswith("на дату события")
    assert note_reason(eff, TODAY, None) == "ответ дан по действующей редакции"
    assert note_reason(eff, date(2026, 8, 1), None) == FUTURE_REASON
    assert note_reason(Note(text="x", kind="repealed", effective=date(2026, 9, 1)), TODAY, None) == ""


def test_event_period_requires_explicit_time():
    def period(quote, start, end):
        return Analysis(standalone="", event_from=start, event_to=end, event_quote=quote).period

    aug = (date(2024, 8, 1), date(2024, 8, 31))
    assert period("заключали в августе 2024 года", *aug) == aug
    assert period("в этом году", date(2026, 1, 1), date(2026, 12, 31))
    assert period("А-08/2024", *aug) is None
    assert period("по договору с ООО «Северный Склад»", *aug) is None
    assert period("летом", date(2026, 6, 1), date(2026, 9, 1)) is None
    assert period("в 2024 году", date(2024, 1, 1), date(2026, 11, 10)) is None


def test_cover_documents_adds_best_fragment_of_missing_candidate():
    a, b = _reg(None, doc_id="a"), _reg(None, doc_id="b")
    fa = Fragment(sid="S1", doc=a, unit=Unit(anchor="p-1", label="п. 1", text="", order=0), text="", score=1.0, origin="t", relevance=3)
    fb1 = Fragment(sid="S2", doc=b, unit=Unit(anchor="p-1", label="п. 1", text="", order=0), text="", score=1.0, origin="t", relevance=0)
    fb2 = Fragment(sid="S3", doc=b, unit=Unit(anchor="p-2", label="п. 2", text="", order=1), text="", score=1.0, origin="t", relevance=1)
    assert [f.sid for f in cover_documents([fa], [fa, fb1, fb2], ["a", "b"], 12)] == ["S1", "S3"]
    assert [f.sid for f in cover_documents([fa], [fa, fb1, fb2], ["a", "b"], 1)] == ["S1"]
