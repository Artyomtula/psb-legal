from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import PurePosixPath

from ..text import ORDINALS, clean, fold, fmt_date, norm_id, parse_date, slugify
from .loader import RawDoc
from .models import Document, Note, Relation, Unit

CODES = [
    ("гражданский процессуальный кодекс", "ГПК РФ", "Гражданский процессуальный кодекс Российской Федерации", "часть", ["гпк"]),
    ("арбитражный процессуальный кодекс", "АПК РФ", "Арбитражный процессуальный кодекс Российской Федерации", "часть", ["апк"]),
    ("кодекс административного судопроизводства", "КАС РФ", "Кодекс административного судопроизводства Российской Федерации", "часть", ["кас"]),
    ("кодекс российской федерации об административных правонарушениях", "КоАП РФ", "Кодекс Российской Федерации об административных правонарушениях", "часть", ["коап", "административный кодекс"]),
    ("налоговый кодекс", "НК РФ", "Налоговый кодекс Российской Федерации", "пункт", ["нк", "налоговый кодекс"]),
    ("трудовой кодекс", "ТК РФ", "Трудовой кодекс Российской Федерации", "часть", ["тк", "трудовой кодекс"]),
    ("гражданский кодекс", "ГК РФ", "Гражданский кодекс Российской Федерации", "пункт", ["гк", "гражданский кодекс"]),
    ("уголовный кодекс", "УК РФ", "Уголовный кодекс Российской Федерации", "часть", ["ук", "уголовный кодекс"]),
    ("бюджетный кодекс", "БК РФ", "Бюджетный кодекс Российской Федерации", "пункт", ["бк", "бюджетный кодекс"]),
    ("жилищный кодекс", "ЖК РФ", "Жилищный кодекс Российской Федерации", "часть", ["жк", "жилищный кодекс"]),
    ("земельный кодекс", "ЗК РФ", "Земельный кодекс Российской Федерации", "пункт", ["зк", "земельный кодекс"]),
    ("семейный кодекс", "СК РФ", "Семейный кодекс Российской Федерации", "пункт", ["ск", "семейный кодекс"]),
]

_NUM = r"(?:N|№)\s*"
_DATE_ANY = r"(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}\s+[а-яё]+\s+\d{4}\s*г?\.?)"
FZ_RE = re.compile(rf"Федеральн\w*\s+закон\w*\s+от\s+(?P<date>{_DATE_ANY})\s+{_NUM}(?P<no>[\d.-]+-ФЗ)\s*\"(?P<title>[^\"]+)\"", re.I)
LAW_RF_RE = re.compile(rf"Закон\s+(?:РФ|Российской\s+Федерации)\s+от\s+(?P<date>{_DATE_ANY})\s+{_NUM}(?P<no>[\d-]+)\s*\"(?P<title>[^\"]+)\"", re.I)
CB_RE = re.compile(rf"(?P<kind>Положение|Указание|Инструкция)\s+Банка\s+России\s+от\s+(?P<date>{_DATE_ANY})\s+{_NUM}(?P<no>[\d-]+-[ПУИ])(?:\s*\"(?P<title>[^\"]+)\")?", re.I)
GOV_RE = re.compile(rf"Постановление\s+Правительства\s+(?:РФ|Российской\s+Федерации)\s+от\s+(?P<date>{_DATE_ANY})\s+{_NUM}(?P<no>[\d-]+)(?:\s*\"(?P<title>[^\"]+)\")?", re.I)
CODE_NO_RE = re.compile(rf"от\s+(?P<date>\d{{1,2}}\.\d{{1,2}}\.\d{{4}})\s+{_NUM}(?P<no>[\d.-]+-ФЗ)", re.I)

ARTICLE_RE = re.compile(r"^Статья\s+(\d+(?:\.\d+)*(?:-\d+)?)\.(?:\s+(?!\d)(.*))?$")
TOP_RE = re.compile(r"^(\d+(?:\.\d+)*(?:-\d+)?)\.\s+(\S.*)$")
SUB_RE = re.compile(r"^(\d+(?:\.\d+)?(?:-\d+)?)\)\s*(.*)$")
LETTER_RE = re.compile(r"^([а-я])\)\s+(.+)$")
PRIM_RE = re.compile(r"^Примечани[ея]\s*[.:]?\s*(.*)$")
PART_REPEALED_RE = re.compile(r"^Часть\s+([а-яё]+)\s+утратила\s+силу", re.I)
REPEALED_ITEM_RE = re.compile(r"^(?:\d+(?:\.\d+)*(?:-\d+)?[.)]\s*)?(?:утратил[аио]?\s+силу|исключен[аоы]?\b)", re.I)

