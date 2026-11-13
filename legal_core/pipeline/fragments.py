from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..index.chunks import validity_text
from ..index.store import KnowledgeIndex
from ..kb.models import Document, Note, Unit
from ..text import fmt_date
from .retrieve import Hit

MAX_UNIT_CHARS = 3500
CHANGE_KINDS = ("changed", "added", "repealed")
MAX_MISSING_REFS = 8
ARTICLE_REF_RE = re.compile(r"стат(?:ья|ьи|ье|ью|ьей|ьёй|ьями|ьях|ьям)\s+(\d+(?:\.\d+)?)", re.I)
SAME_ACT_RE = re.compile(r"настоящ\w+\s+(?:Федеральн\w+\s+закон\w*|Кодекс\w*)", re.I)
OTHER_ACT_RE = re.compile(r"Федеральн\w+\s+закон\w*\s+от|кодекс\w*\s+Российской", re.I)

Period = tuple[date, date]


@dataclass
class Fragment:
    sid: str
    doc: Document
    unit: Unit
    text: str
    score: float
    origin: str
    chunk_ids: list[int] = field(default_factory=list)
    relevance: int | None = None

    @property
    def key(self) -> str:
        return f"{self.doc.doc_id}#{self.unit.anchor}"


def unit_notes(doc: Document, unit: Unit) -> list[Note]:
    notes = list(unit.notes)
    if doc.category == "law":
        notes = [n for n in doc.notes if n.kind != "info" or n.effective] + notes
    return notes


def _labels(doc: Document, anchors: list[str]) -> str:
    out = []
    for a in anchors:
        u = doc.unit(a)
        out.append(u.label if u and u.label else a)
    return ", ".join(out) or "—"


def period_text(period: Period) -> str:
    start, end = period
    return fmt_date(start) if start == end else f"{fmt_date(start)} – {fmt_date(end)}"


def edition_applicability(doc: Document, period: Period | None, today: date) -> str:
    if not doc.family or (doc.valid_from is None and doc.valid_to is None):
        return ""
    if period is None:
        if doc.valid_to and doc.valid_to < today:
            return f"Применимость: редакция утратила силу — к текущим ситуациям не применяй, только к событиям до {fmt_date(doc.valid_to)} включительно"
        if doc.valid_from and doc.valid_from > today:
            return f"Применимость: редакция ещё не вступила в силу (вступает {fmt_date(doc.valid_from)})"
        return ""
    start, end = period
    when = period_text(period)
    if doc.valid_from and end < doc.valid_from:
        return f"Применимость: на дату события ({when}) эта редакция ещё не действовала — к событию её не применяй"
    if doc.valid_to and start > doc.valid_to:
        return f"Применимость: на дату события ({when}) эта редакция уже утратила силу — к событию её не применяй"
    if (doc.valid_from is None or start >= doc.valid_from) and (doc.valid_to is None or end <= doc.valid_to):
        return f"Применимость: на дату события ({when}) действовала именно эта редакция — применяй её"
    return f"Применимость: период события ({when}) лишь частично совпадает со сроком действия этой редакции"


def change_applicability(effective: date, kind: str, period: Period | None) -> str:
    if period is None:
        return ""
    start, end = period
    when = period_text(period)
    if kind == "repealed":
        return f"на дату события ({when}) положение уже утратило силу" if start >= effective else ""
    if end < effective:
        return f"на дату события ({when}) изменение от {fmt_date(effective)} ещё не действовало: приведённый текст — более поздняя редакция, а редакции на дату события в базе нет"
    if start < effective:
        return f"в период события ({when}) положение изменилось с {fmt_date(effective)}: до этой даты действовала прежняя редакция, которой в базе нет"
    return ""


def notes_applicability(notes: list[Note], period: Period | None) -> list[str]:
    out = [change_applicability(n.effective, n.kind, period) for n in notes if n.effective and n.kind in CHANGE_KINDS]
    return list(dict.fromkeys(x for x in out if x))[:3]


