from __future__ import annotations

import re
from datetime import date, timedelta

from ..text import MONTHS

NUM_RE = re.compile(r"(?<![\w.])\d+(?:[ \u00a0]\d{3}(?![\d-]))*(?:[.,]\d+)?")
NUM_DATE_RE = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
WORD_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})(?:-?го)?\s+(" + "|".join(MONTHS) + r")(?:\s+(\d{4}))?", re.I)
PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:%|процент)")
WORD_RE = re.compile(r"[а-яё]+|\S")
MIN_AMOUNT = 1000
MAX_DAY_OFFSET = 366
MAX_BASES = 60

_NUMERAL_PATTERNS: tuple[tuple[str, float, bool], ...] = (
    (r"полтора|полутора", 1.5, False),
    (r"од(?:ин|на|но|ного|ной|ному|ним|ну)", 1, False),
    (r"дв(?:а|е|ух|ум|умя)", 2, False),
    (r"тр(?:и|[её]х|[её]м|емя)", 3, False),
    (r"четыр(?:е|[её]х|[её]м|ьмя)", 4, False),
    (r"пят(?:ь|и|ью)", 5, False),
    (r"шест(?:ь|и|ью)", 6, False),
    (r"сем(?:ь|и|ью)", 7, False),
    (r"вос(?:емь|ьми|емью|ьмью)", 8, False),
    (r"девят(?:ь|и|ью)", 9, False),
    (r"десят(?:ь|и|ью)", 10, False),
    (r"одиннадцат(?:ь|и|ью)", 11, False),
    (r"двенадцат(?:ь|и|ью)", 12, False),
    (r"тринадцат(?:ь|и|ью)", 13, False),
    (r"четырнадцат(?:ь|и|ью)", 14, False),
    (r"пятнадцат(?:ь|и|ью)", 15, False),
    (r"шестнадцат(?:ь|и|ью)", 16, False),
    (r"семнадцат(?:ь|и|ью)", 17, False),
    (r"восемнадцат(?:ь|и|ью)", 18, False),
    (r"девятнадцат(?:ь|и|ью)", 19, False),
    (r"двадцат(?:ь|и|ью)", 20, False),
    (r"тридцат(?:ь|и|ью)", 30, False),
    (r"сорока?", 40, False),
    (r"пят(?:ьдесят|идесяти)", 50, False),
    (r"шест(?:ьдесят|идесяти)", 60, False),
    (r"сем(?:ьдесят|идесяти)", 70, False),
    (r"вос(?:емьдесят|ьмидесяти)", 80, False),
    (r"девяност[оа]", 90, False),
    (r"ст[оа]", 100, False),
    (r"двести|двухсот", 200, False),
    (r"триста|тр[её]хсот", 300, False),
    (r"четыреста|четыр[её]хсот", 400, False),
    (r"пят(?:ьсот|исот)", 500, False),
    (r"шест(?:ьсот|исот)", 600, False),
    (r"сем(?:ьсот|исот)", 700, False),
    (r"вос(?:емьсот|ьмисот)", 800, False),
    (r"девят(?:ьсот|исот)", 900, False),
    (r"тысяч(?:а|и|у|е|ей|ам|ами|ах)?", 1_000, True),
    (r"миллион(?:а|ов|у|ом|ам|ами|ах|е)?", 1_000_000, True),
    (r"миллиард(?:а|ов|у|ом|ам|ами|ах|е)?", 1_000_000_000, True),
)
_NUMERALS = tuple((re.compile(pattern), value, multiplier) for pattern, value, multiplier in _NUMERAL_PATTERNS)


def _compact(raw: str) -> str:
    return raw.replace("\u00a0", "").replace(" ", "").replace(",", ".")


def _plain(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{round(value, 2):.2f}".rstrip("0").rstrip(".")


def num_forms(raw: str) -> set[str]:
    compact = _compact(raw)
    forms = {compact}
    try:
        value = float(compact)
    except ValueError:
        return forms
    if value >= MIN_AMOUNT:
        for div in (1_000, 1_000_000, 1_000_000_000):
            if value / div >= 1:
                forms.add(f"{value / div:g}")
    if value.is_integer():
        forms.add(str(int(value)))
    return forms


def numbers_in(text: str) -> set[str]:
    out: set[str] = set()
    for m in NUM_RE.finditer(text):
        out |= num_forms(m.group(0))
    return out


def _numeral(word: str) -> tuple[float, bool] | None:
    for pattern, value, multiplier in _NUMERALS:
        if pattern.fullmatch(word):
            return value, multiplier
    return None


def spelled_numbers(text: str) -> tuple[set[float], set[float]]:
    values: set[float] = set()
    percents: set[float] = set()
    total = group = 0.0
    active = False
    for word in [*WORD_RE.findall(text.lower()), ""]:
        hit = _numeral(word)
        if hit is None:
            if active and total + group:
                values.add(total + group)
                if word.startswith("процент"):
                    percents.add(total + group)
            total = group = 0.0
            active = False
            continue
        value, multiplier = hit
        if multiplier:
            total += (group or 1) * value
            group = 0.0
        else:
            group += value
        active = True
    return values, percents


def dates_in(text: str, default_year: int) -> set[date]:
    found: set[tuple[int, int, int]] = set()
    for m in NUM_DATE_RE.finditer(text):
        found.add((int(m.group(3)), int(m.group(2)), int(m.group(1))))
    for m in WORD_DATE_RE.finditer(text):
        found.add((int(m.group(3)) if m.group(3) else default_year, MONTHS[m.group(2).lower()], int(m.group(1))))
    out: set[date] = set()
    for year, month, day in found:
        try:
            out.add(date(year, month, day))
        except ValueError:
            continue
    return out


def date_forms(d: date) -> set[str]:
    return {str(d.day), f"{d.day:02d}", f"{d.day}.{d.month:02d}", f"{d.day:02d}.{d.month:02d}", f"{d.day}.{d.month}", str(d.year)}


def known_numbers(texts: list[str], today: date) -> set[str]:
    text = "\n".join(texts)
    allowed = numbers_in(text)
    spelled, spelled_percents = spelled_numbers(text)
    for value in spelled:
        allowed |= num_forms(_plain(value))
    digits = [float(_compact(m.group(0))) for m in NUM_RE.finditer(text)]
    amounts = sorted({v for v in [*digits, *spelled] if v >= MIN_AMOUNT}, reverse=True)[:MAX_BASES]
    percents = {float(m.group(1).replace(",", ".")) for m in PERCENT_RE.finditer(text)} | spelled_percents
    for amount in amounts:
        for p in percents:
            if 0 < p <= 100:
                for derived in (amount * p / 100, amount * (1 + p / 100), amount * (1 - p / 100)):
                    allowed |= num_forms(_plain(derived))
    bases = sorted(dates_in(text, today.year) | {today})[-MAX_BASES:]
    offsets = {int(v) for v in digits if v.is_integer() and 1 <= v <= MAX_DAY_OFFSET} | {int(v) for v in spelled if v.is_integer() and 1 <= v <= MAX_DAY_OFFSET}
    for base in bases:
        allowed |= date_forms(base)
        for days in offsets:
            allowed |= date_forms(base + timedelta(days=days)) | date_forms(base - timedelta(days=days))
    return allowed