_NOTE_SUBJ = r"(?:Статья|Статьи|Часть|Части|Пункт|Пункты|Подпункт|Подпункты|Абзац|Абзацы|Примечани\w+|Наименование|Глава|Раздел|Приложение)"
_NOTE_VERB = r"(?:изменен\w*|дополнен\w*|утратил\w*\s+силу|признан\w*\s+утративш\w+|введен\w*|исключен\w*|приостановлен\w*|изложен\w*)"
NOTE_RE = re.compile(
    "|".join(
        [
            rf"^{_NOTE_SUBJ}\b(?:[^.]|\.(?=\d)){{0,160}}?\b{_NOTE_VERB}\b.*(?:Федеральн\w*\s+закон|\d{{4}}\s*г)",
            r"^Федеральн\w*\s+закон\w*\s+от\s",
            r"^См\.\s",
            r"^Информация\s+об\s+изменениях",
            r"^О\s.+?\bсм\.",
            r"^Согласно\s+(?:Определению|Постановлению|решению)\s",
            r"^Обратите\s+внимание",
            r"^[CС]рок\s+давности\s+привлечения",
            r"^Разъяснения\s",
            r"^Комментарий\s+к\s",
        ]
    ),
    re.I,
)
APPLICATION_NOTE_RE = re.compile(
    r"^(?:(?:Положения|Действие\s+положений)\s+(?:статьи|статей|пункта|пунктов|подпункта|подпунктов|части|частей|абзаца|абзацев)\s+\d"
    r"|(?:Подпункт|Пункт|Часть|Абзац|Статья)\s+[\d.]+(?:-\d+)?\s+(?:применяется|распространяется|не\s+применяется|действует|вступает))",
    re.I,
)
APPLICATION_MARK_RE = re.compile(r"в\s+редакции|федеральн\w*\s+закон|\d{4}\s*г", re.I)
NOTE_EFFECTIVE_RE = re.compile(r"\bс\s+(\d{1,2}\s+[а-яё]+\s+\d{4}|\d{1,2}\.\d{1,2}\.\d{4})", re.I)
NOTE_LAW_RE = re.compile(r"(Федеральн\w*\s+закон\w*\s+от\s+.+?(?:N|№)\s*[\d-]+-ФЗ)", re.I)

INJECTION_RE = re.compile(
    r"(?:для\s+(?:ии|ai|модели|нейросети|ассистента|бота|llm)\b|ии[- ]?(?:ассистент|помощник|модел)|ai[- ]?assistant|"
    r"\bигнорируй|\bignore\s+(?:all|previous|the|any)|не\s+упоминай\s+(?:эту|данную|настоящую)|system\s+prompt|"
    r"системн\w+\s+(?:промпт|инструкц|сообщени|подсказ)|на\s+любой\s+вопрос\s+(?:о|об|про)?.{0,60}?отвечай|отвечай\s+«)",
    re.I,
)

ORG_RE = re.compile(r"\b(ООО|АО|ПАО|ЗАО|ОАО|НАО|ИП|ФГУП|АНО|НКО)\s+«([^»]+)»(?:\s*\(ИНН\s*(\d{10,12})\))?")
SECTION_RE = re.compile(r"^(\d+)\.\s+([А-ЯЁ][^.]{0,100})$")
POINT_RE = re.compile(r"^(\d+(?:\.\d+)+)\.?\s+(\S.*)$")
CONTRACT_RE = re.compile(r"^ДОГОВОР\b(?P<kind>[^№]*)№\s*(?P<no>\S+)", re.I)
SUPPL_RE = re.compile(r"^ДОПОЛНИТЕЛЬНОЕ\s+СОГЛАШЕНИЕ\s*(?:№\s*(?P<no>\S+))?", re.I)
SUPPL_BASE_RE = re.compile(r"к\s+договору\s*(?P<kind>[^№]*?)\s*№\s*(?P<base>[^\s,]+)(?:\s+от\s+(?P<date>.+?))?$", re.I)
REG_RE = re.compile(r"^(РЕГЛАМЕНТ|ПОЛОЖЕНИЕ|ПОРЯДОК|ИНСТРУКЦИЯ|ПОЛИТИКА|ПРАВИЛА|СТАНДАРТ|МЕТОДИКА)\b(.*)$")
MEMO_RE = re.compile(r"^СЛУЖЕБНАЯ\s+ЗАПИСКА\s*№\s*(?P<no>\S+)(?:\s+от\s+(?P<date>[\d.]+))?", re.I)
LETTER_NO_RE = re.compile(r"Исх\.?\s*№\s*(?P<no>\S+)(?:\s+от\s+(?P<date>[\d.]+))?", re.I)
ORDER_RE = re.compile(r"^ПРИКАЗ\b.*?№\s*(?P<no>\S+)", re.I)
LOST_FORCE_RE = re.compile(r"утратил\w*\s+силу\s+с\s+(\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}\s+[а-яё]+\s+\d{4})", re.I)
EDITION_RE = re.compile(r"Редакция\s+от\s+(\d{1,2}\.\d{1,2}\.\d{4})", re.I)
ACTIVE_FROM_RE = re.compile(r"Действует\s+с\s+(\d{1,2}\.\d{1,2}\.\d{4})", re.I)
APPROVED_RE = re.compile(r"Утвержд\w*\s+(.+?)\s*№\s*(\S+)\s+от\s+(\d{1,2}\.\d{1,2}\.\d{4})", re.I)
AMENDS_POINT_RE = re.compile(r"пункт\w*\s+(\d+(?:\.\d+)+)\s+договора", re.I)
REF_POINT_RE = re.compile(r"(?:п\.|пункт\w*)\s*(\d+(?:\.\d+)+)\s+(Регламент\w*|Положени\w*|Порядк\w*|Инструкци\w*|Политик\w*)\s+([^()]+?)\s*(?:\(\s*ред\w*\.?\s+от\s+(\d{1,2}\.\d{1,2}\.\d{4})\s*\))?(?=[,.;)]|\s+до\s|$)", re.I)


