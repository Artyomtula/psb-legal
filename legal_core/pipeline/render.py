from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import quote

from ..kb.models import Document, Note
from ..text import fmt_date, fold, norm_id, quoted_name
from .fragments import CHANGE_KINDS, Fragment, unit_notes

MARKER_RE = re.compile(r"\s*\[(S\d+(?:\s*[,;]\s*S\d+)*)\]")
_MARKER = r"\[S\d+(?:\s*[,;]\s*S\d+)*\]"
_MARKERS = rf"\s*{_MARKER}(?:\s*(?:[,;]|\bи\b)?\s*{_MARKER})*"
MARKER_GROUP_RE = re.compile(_MARKERS)
PUNCT_BEFORE_RE = re.compile(rf"([.!?;:])({_MARKERS})(?=\s*(?:$|[А-ЯЁA-Z«\"\d—–-]))")
ORPHAN_MARKERS_RE = re.compile(rf"({_MARKER})\s*[.!?;]((?:\s*{_MARKER})+)(?=\s*(?:[.!?;]|$))")
SAME_SOURCE = "там же"
SUPPLEMENT_NO_RE = re.compile(r"соглашение №\s*\S+")
LABEL_PREFIX_RE = re.compile(r"^((?:п|ч|пп|абз)\.\s)(.+)$")
_ODD_MARKER_RE = re.compile(r"\[\s*[SsЅ]\s*#?\s*[SsЅ]?\s*(\d+)\s*\]")
_EMPTY_MARKER_RE = re.compile(r"\s*\[\s*S\s*#\s*\]")
_LIST_MARKER_RE = re.compile(r"\[\s*(S\d+(?:\s*(?:[,;]|и)\s*S?\d+)+)\s*\]")
BARE_SID_RE = re.compile(r"(?<![\[\w#])S\d+(?![\w\]])")


def normalize_markers(answer: str) -> str:
    answer = _ODD_MARKER_RE.sub(lambda m: f"[S{m.group(1)}]", answer)
    answer = _LIST_MARKER_RE.sub(lambda m: "[" + ", ".join(f"S{n}" for n in re.findall(r"\d+", m.group(1))) + "]", answer)
    answer = re.sub(r"\((S\d+(?:\s*,\s*S\d+)*)\)", r"[\1]", answer)
    return _EMPTY_MARKER_RE.sub("", answer)


@dataclass
class Citation:
    n: int
    sid: str
    doc_id: str
    anchor: str
    label: str
    title: str
    text: str
    url: str


@dataclass
class Rendered:
    text: str
    citations: list[Citation] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    used_sids: list[str] = field(default_factory=list)


def unit_url(base: str, doc_id: str, anchor: str) -> str:
    return f"{base}/kb/{quote(doc_id)}?hl={quote(anchor)}#{quote(anchor)}"


def _doc_genitive(doc: Document, answer: str = "") -> str:
    if doc.doc_type == "contract":
        party = doc.counterparty or ""
        return f"договора № {doc.number}" + (f" с {party}" if party and fold(quoted_name(party)) not in fold(answer) else "")
    if doc.doc_type == "supplement":
        return doc.short.replace("доп. соглашение", "доп. соглашения")
    if doc.doc_type == "regulation":
        base = doc.short.split(" (")[0]
        words = base.split()
        if words and words[0].lower() == "регламент":
            words[0] = "Регламента"
        elif words and words[0].lower() == "положение":
            words[0] = "Положения"
        return " ".join(words) + (f" (ред. от {doc.edition})" if doc.edition else "")
    return doc.short


def _doc_mentioned(doc: Document, answer: str) -> bool:
    low = fold(answer)
    if doc.number and (fold(doc.number) in low or norm_id(doc.number) in norm_id(answer)):
        return True
    if doc.doc_type == "regulation":
        return bool(doc.edition) and doc.edition in answer
    return False


def _supplement_label(doc: Document) -> str:
    label = doc.short
    if doc.doc_date and " от " not in label:
        label = SUPPLEMENT_NO_RE.sub(lambda m: f"{m.group(0)} от {fmt_date(doc.doc_date)}", label, count=1)
    amended = [f"п. {a[2:]}" for rel in doc.relations if rel.kind == "amends" for a in rel.target_anchors if a.startswith("p-")]
    return f"{label}, новая редакция {', '.join(dict.fromkeys(amended))}" if amended else label


