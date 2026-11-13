from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


@dataclass
class Note:
    text: str
    kind: str
    effective: date | None = None
    law_ref: str | None = None


@dataclass
class Unit:
    anchor: str
    label: str
    text: str
    order: int
    heading: str = ""
    lead: str = ""
    kind: str = "part"
    repealed: bool = False
    injection: bool = False
    notes: list[Note] = field(default_factory=list)


@dataclass
class Relation:
    kind: str
    target_doc: str
    target_anchors: list[str] = field(default_factory=list)
    condition: str = ""
    effective: date | None = None
    text: str = ""
    source_anchor: str = ""


@dataclass
class Document:
    doc_id: str
    source_name: str
    category: str
    doc_type: str
    title: str
    short: str
    act: str
    act_full: str = ""
    number: str | None = None
    doc_date: date | None = None
    article: str | None = None
    article_title: str | None = None
    counterparty: str | None = None
    counterparty_inn: str | None = None
    our_party: str | None = None
    parties: list[dict] = field(default_factory=list)
    subject: str | None = None
    amount: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    edition: str | None = None
    family: str | None = None
    source_url: str | None = None
    exported: date | None = None
    unit_term: str = "пункт"
    header: list[str] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    summary: str = ""
    raw_text: str = ""

    def unit(self, anchor: str) -> Unit | None:
        for u in self.units:
            if u.anchor == anchor:
                return u
        return None


def _encode(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    return value


def _date(value: Any) -> date | None:
    return date.fromisoformat(value) if value else None


def doc_to_dict(doc: Document) -> dict:
    return _encode(asdict(doc))


def doc_from_dict(data: dict) -> Document:
    data = dict(data)
    for key in ("doc_date", "valid_from", "valid_to", "exported"):
        data[key] = _date(data.get(key))
    data["notes"] = [Note(**{**n, "effective": _date(n.get("effective"))}) for n in data.get("notes", [])]
    data["relations"] = [Relation(**{**r, "effective": _date(r.get("effective"))}) for r in data.get("relations", [])]
    units = []
    for u in data.get("units", []):
        u = dict(u)
        u["notes"] = [Note(**{**n, "effective": _date(n.get("effective"))}) for n in u.get("notes", [])]
        units.append(Unit(**u))
    data["units"] = units
    return Document(**data)