def _lines(text: str) -> list[str]:
    return [line for line in clean(text).split("\n") if line.strip()]


def is_note(line: str) -> bool:
    if NOTE_RE.search(line):
        return True
    if APPLICATION_NOTE_RE.search(line):
        if re.search(r"\(в\s+редакции\s+федеральн", line, re.I):
            return True
        return "настоящ" not in fold(line[:90]) and bool(APPLICATION_MARK_RE.search(line))
    return False


def make_note(line: str) -> Note:
    low = fold(line)
    if "утратил" in low or "признан" in low or "исключен" in low:
        kind = "repealed"
    elif "дополнен" in low or "введен" in low:
        kind = "added"
    elif "изменен" in low or "изложен" in low or "внесены изменения" in low:
        kind = "changed"
    else:
        kind = "info"
    m = NOTE_EFFECTIVE_RE.search(line)
    eff = parse_date(m.group(1)) if m else None
    law = NOTE_LAW_RE.search(line)
    return Note(text=line, kind=kind, effective=eff, law_ref=law.group(1) if law else None)


def is_injection(text: str) -> bool:
    return bool(INJECTION_RE.search(fold(text)))


_ORG_FORM_RE = re.compile(r"\b(ооо|оао|зао|пао|нао|ао|ип|фгуп|ано|нко|рф)\b")


def _sentence_case(text: str) -> str:
    low = text.lower()
    out = low[:1].upper() + low[1:]
    out = _ORG_FORM_RE.sub(lambda m: m.group(1).upper(), out)
    return re.sub(r"«([^»]+)»", lambda m: "«" + " ".join(w[:1].upper() + w[1:] for w in m.group(1).split()) + "»", out)


@dataclass
class _Node:
    unit: Unit
    level: int
    first: str
    children: int = 0


@dataclass
class _BodyState:
    units: list[Unit] = field(default_factory=list)
    stack: list[_Node] = field(default_factory=list)
    pending: list[Note] = field(default_factory=list)
    part_no: int = 0
    prim: bool = False
    prim_no: int = 0