def cite_label(f: Fragment, answer: str, named: bool = False) -> str:
    doc, unit = f.doc, f.unit
    if doc.category == "law":
        base = f"{doc.act}, ст. {doc.article}" if doc.article else doc.act
        return f"{base}, {unit.label}" if unit.label else base
    if unit.kind == "preamble":
        return f"преамбула {_doc_genitive(doc, answer)}" if doc.doc_type == "contract" else doc.short
    if not unit.label:
        if named:
            return SAME_SOURCE
        return _supplement_label(doc) if doc.doc_type == "supplement" else doc.short
    return unit.label if named or _doc_mentioned(doc, answer) else f"{unit.label} {_doc_genitive(doc, answer)}"


def _group_label(frags: list[Fragment], answer: str, links: bool, base_url: str, named_doc: str = "") -> str:
    parts: list[str] = []
    by_doc: dict[str, list[Fragment]] = {}
    for f in frags:
        by_doc.setdefault(f.doc.doc_id, []).append(f)
    for doc_id, doc_frags in by_doc.items():
        named = doc_id == named_doc
        labels = [cite_label(f, answer, named) for f in doc_frags]
        doc = doc_frags[0].doc
        if len(doc_frags) > 1 and doc.category == "company" and all(f.unit.label.startswith("п. ") for f in doc_frags):
            nums = [f.unit.label[3:] for f in doc_frags]
            tail = "" if named or _doc_mentioned(doc, answer) else " " + _doc_genitive(doc, answer)
            if links:
                items = [f"[{('п. ' if i == 0 else '')}{n}]({unit_url(base_url, f.doc.doc_id, f.unit.anchor)})" for i, (n, f) in enumerate(zip(nums, doc_frags))]
                parts.append(", ".join(items) + tail)
            else:
                parts.append("п. " + ", ".join(nums) + tail)
            continue
        if not links and len(doc_frags) > 1 and doc.category == "law" and doc.article:
            parts.append(_law_label(doc, [f.unit.label for f in doc_frags]))
            continue
        for lab, f in zip(labels, doc_frags):
            parts.append(f"[{lab}]({unit_url(base_url, f.doc.doc_id, f.unit.anchor)})" if links else lab)
    return "; ".join(dict.fromkeys(parts))


def _law_label(doc: Document, units: list[str]) -> str:
    base = f"{doc.act}, ст. {doc.article}"
    split = [LABEL_PREFIX_RE.match(u) for u in units if u]
    if split and all(split) and len({m.group(1) for m in split}) == 1:
        return f"{base}, {split[0].group(1)}{', '.join(m.group(2) for m in split)}"
    return ", ".join([base, *[u for u in units if u]])


def _already_named(f: Fragment, before: str) -> bool:
    label = f.unit.label
    return f.doc.category == "company" and bool(label) and bool(re.search(rf"{re.escape(label)}(?![\d.]*\d)", before))


def qualifying_notes(f: Fragment, today: date, event: date | None, recent_days: int) -> list[Note]:
    out = []
    for n in unit_notes(f.doc, f.unit):
        if n.kind not in CHANGE_KINDS or not n.effective:
            continue
        recent = n.effective >= today - timedelta(days=recent_days)
        future = n.effective > today
        after_event = event is not None and n.effective > event
        if recent or future or after_event:
            out.append(n)
    return out


FUTURE_REASON = "предстоящее изменение, сейчас ещё не действует"
FUTURE_VERBS = {"изменен": "изменяется", "дополнен": "дополняется", "изложен": "излагается", "признан": "признаётся"}
FUTURE_VERB_RE = re.compile(r"\b(" + "|".join(FUTURE_VERBS) + r")[аоы]?\b")


def note_reason(note: Note, today: date, event: date | None) -> str:
    if note.kind == "repealed" or not note.effective:
        return ""
    if event is not None and note.effective > event:
        return "на дату события действовала прежняя редакция, которой в базе нет"
    if note.effective > today:
        return FUTURE_REASON
    return "ответ дан по действующей редакции"


