from __future__ import annotations

from datetime import date

from legal_core.pipeline.numbers import NUM_RE, dates_in, known_numbers, num_forms, spelled_numbers
from legal_core.pipeline.verify import deterministic_issues

TODAY = date(2026, 11, 10)


def _known(*texts: str) -> set[str]:
    return known_numbers(list(texts), TODAY)


def _allowed(number: str, *texts: str) -> bool:
    return bool(num_forms(number) & _known(*texts))


def test_spelled_numbers_are_recognised():
    values, percents = spelled_numbers("штраф от трехсот тысяч до семисот тысяч рублей, повторно — от одного миллиона до полутора миллионов; ставка пятнадцати процентов")
    assert {300_000, 700_000, 1_000_000, 1_500_000, 15} <= values
    assert percents == {15}
    assert _allowed("300 000", "от трехсот тысяч до семисот тысяч рублей")
    assert _allowed("1,5", "до полутора миллионов рублей")


def test_percent_of_amount_and_increase_are_derivable():
    texts = ("ограничен 50% от общей цены договора", "общая цена договора составляет 8 400 000 рублей", "плата 450 000 рублей, увеличение не более чем на 7%")
    assert _allowed("4 200 000", *texts)
    assert _allowed("481 500", *texts)
    assert not _allowed("250 000", *texts)


def test_dates_in_both_notations_and_shifted_by_term():
    assert dates_in("подписана 15 ноября 2024 года и 03.09.2024", 2026) == {date(2024, 11, 15), date(2024, 9, 3)}
    texts = ("спецификация подписана 15 ноября 2024 года", "уведомив не менее чем за 10 календарных дней")
    assert _allowed("15.11", *texts)
    assert _allowed("05.11", *texts)
    assert _allowed("2026", "вопрос без дат")
    assert not _allowed("17.03", *texts)


def test_numbers_check_accepts_calculation_and_flags_invention():
    context = "Какой максимальный размер ответственности? Лимит 50% от цены договора 8 400 000 рублей."
    ok = deterministic_issues("Предел — 4 200 000 рублей [S1].", "answer", [], context, "", TODAY)
    assert not any("не встречаются" in i for i in ok)
    bad = deterministic_issues("Предел — 3 700 000 рублей [S1].", "answer", [], context, "", TODAY)
    assert any("3 700 000" in i for i in bad)


def test_law_reference_from_service_context_is_not_flagged():
    context = "Вопрос\n- Из ТК РФ в базе есть только статьи: 59, 70, 99, 152 — назови их пользователю"
    issues = deterministic_issues("Статьи 81 ТК РФ в базе нет; есть статьи 59, 70, 99 и 152.", "insufficient", [], context + " статья 81", "", TODAY)
    assert not any("не подтверждается" in i or "не встречаются" in i for i in issues)


def test_law_number_is_not_glued_to_preceding_article():
    assert NUM_RE.findall("статьи 11 152-ФЗ") == ["11", "152"]
    assert NUM_RE.findall("цена 8 400 000 рублей") == ["8 400 000"]