class LawBodyParser:
    def __init__(self, unit_term: str, numbered: bool) -> None:
        self.term = unit_term
        self.numbered = numbered
        self.s = _BodyState()

    def _flush(self, unit: Unit) -> None:
        if self.s.pending:
            unit.notes.extend(self.s.pending)
            self.s.pending = []

    def _add(self, anchor: str, label: str, text: str, level: int, kind: str) -> Unit:
        while self.s.stack and self.s.stack[-1].level >= level:
            self.s.stack.pop()
        lead = " / ".join(n.first for n in self.s.stack)[:400]
        unit = Unit(anchor=anchor, label=label, text=text, order=len(self.s.units), lead=lead, kind=kind)
        unit.repealed = bool(REPEALED_ITEM_RE.search(text)) or bool(PART_REPEALED_RE.search(text))
        self._flush(unit)
        if self.s.stack:
            self.s.stack[-1].children += 1
        self.s.units.append(unit)
        self.s.stack.append(_Node(unit, level, text[:300]))
        return unit

    def _top(self) -> _Node | None:
        for node in self.s.stack:
            if node.level == 1:
                return node
        return None

    def _top_labels(self) -> tuple[str, str]:
        top = self._top()
        if not top:
            return "", ""
        return top.unit.anchor, top.unit.label

    def _continue(self, line: str) -> None:
        if not self.s.stack:
            self._new_part(line)
            return
        node = self.s.stack[-1]
        self._flush(node.unit)
        node.unit.text += "\n" + line

    def _new_part(self, line: str, number: int | None = None) -> None:
        self.s.part_no = number if number else self.s.part_no + 1
        n = self.s.part_no
        if self.term == "часть":
            self._add(f"ch-{n}", f"ч. {n}", line, 1, "part")
        else:
            self._add(f"abz-{n}", f"абз. {n}", line, 1, "paragraph")

    def feed(self, line: str) -> None:
        if is_note(line):
            self.s.pending.append(make_note(line))
            return
        pm = PRIM_RE.match(line)
        if pm and len(line) < 400:
            self.s.prim = True
            rest = pm.group(1).strip()
            if rest:
                self.s.prim_no += 1
                self._add(f"prim-{self.s.prim_no}", "примечание", rest, 1, "note")
            return
        tm = TOP_RE.match(line)
        if tm and (self.numbered or self.s.prim):
            num, text = tm.group(1), line
            if self.s.prim:
                self._add(f"prim-{num}", f"примечание {num}", text, 1, "note")
            elif self.term == "часть":
                self._add(f"ch-{num}", f"ч. {num}", text, 1, "part")
            else:
                self._add(f"p-{num}", f"п. {num}", text, 1, "point")
            return
        sm = SUB_RE.match(line)
        if sm:
            num = sm.group(1)
            top_anchor, top_label = self._top_labels()
            if self.term == "часть":
                anchor = f"{top_anchor}-p-{num}" if top_anchor else f"p-{num}"
                label = f"п. {num} {top_label}".strip()
            else:
                anchor = f"{top_anchor}-pp-{num}" if top_anchor else f"pp-{num}"
                label = f"пп. {num} {top_label}".strip()
            self._add(anchor, label, line, 2, "subpoint")
            return
        lm = LETTER_RE.match(line)
        if lm and self.s.stack:
            parent = next((n for n in reversed(self.s.stack) if n.level <= 2), None)
            if parent:
                letter = lm.group(1)
                self._add(f"{parent.unit.anchor}-{letter}", f"подп. «{letter}» {parent.unit.label}", line, 3, "letter")
                return
        if self.s.prim:
            if not self.s.stack or self.s.stack[-1].unit.kind != "note":
                self.s.prim_no += 1
                self._add(f"prim-{self.s.prim_no}", "примечание", line, 1, "note")
            else:
                self._continue(line)
            return
        if not self.numbered:
            rm = PART_REPEALED_RE.match(line)
            if rm and fold(rm.group(1)) in ORDINALS:
                self._new_part(line, ORDINALS[fold(rm.group(1))])
                return
            if line[:1].isupper() or line[:1].isdigit() or line[:1] in "\"«(":
                self._new_part(line)
                return
        self._continue(line)

    def finish(self) -> tuple[list[Unit], list[Note]]:
        leftover = self.s.pending
        if leftover and self.s.units:
            self.s.units[-1].notes.extend(leftover)
            leftover = []
        return self.s.units, leftover


def _detect_act(source: str, fallback_name: str) -> dict:
    low = fold(source)
    for key, short, full, term, aliases in CODES:
        if key in low:
            m = CODE_NO_RE.search(source)
            return {
                "short": short, "full": full, "term": term, "kind": "code",
                "number": m.group("no") if m else None, "date": parse_date(m.group("date")) if m else None,
                "aliases": aliases + [fold(short), key],
            }
    for rx, kind, term in ((FZ_RE, "federal_law", "часть"), (LAW_RF_RE, "law_rf", "часть"), (CB_RE, "cb_act", "пункт"), (GOV_RE, "gov_decree", "пункт")):
        m = rx.search(source)
        if not m:
            continue
        no = m.group("no")
        title = (m.groupdict().get("title") or "").strip()
        dt = parse_date(m.group("date"))
        if kind == "federal_law":
            short = no
            full = f"Федеральный закон от {fmt_date(dt)} № {no} «{title}»"
            digits = no.split("-")[0]
            aliases = [fold(no), f"{digits} фз", f"фз-{digits}", f"фз {digits}", f"закон {digits}-фз", f"федеральный закон {digits}"]
        elif kind == "law_rf":
            short = f"Закон РФ № {no}"
            full = f"Закон РФ от {fmt_date(dt)} № {no} «{title}»"
            aliases = [fold(no), f"закон рф {no}"]
        elif kind == "cb_act":
            k = m.group("kind").capitalize()
            short = f"{k} Банка России № {no}"
            full = f"{k} Банка России от {fmt_date(dt)} № {no}" + (f" «{title}»" if title else "")
            aliases = [fold(no), f"{fold(k)} {fold(no)}", f"{fold(k)} цб {fold(no)}", f"{fold(k)} банка россии {fold(no)}"]
        else:
            short = f"Постановление Правительства РФ № {no}"
            full = f"Постановление Правительства РФ от {fmt_date(dt)} № {no}" + (f" «{title}»" if title else "")
            aliases = [f"постановление {no}", f"постановление правительства {no}"]
        if title:
            aliases += [fold(title), "закон " + fold(title)]
        return {"short": short, "full": full, "term": term, "kind": kind, "number": no, "date": dt, "aliases": aliases, "title": title}
    stem = PurePosixPath(fallback_name).stem
    return {"short": stem, "full": stem, "term": "пункт", "kind": "other_act", "number": None, "date": None, "aliases": [fold(stem)]}