def footnote_text(doc: Document, note: Note, reason: str = "") -> str:
    text = note.text.replace(" - ", " — ").strip().rstrip(".")
    if reason == FUTURE_REASON:
        text = FUTURE_VERB_RE.sub(lambda m: FUTURE_VERBS[m.group(1)], text, count=1)
    if doc.category == "law":
        m = re.match(r"^(Статья\s+[\d.]+)\b", text)
        if m:
            text = f"{m.group(1)} {doc.act}" + text[m.end():]
        else:
            m = re.match(r"^((?:Пункт|Часть|Подпункт|Абзац|Примечани\w+)\s*[\d.]*(?:-\d+)?)\s", text)
            if m and doc.article:
                text = f"{m.group(1)} статьи {doc.article} {doc.act} " + text[m.end():]
    if reason:
        text = f"{reason[0].upper()}{reason[1:]}; {text[0].lower()}{text[1:]}"
    return text + "."


def render(answer: str, frags: list[Fragment], *, links: bool, base_url: str, today: date, event: date | None, recent_days: int, footnotes: bool = True, one_line: bool = False) -> Rendered:
    by_sid = {f.sid: f for f in frags}
    answer = ORPHAN_MARKERS_RE.sub(r"\1\2", normalize_markers(answer))
    answer = PUNCT_BEFORE_RE.sub(lambda m: m.group(2) + m.group(1), answer)
    used: list[str] = []
    note_index: dict[str, int] = {}
    notes_out: list[str] = []
    footnote_refs: dict[str, list[int]] = {}

    if footnotes:
        for f in frags:
            for n in qualifying_notes(f, today, event, recent_days):
                if one_line and not (event is not None and n.effective > event):
                    continue
                txt = footnote_text(f.doc, n, note_reason(n, today, event))
                if txt not in note_index:
                    notes_out.append(txt)
                    note_index[txt] = len(notes_out)
                footnote_refs.setdefault(f.sid, []).append(note_index[txt])

    cited_notes: set[int] = set()
    last_doc = ""

    def repl(m: re.Match) -> str:
        nonlocal last_doc
        sids = re.findall(r"S\d+", m.group(0))
        group = []
        for sid in sids:
            f = by_sid.get(sid)
            if f and f not in group:
                group.append(f)
                if sid not in used:
                    used.append(sid)
        if not group:
            return ""
        marks = ""
        if footnotes:
            nums = sorted({n for f in group for n in footnote_refs.get(f.sid, [])} - cited_notes)
            cited_notes.update(nums)
            if nums and not one_line:
                marks = "".join(f"[^{n}]" for n in nums)
        before = m.string[max(0, m.start() - 60):m.start()]
        shown = [f for f in group if links or not _already_named(f, before)]
        if not shown:
            return marks
        label = _group_label(shown, answer, links, base_url, last_doc)
        last_doc = shown[-1].doc.doc_id
        return f" ({label}){marks}"

    text = MARKER_GROUP_RE.sub(repl, answer)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    used_notes = [notes_out[n - 1] for n in sorted(cited_notes)]
    if footnotes and used_notes:
        if one_line:
            label = "Примечание" if len(used_notes) == 1 else "Примечания"
            body = used_notes[0] if len(used_notes) == 1 else " ".join(f"{i}) {t}" for i, t in enumerate(used_notes, 1))
            text = text.rstrip() + f" {label}: {body}"
        else:
            renum = {old: new for new, old in enumerate(sorted(cited_notes), 1)}
            text = re.sub(r"\[\^(\d+)\]", lambda m: f"[^{renum.get(int(m.group(1)), m.group(1))}]", text)
            text = text.rstrip() + "\n\n**Примечания**\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(used_notes, 1))
    if one_line:
        text = " ".join(text.split())
    cites = []
    for n, sid in enumerate(used, 1):
        f = by_sid[sid]
        cites.append(Citation(n=n, sid=sid, doc_id=f.doc.doc_id, anchor=f.unit.anchor, label=cite_label(f, answer), title=f.doc.title, text=f.unit.text, url=unit_url(base_url, f.doc.doc_id, f.unit.anchor)))
    return Rendered(text=text.strip(), citations=cites, footnotes=used_notes, used_sids=used)