def relation_lines(index: KnowledgeIndex, doc: Document, unit: Unit, period: Period | None = None) -> list[str]:
    lines = []
    for rel in doc.relations:
        other = index.by_id.get(rel.target_doc)
        if other is None:
            continue
        if rel.kind == "amended_by" and (not rel.target_anchors or unit.anchor in rel.target_anchors):
            cond = f"; условие применения: {rel.condition}" if rel.condition else ""
            line = f"Этот пункт изменён документом «{other.title}» (вступает в силу {fmt_date(rel.effective) or 'с даты подписания'}){cond}"
            if rel.effective and period:
                if period[1] < rel.effective:
                    line += f". На дату события ({period_text(period)}) изменение ещё не вступило в силу — применяй этот пункт в исходной редакции"
                elif period[0] >= rel.effective:
                    line += f". На дату события ({period_text(period)}) изменение уже вступило в силу" + (" — проверь условие применения" if rel.condition else " — применяй изменённую редакцию")
            lines.append(line)
        elif rel.kind == "supplemented_by" and (not rel.target_anchors or unit.anchor in rel.target_anchors):
            lines.append(f"Этот пункт дополнен документом «{other.title}»")
        elif rel.kind == "amends":
            cond = f"; условие применения: {rel.condition}" if rel.condition else ""
            lines.append(f"Документ изменяет {other.title}, пункты: {_labels(other, rel.target_anchors)}{cond}")
        elif rel.kind == "supplements":
            lines.append(f"Документ дополняет {other.title}, пункты: {_labels(other, rel.target_anchors)}")
        elif rel.kind == "replaced_by":
            lines.append(f"Редакция заменена: {other.title} (с {fmt_date(rel.effective)})")
        elif rel.kind == "replaces":
            lines.append(f"Эта редакция заменила: {other.title}")
    return lines


def build_fragments(index: KnowledgeIndex, hits: list[Hit], limit: int) -> list[Fragment]:
    grouped: dict[tuple[str, str], Fragment] = {}
    order: list[tuple[str, str]] = []
    for h in hits:
        c = index.chunks[h.idx]
        key = (c.doc_id, c.anchor)
        if key in grouped:
            grouped[key].chunk_ids.append(h.idx)
            continue
        if len(order) >= limit:
            continue
        doc = index.by_id[c.doc_id]
        unit = doc.unit(c.anchor)
        if unit is None:
            continue
        grouped[key] = Fragment(sid="", doc=doc, unit=unit, text="", score=h.score, origin=h.origin, chunk_ids=[h.idx])
        order.append(key)
    frags = []
    for n, key in enumerate(order, 1):
        f = grouped[key]
        f.sid = f"S{n}"
        full = (f.unit.lead + "\n" if f.unit.lead else "") + f.unit.text
        if len(full) > MAX_UNIT_CHARS:
            parts = [index.chunks[i].text for i in sorted(f.chunk_ids)]
            full = (f.unit.lead + "\n" if f.unit.lead else "") + "\n…\n".join(parts)
        f.text = full
        frags.append(f)
    return frags


def pair_editions(picked: list[Fragment], pool: list[Fragment], limit: int) -> list[Fragment]:
    keys = {f.key for f in picked}
    out = list(picked)
    for f in picked:
        if not f.doc.family:
            continue
        for g in pool:
            if len(out) >= limit:
                return out
            if g.key not in keys and g.doc.family == f.doc.family and g.unit.anchor == f.unit.anchor:
                out.append(g)
                keys.add(g.key)
    return out


def cover_documents(picked: list[Fragment], pool: list[Fragment], doc_ids: list[str], limit: int) -> list[Fragment]:
    out = list(picked)
    present = {f.doc.doc_id for f in out}
    for doc_id in dict.fromkeys(doc_ids):
        if doc_id in present or len(out) >= limit:
            continue
        best = max((g for g in pool if g.doc.doc_id == doc_id), key=lambda g: g.relevance or 0, default=None)
        if best is not None:
            out.append(best)
            present.add(doc_id)
    return out