def _split_articles(lines: list[str]) -> list[tuple[str | None, str | None, list[str], list[str]]]:
    chunks: list[tuple[str | None, str | None, list[str], list[str]]] = []
    pre: list[str] = []
    cur: tuple[str | None, str | None, list[str], list[str]] | None = None
    for line in lines:
        m = None if is_note(line) else ARTICLE_RE.match(line)
        if m:
            if cur:
                body = cur[3]
                while body and is_note(body[-1]):
                    pre.insert(0, body.pop())
                chunks.append(cur)
            cur = (m.group(1), (m.group(2) or "").strip(), pre, [])
            pre = []
        elif cur is None:
            pre.append(line)
        else:
            cur[3].append(line)
    if cur:
        chunks.append(cur)
    elif pre:
        chunks.append((None, None, [], pre))
    return chunks


def parse_law(raw: RawDoc) -> list[Document]:
    lines = _lines(raw.text)
    meta: dict[str, str] = {}
    body_start = 0
    for i, line in enumerate(lines[:6]):
        for key, prefix in (("source", "Источник:"), ("url", "URL:"), ("exported", "Дата выгрузки:")):
            if line.startswith(prefix):
                meta[key] = line[len(prefix):].strip()
                body_start = i + 1
    source = meta.get("source", lines[0] if lines else raw.name)
    act = _detect_act(source, raw.name)
    body = lines[body_start:]
    docs = []
    for number, heading_title, pre_notes_lines, art_lines in _split_articles(body):
        doc_notes = [make_note(l) for l in pre_notes_lines if is_note(l)]
        extra = [l for l in pre_notes_lines if not is_note(l)]
        art_title = heading_title or ""
        first_text: list[str] = []
        if art_title and number:
            src_low = fold(source)
            looks_title = fold(art_title)[:60] in src_low or (not meta.get("source") and len(art_title) < 150 and not art_title.endswith("."))
            if not looks_title:
                first_text = [art_title]
                art_title = ""
        content = extra + first_text + art_lines
        numbered = any(TOP_RE.match(l) and not is_note(l) for l in content)
        parser = LawBodyParser(act["term"], numbered)
        for line in content:
            parser.feed(line)
        units, leftover = parser.finish()
        doc_notes.extend(leftover)
        art_label = f", ст. {number}" if number else ""
        short = f"{act['short']}{art_label}"
        title = act["full"] + (f", статья {number}" if number else "") + (f". {art_title}" if art_title else "")
        doc = Document(
            doc_id="",
            source_name=raw.name,
            category="law",
            doc_type=act["kind"],
            title=title,
            short=short,
            act=act["short"],
            act_full=act["full"],
            number=act["number"],
            doc_date=act["date"],
            article=number,
            article_title=art_title or None,
            source_url=meta.get("url"),
            exported=parse_date(meta.get("exported", "")),
            unit_term=act["term"],
            header=[f"Источник: {source}"] + ([f"URL: {meta['url']}"] if meta.get("url") else []),
            units=units,
            notes=doc_notes,
            aliases=sorted(set(act["aliases"])),
            raw_text=raw.text,
        )
        doc.doc_id = slugify(act["short"]) + (f"-st-{slugify(number)}" if number else "")
        docs.append(doc)
    return docs


def _orgs(lines: list[str]) -> list[tuple[str, str, str | None]]:
    found = []
    for line in lines:
        for m in ORG_RE.finditer(line):
            found.append((m.group(1), m.group(2), m.group(3)))
    return found


def _paragraph_units(lines: list[str], start_order: int = 0) -> list[Unit]:
    units = []
    for i, line in enumerate(lines, 1):
        units.append(Unit(anchor=f"abz-{i}", label="", text=line, order=start_order + i - 1, kind="paragraph"))
    return units


def _structured_units(lines: list[str]) -> tuple[list[str], list[Unit]]:
    header: list[str] = []
    units: list[Unit] = []
    section = ""
    preamble: list[str] = []
    started = False
    for line in lines:
        sm = SECTION_RE.match(line)
        if sm and not POINT_RE.match(line):
            section = line
            started = True
            continue
        pm = POINT_RE.match(line)
        if pm:
            started = True
            units.append(Unit(anchor=f"p-{pm.group(1)}", label=f"п. {pm.group(1)}", text=line, order=len(units), heading=section, kind="point"))
            continue
        if not started:
            preamble.append(line)
            continue
        if units and units[-1].heading == section:
            units[-1].text += "\n" + line
        else:
            units.append(Unit(anchor=f"s-{slugify(section or 'text', 24)}-{len(units)}", label="", text=line, order=len(units), heading=section, kind="paragraph"))
    return preamble, units


STATUS_LINE_RE = re.compile(r"^(?:РЕДАКЦИЯ|ДОКУМЕНТ|ДОГОВОР|РЕГЛАМЕНТ|ПОЛОЖЕНИЕ)?\s*(?:УТРАТИЛ\w*\s+СИЛУ|НЕ\s+ДЕЙСТВУЕТ|ДЕЙСТВУЮЩАЯ\s+РЕДАКЦИЯ)", re.I)


