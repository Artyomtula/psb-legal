from __future__ import annotations

import re
import sys
import unicodedata
from datetime import date
from functools import lru_cache

_SPACE_RE = re.compile(r"[ \t    ]+")
_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "’": "'", "‘": "'"})
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})

MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_DATE_WORDS_RE = re.compile(r"(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})", re.I)
_DATE_DOTS_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")

ORDINALS = {
    "первая": 1, "вторая": 2, "третья": 3, "четвертая": 4, "пятая": 5, "шестая": 6, "седьмая": 7,
    "восьмая": 8, "девятая": 9, "десятая": 10, "одиннадцатая": 11, "двенадцатая": 12,
    "тринадцатая": 13, "четырнадцатая": 14, "пятнадцатая": 15,
}

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}

STOPWORDS = frozenset(
    "и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот "
    "от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять "
    "уж вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без "
    "будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один "
    "почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после "
    "над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед "
    "иногда лучше чуть том нельзя такой им более всегда конечно всю между весь свой который это мочь нужно "
    "наш ваш также либо".split()
)

_TOKEN_RE = re.compile(r"[0-9a-zа-яё]+(?:[-/.][0-9a-zа-яё]+)*")
_SPLIT_RE = re.compile(r"[-/.]")


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("﻿", "").replace("​", "")
    return "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.split("\n"))


def fold(text: str) -> str:
    return clean(text).translate(_QUOTES).translate(_DASHES).lower().replace("ё", "е")


def norm_id(value: str) -> str:
    value = fold(value).replace("№", "").replace(" ", "")
    value = value.replace("_", "-")
    return re.sub(r"[^0-9a-zа-я/-]", "", value).replace("/", "-")


def slugify(value: str, limit: int = 80) -> str:
    out = []
    for ch in fold(value):
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")
    return slug[:limit].strip("-") or "doc"


def parse_date(text: str) -> date | None:
    m = _DATE_WORDS_RE.search(text)
    d = _DATE_DOTS_RE.search(text)
    candidates = []
    if m:
        candidates.append((m.start(), _safe_date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))))
    if d:
        candidates.append((d.start(), _safe_date(int(d.group(3)), int(d.group(2)), int(d.group(1)))))
    candidates = [c for c in candidates if c[1]]
    return min(candidates)[1] if candidates else None


def find_dates(text: str) -> list[date]:
    found = []
    for m in _DATE_WORDS_RE.finditer(text):
        dt = _safe_date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        if dt:
            found.append((m.start(), dt))
    for m in _DATE_DOTS_RE.finditer(text):
        dt = _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        if dt:
            found.append((m.start(), dt))
    return [dt for _, dt in sorted(found)]


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def fmt_date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else ""


def fmt_date_words(value: date) -> str:
    months = {v: k for k, v in MONTHS.items()}
    return f"{value.day} {months[value.month]} {value.year} г."


class _Lemmatizer:
    def __init__(self) -> None:
        self._morph = None
        try:
            import pymorphy3

            self._morph = pymorphy3.MorphAnalyzer()
        except Exception as exc:
            print(f"pymorphy3 недоступен ({type(exc).__name__}), используется упрощённый стемминг", file=sys.stderr)

    @property
    def available(self) -> bool:
        return self._morph is not None

    def __call__(self, word: str) -> str:
        if self._morph is None:
            return _crude_stem(word)
        return self._morph.parse(word)[0].normal_form.replace("ё", "е")


_SUFFIXES = sorted(
    "ами ями ого ему ому ыми ими ых их ая яя ое ее ые ие ой ей ий ый ом ем ам ям ах ях ов ев ую юю а я о е ы и у ю ь".split(),
    key=len,
    reverse=True,
)


def _crude_stem(word: str) -> str:
    if len(word) <= 4 or not word.isalpha():
        return word
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            return word[: -len(suf)]
    return word


_lemmatizer: _Lemmatizer | None = None


def lemmatizer() -> _Lemmatizer:
    global _lemmatizer
    if _lemmatizer is None:
        _lemmatizer = _Lemmatizer()
    return _lemmatizer


@lru_cache(maxsize=300_000)
def lemma(word: str) -> str:
    if not word.isalpha():
        return word
    return lemmatizer()(word)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(fold(text)):
        raw = raw.strip(".-/")
        if not raw:
            continue
        if _SPLIT_RE.search(raw):
            tokens.append(raw)
            parts = [p for p in _SPLIT_RE.split(raw) if p]
            for p in parts:
                if p not in STOPWORDS and (len(p) > 1 or p.isdigit()):
                    tokens.append(lemma(p))
            continue
        if raw in STOPWORDS:
            continue
        if len(raw) == 1 and not raw.isdigit():
            continue
        tokens.append(lemma(raw))
    return tokens


STEM_LENGTH = 5
_QUOTED_RE = re.compile(r"«([^»]+)»")


def stems(text: str) -> set[str]:
    return {token[:STEM_LENGTH] for token in tokenize(text) if token.isalpha() and len(token) >= STEM_LENGTH - 1}


def quoted_name(name: str) -> str:
    m = _QUOTED_RE.search(name)
    return m.group(1) if m else name