def drop_expired(frags: list[Fragment], today: date) -> list[Fragment]:
    current = [f for f in frags if not (f.doc.family and f.doc.valid_to and f.doc.valid_to < today)]
    return current or frags


def missing_articles(index: KnowledgeIndex, doc: Document, unit: Unit) -> list[str]:
    if doc.category != "law":
        return []
    known = {d.article for d in index.by_id.values() if d.category == "law" and d.act == doc.act}
    out: list[str] = []
    for sentence in re.split(r"(?<=[.;])\s+", unit.text):
        for m in ARTICLE_REF_RE.finditer(sentence):
            tail = sentence[m.end():]
            same = SAME_ACT_RE.search(tail)
            if same is None or OTHER_ACT_RE.search(tail[:same.start()]):
                continue
            number = m.group(1)
            if number not in known and number not in out:
                out.append(number)
    return out[:MAX_MISSING_REFS]


def drop_unrelated_amendments(frags: list[Fragment]) -> list[Fragment]:
    present = {(f.doc.doc_id, f.unit.anchor) for f in frags}

    def related(f: Fragment) -> bool:
        targets = [(rel.target_doc, anchor) for rel in f.doc.relations if rel.kind in ("amends", "supplements") for anchor in rel.target_anchors]
        return not targets or any(target in present for target in targets)

    kept = [f for f in frags if f.doc.doc_type != "supplement" or related(f)]
    return kept or frags


def edition_groups(frags: list[Fragment]) -> list[list[str]]:
    families: dict[str, dict[str, list[str]]] = {}
    for f in frags:
        if f.doc.family:
            families.setdefault(f.doc.family, {}).setdefault(f.doc.doc_id, []).append(f.sid)
    return [sids for editions in families.values() if len(editions) > 1 for sids in editions.values()]


def renumber(frags: list[Fragment]) -> list[Fragment]:
    for n, f in enumerate(frags, 1):
        f.sid = f"S{n}"
    return frags


def format_fragment(index: KnowledgeIndex, f: Fragment, period: Period | None = None, today: date | None = None) -> str:
    today = today or date.today()
    doc, unit = f.doc, f.unit
    lines = [f"[{f.sid}] {doc.title}"]
    where = unit.label or ("преамбула" if unit.kind == "preamble" else "текст документа")
    if unit.heading:
        where += f" (раздел «{unit.heading}»)"
    lines.append(f"Положение: {where}")
    status = validity_text(doc)
    if doc.category == "company" and doc.doc_type == "regulation":
        lines.append(f"Статус редакции: {status or 'действующая'}")
    elif status:
        lines.append(f"Статус: {status}")
    applicability = edition_applicability(doc, period, today)
    if applicability:
        lines.append(applicability)
    if doc.category == "law":
        lines.append("Редакция: действующая (другие редакции в базе отсутствуют)")
    lines.extend(relation_lines(index, doc, unit, period))
    absent = missing_articles(index, doc, unit)
    if absent:
        lines.append(f"Отсылки к положениям, которых нет в базе знаний: ст. {', '.join(absent)} {doc.act}")
    notes = unit_notes(doc, unit)
    if notes:
        lines.append("Примечания к редакции: " + " | ".join(n.text for n in notes[:6]))
        changes = notes_applicability(notes, period)
        if changes:
            lines.append("Применимость изменений: " + "; ".join(changes))
    lines.append("Текст: " + f.text)
    return "\n".join(lines)


def format_fragments(index: KnowledgeIndex, frags: list[Fragment], period: Period | None = None, today: date | None = None) -> str:
    return "\n\n".join(format_fragment(index, f, period, today) for f in frags)
