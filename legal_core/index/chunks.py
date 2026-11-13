from __future__ import annotations

from dataclasses import dataclass

from ..kb.models import Document, Unit
from ..text import fmt_date

MAX_CHUNK = 1500
WINDOW = 1100


@dataclass
class Chunk:
    cid: str
    doc_id: str
    anchor: str
    label: str
    header: str
    lead: str
    text: str

    @property
    def index_text(self) -> str:
        parts = [self.header]
        if self.lead:
            parts.append(self.lead)
        parts.append(self.text)
        return "\n".join(parts)


def validity_text(doc: Document) -> str:
    if doc.valid_to and doc.valid_from:
        return f"действовал с {fmt_date(doc.valid_from)} по {fmt_date(doc.valid_to)}, утратил силу"
    if doc.valid_to:
        return f"утратил силу после {fmt_date(doc.valid_to)}"
    if doc.valid_from and doc.doc_type == "regulation":
        return f"действует с {fmt_date(doc.valid_from)}"
    return ""


def doc_header(doc: Document) -> str:
    if doc.category == "law":
        head = f"{doc.act_full} ({doc.act})"
        if doc.article:
            head += f", статья {doc.article}"
            if doc.article_title:
                head += f". {doc.article_title}"
        return head
    parts = [doc.title]
    if doc.subject and doc.doc_type == "contract":
        parts.append(f"Предмет: {doc.subject[:160]}")
    v = validity_text(doc)
    if v:
        parts.append(v)
    return ". ".join(parts)


def unit_header(doc: Document, unit: Unit, amended: dict[str, list[str]]) -> str:
    head = doc_header(doc)
    if unit.heading:
        head += f" | {unit.heading}"
    if unit.label:
        head += f" | {unit.label}"
    notes = amended.get(unit.anchor)
    if notes:
        head += " | " + "; ".join(notes)
    return head


def _windows(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK:
        return [text]
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for line in text.split("\n"):
        while len(line) > WINDOW:
            cut = line.rfind(" ", 0, WINDOW)
            cut = cut if cut > WINDOW // 2 else WINDOW
            if cur:
                out.append("\n".join(cur))
                cur, size = [], 0
            out.append(line[:cut])
            line = line[cut:].lstrip()
        if size + len(line) > WINDOW and cur:
            out.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def build_chunks(docs: list[Document]) -> list[Chunk]:
    by_id = {d.doc_id: d for d in docs}
    chunks: list[Chunk] = []
    for doc in docs:
        amended: dict[str, list[str]] = {}
        for rel in doc.relations:
            src = by_id.get(rel.target_doc)
            if not src:
                continue
            if rel.kind == "amended_by":
                for a in rel.target_anchors:
                    amended.setdefault(a, []).append(f"изменён: {src.short} от {fmt_date(src.doc_date)}")
            elif rel.kind == "supplemented_by":
                for a in rel.target_anchors:
                    amended.setdefault(a, []).append(f"дополнен: {src.short}")
        for unit in doc.units:
            if unit.repealed or unit.injection or not unit.text.strip():
                continue
            header = unit_header(doc, unit, amended)
            pieces = _windows(unit.text)
            for k, piece in enumerate(pieces):
                cid = f"{doc.doc_id}#{unit.anchor}" + (f"~{k}" if len(pieces) > 1 else "")
                chunks.append(Chunk(cid=cid, doc_id=doc.doc_id, anchor=unit.anchor, label=unit.label, header=header, lead=unit.lead, text=piece))
    return chunks