def parse_company(raw: RawDoc) -> Document:
    lines = _lines(raw.text)
    status_lines = [l for l in lines[:2] if STATUS_LINE_RE.match(l)]
    body = [l for l in lines if l not in status_lines]
    doc = Document(doc_id="", source_name=raw.name, category="company", doc_type="other", title="", short="", act="", raw_text=raw.text)
    for line in status_lines:
        lost = LOST_FORCE_RE.search(line)
        lost_date = parse_date(lost.group(1)) if lost else None
        if lost_date:
            doc.valid_to = lost_date - timedelta(days=1)
    head = body[:8]
    head_text = "\n".join(head)
    ed = EDITION_RE.search(head_text)
    if ed:
        doc.edition = ed.group(1)
    af = ACTIVE_FROM_RE.search(head_text)
    if af:
        doc.valid_from = parse_date(af.group(1))
    elif ed:
        doc.valid_from = parse_date(ed.group(1))
    orgs = _orgs(body[:12])
    first = body[0] if body else raw.name

    cm = CONTRACT_RE.match(first)
    sm = SUPPL_RE.match(first)
    rm = REG_RE.match(first) if first.isupper() else None
    mm = next((MEMO_RE.search(l) for l in head if MEMO_RE.search(l)), None)
    lm = next((LETTER_NO_RE.search(l) for l in head if LETTER_NO_RE.search(l)), None)
    om = ORDER_RE.match(first)
    subject_line = next((l for l in head if re.match(r"^Об?\s", l)), "")

    if cm:
        kind = cm.group("kind").strip().lower()
        no = cm.group("no").strip()
        doc.doc_type = "contract"
        doc.number = no
        doc.doc_date = parse_date("\n".join(body[1:3]))
        doc.valid_from = doc.doc_date
        doc.act = f"Договор {kind} № {no}".replace("  ", " ")
        doc.short = f"договор № {no}"
        preamble, units = _structured_units(body[1:])
        doc.header = [first] + preamble[:1]
        doc.units = [Unit(anchor="preamble", label="преамбула", text="\n".join(preamble), order=0, kind="preamble")] + units
        subj = doc.unit("p-1.1")
        doc.subject = subj.text[5:].strip()[:220] if subj else None
    elif sm:
        doc.doc_type = "supplement"
        no = (sm.group("no") or "").strip()
        doc.number = no
        base_line = next((l for l in body[1:4] if SUPPL_BASE_RE.search(l)), "")
        bm = SUPPL_BASE_RE.search(base_line)
        doc.doc_date = parse_date("\n".join(l for l in body[1:4] if l != base_line))
        doc.valid_from = doc.doc_date
        base = bm.group("base") if bm else ""
        doc.act = f"Дополнительное соглашение № {no} к договору № {base}"
        doc.short = f"доп. соглашение № {no} к договору № {base}"
        doc.header = [first, base_line]
        rest = [l for l in body[1:] if l != base_line]
        doc.units = _paragraph_units(rest)
        rel = Relation(kind="amends", target_doc=f"number:{base}", effective=doc.doc_date, text=base_line)
        for u in doc.units:
            for m in AMENDS_POINT_RE.finditer(u.text):
                rel.target_anchors.append(f"p-{m.group(1)}")
            if re.search(r"применя\w+\s+к\s", u.text, re.I):
                rel.condition = (rel.condition + " " + u.text).strip()
        doc.relations.append(rel)
    elif rm:
        doc.doc_type = "regulation"
        title = _sentence_case(first)
        doc.act = title
        appr = APPROVED_RE.search(head_text)
        if appr:
            doc.doc_date = parse_date(appr.group(3))
        ed_txt = f" (ред. от {doc.edition})" if doc.edition else ""
        base_title = re.sub(r"\s+ООО\s+«.+»$", "", title)
        doc.short = f"{base_title}{ed_txt}"
        doc.family = slugify(base_title)
        preamble, units = _structured_units(body[1:])
        doc.header = [first] + preamble
        doc.units = ([Unit(anchor="header", label="", text="\n".join(preamble), order=0, kind="preamble")] if preamble else []) + units
    elif mm:
        doc.doc_type = "memo"
        doc.number = mm.group("no")
        doc.doc_date = parse_date(mm.group("date") or "")
        doc.valid_from = doc.doc_date
        doc.subject = subject_line or None
        doc.short = f"служебная записка № {doc.number}" + (f" от {fmt_date(doc.doc_date)}" if doc.doc_date else "")
        doc.act = doc.short[:1].upper() + doc.short[1:]
        hdr_end = body.index(subject_line) + 1 if subject_line in body else 2
        doc.header = body[:hdr_end]
        doc.units = _paragraph_units(body[hdr_end:])
    elif lm:
        doc.doc_type = "letter"
        doc.number = lm.group("no")
        doc.doc_date = parse_date(lm.group("date") or "")
        doc.valid_from = doc.doc_date
        doc.subject = subject_line or None
        doc.short = f"письмо № {doc.number}" + (f" от {fmt_date(doc.doc_date)}" if doc.doc_date else "")
        doc.act = doc.short[:1].upper() + doc.short[1:]
        hdr_end = body.index(subject_line) + 1 if subject_line in body else 3
        doc.header = body[:hdr_end]
        doc.units = _paragraph_units(body[hdr_end:])
    elif om:
        doc.doc_type = "order"
        doc.number = om.group("no")
        doc.doc_date = parse_date("\n".join(body[:3]))
        doc.short = f"приказ № {doc.number}"
        doc.act = doc.short.capitalize()
        preamble, units = _structured_units(body[1:])
        doc.header = [first] + preamble
        doc.units = units or _paragraph_units(body[1:])
    else:
        doc.doc_type = "other"
        doc.short = PurePosixPath(raw.name).stem.split("__")[-1]
        doc.act = doc.short
        doc.doc_date = parse_date("\n".join(body[:4]))
        preamble, units = _structured_units(body[1:])
        doc.header = [first]
        doc.units = units if units else _paragraph_units(body[1:])

    doc.unit_term = "пункт"
    doc.header = status_lines + doc.header
    for i, u in enumerate(doc.units):
        u.order = i
        u.injection = is_injection(u.text)
    parties: dict[str, dict] = {}
    for form, name, inn in orgs:
        party = parties.setdefault(name, {"form": form, "name": name, "inn": inn})
        if inn and not party["inn"]:
            party["inn"] = inn
    doc.parties = list(parties.values())
    extra = [l for l in head[:3] if not ORG_RE.search(l) and len(l) < 60 and not re.search(r"\d", l) and not l.isupper()]
    doc.aliases = [fold(l) for l in extra]
    return doc


def _is_law(raw: RawDoc) -> bool:
    name = fold(raw.name)
    head = fold(raw.text[:600])
    if "base.garant.ru" in head or head.startswith("источник:"):
        return True
    if name.startswith(("законы", "закон", "нпа", "law")) or "/законы" in name:
        return True
    return bool(re.search(r"^(федеральный закон|.*кодекс российской федерации|положение банка россии|указание банка россии)", head))


_ORD_WORD = "|".join(w[:-2] + r"\w*" for w in ORDINALS)
LAW_REF_RE = re.compile(
    r"(?:(?P<kind>пункт\w*|подпункт\w*|част\w*)\s+(?P<num>\d+(?:\.\d+)*(?:-\d+)?|" + _ORD_WORD + r")"
    r"(?:\s*(?:-|и|,)\s*(?P<num2>\d+(?:\.\d+)*))?\s+)?"
    r"(?:(?P<self>настоящей\s+статьи)|стать\w*\s+(?P<art>\d+(?:\.\d+)*(?:-\d+)?)(?!\s*(?:\d|Федерального|Закона|закона)))",
    re.I,
)


def _ordinal_num(token: str) -> str | None:
    if token.isdigit() or re.match(r"^\d", token):
        return token
    low = fold(token)
    for word, n in ORDINALS.items():
        if low.startswith(word[:-2]):
            return str(n)
    return None


def link_law_references(docs: list[Document]) -> None:
    by_act_article: dict[tuple[str, str], Document] = {(d.act, d.article): d for d in docs if d.category == "law" and d.article}
    for d in docs:
        if d.category != "law":
            continue
        for u in d.units:
            for m in LAW_REF_RE.finditer(u.text):
                target = d if m.group("self") else by_act_article.get((d.act, m.group("art") or ""))
                if target is None:
                    continue
                anchors: list[str] = []
                kind = fold(m.group("kind") or "")
                nums = [n for n in (_ordinal_num(m.group("num") or ""), m.group("num2")) if n]
                for n in nums:
                    if kind.startswith("част") or (kind.startswith("пункт") and target.unit_term == "часть"):
                        cand = f"ch-{n}" if kind.startswith("част") else None
                    else:
                        cand = f"p-{n}"
                    if cand and target.unit(cand):
                        anchors.append(cand)
                if target is d and (not anchors or anchors == [u.anchor]):
                    continue
                d.relations.append(Relation(kind="refers", target_doc=target.doc_id, target_anchors=anchors, source_anchor=u.anchor, text=m.group(0)[:120]))


def _contract_aliases(no: str) -> list[str]:
    n = fold(no)
    base = {n, n.replace("/", "-"), n.replace("-", ""), n.replace("/", "-").replace("-", "")}
    head = re.split(r"[/]", n)[0]
    base.add(head)
    return sorted(base)


def link(docs: list[Document]) -> list[Document]:
    org_counter: Counter[str] = Counter()
    for d in docs:
        for p in d.parties:
            org_counter[p["name"]] += 1
    our = org_counter.most_common(1)[0][0] if org_counter else None

    used: dict[str, int] = {}
    for d in docs:
        if d.category == "company":
            own = next((p for p in d.parties if p["name"] == our), None)
            if own:
                d.our_party = f"{own['form']} «{own['name']}»"
            others = [p for p in d.parties if p["name"] != our]
            if others and d.doc_type in ("contract", "supplement"):
                d.counterparty = f"{others[0]['form']} «{others[0]['name']}»"
                d.counterparty_inn = others[0]["inn"]
            aliases = list(d.aliases)
            if d.doc_type == "contract":
                kind = d.act.split("№")[0].replace("Договор", "").strip()
                d.title = f"Договор {kind} № {d.number} от {fmt_date(d.doc_date)}" + (f" с {d.counterparty}" if d.counterparty else "")
                d.doc_id = "dogovor-" + slugify(d.number or d.source_name)
                aliases += _contract_aliases(d.number or "") + [f"договор {kind}"]
                if others:
                    aliases.append(fold(others[0]["name"]))
            elif d.doc_type == "supplement":
                d.title = f"{d.act} от {fmt_date(d.doc_date)}" + (f" ({d.counterparty})" if d.counterparty else "")
                base = d.act.split("№")[-1].strip()
                d.doc_id = f"ds-{slugify(d.number or '1')}-k-dogovoru-{slugify(base)}"
                aliases += [f"доп соглашение {fold(d.number or '')} к договору {fold(base)}", f"дс {fold(d.number or '')}", "дополнительное соглашение"]
            elif d.doc_type == "regulation":
                d.title = d.act + (f" (ред. от {d.edition})" if d.edition else "")
                d.doc_id = slugify(" ".join(d.act.split()[:3])) + (f"-red-{d.edition.replace('.', '-')}" if d.edition else "")
                aliases += [fold(d.short.split(" (")[0]), fold(d.act.split()[0])]
            elif d.doc_type in ("memo", "letter", "order"):
                d.title = d.act + (f" «{d.subject}»" if d.subject else "")
                prefix = {"memo": "sluzhebnaya-zapiska", "letter": "pismo", "order": "prikaz"}[d.doc_type]
                d.doc_id = f"{prefix}-{slugify(d.number or d.source_name)}"
                aliases += [fold(d.number or ""), fold(d.short)]
            else:
                d.title = d.act
                d.doc_id = slugify(d.act)
            d.aliases = sorted({a.strip() for a in aliases if a and a.strip()})
        else:
            d.title = d.title or d.short
        base_id = d.doc_id or slugify(d.source_name)
        if base_id in used:
            used[base_id] += 1
            d.doc_id = f"{base_id}-{used[base_id]}"
        else:
            used[base_id] = 1
            d.doc_id = base_id

    families: dict[str, list[Document]] = {}
    for d in docs:
        if d.family:
            families.setdefault(d.family, []).append(d)
    for fam in families.values():
        fam.sort(key=lambda d: d.valid_from or date.min)
        for prev, nxt in zip(fam, fam[1:]):
            if nxt.valid_from and (prev.valid_to is None or prev.valid_to >= nxt.valid_from):
                prev.valid_to = nxt.valid_from - timedelta(days=1)
            prev.relations.append(Relation(kind="replaced_by", target_doc=nxt.doc_id, effective=nxt.valid_from))
            nxt.relations.append(Relation(kind="replaces", target_doc=prev.doc_id, effective=nxt.valid_from))

    by_number = {norm_id(d.number): d for d in docs if d.number and d.doc_type == "contract"}
    for d in docs:
        for rel in d.relations:
            if rel.target_doc.startswith("number:"):
                target = by_number.get(norm_id(rel.target_doc[len("number:"):]))
                rel.target_doc = target.doc_id if target else rel.target_doc
        if d.category != "company":
            continue
        for u in d.units:
            for m in REF_POINT_RE.finditer(u.text):
                anchor = f"p-{m.group(1)}"
                ed = m.group(4)
                cands = [x for x in docs if x.doc_type == "regulation" and x.unit(anchor) and fold(m.group(2))[:6] in fold(x.act)]
                if ed:
                    cands = [x for x in cands if x.edition == ed] or cands
                for tgt in cands[:1]:
                    if tgt.doc_id != d.doc_id:
                        d.relations.append(Relation(kind="supplements", target_doc=tgt.doc_id, target_anchors=[anchor], effective=d.doc_date, text=u.text[:300]))

    for d in docs:
        for rel in list(d.relations):
            if rel.kind not in ("amends", "supplements"):
                continue
            target = next((x for x in docs if x.doc_id == rel.target_doc), None)
            if target:
                target.relations.append(Relation(kind="amended_by" if rel.kind == "amends" else "supplemented_by", target_doc=d.doc_id, target_anchors=list(rel.target_anchors), condition=rel.condition, effective=rel.effective))
    link_law_references(docs)
    return docs


def parse_documents(raws: list[RawDoc]) -> list[Document]:
    docs: list[Document] = []
    for raw in raws:
        if _is_law(raw):
            docs.extend(parse_law(raw))
        else:
            docs.append(parse_company(raw))
    return link(docs)
