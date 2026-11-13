from __future__ import annotations

from collections import Counter, defaultdict

from ..index.chunks import validity_text
from ..index.store import KnowledgeIndex
from ..kb.models import Document
from ..text import fmt_date


def our_company(index: KnowledgeIndex) -> str:
    c = Counter(d.our_party for d in index.docs if d.our_party)
    return c.most_common(1)[0][0] if c else ""


def _company_line(d: Document) -> str:
    parts = [f"{d.doc_id} | {d.title}"]
    if d.counterparty_inn:
        parts.append(f"ИНН контрагента {d.counterparty_inn}")
    if d.subject:
        parts.append(f"предмет: {d.subject[:180]}")
    v = validity_text(d)
    if v:
        parts.append(v)
    rel = []
    for r in d.relations:
        if r.kind in ("amends", "supplements"):
            rel.append(f"{'изменяет' if r.kind == 'amends' else 'дополняет'} {r.target_doc}" + (f" ({', '.join(r.target_anchors)})" if r.target_anchors else ""))
        elif r.kind in ("amended_by", "supplemented_by"):
            rel.append(f"{'изменён' if r.kind == 'amended_by' else 'дополнен'} документом {r.target_doc}")
        elif r.kind == "replaced_by":
            rel.append(f"заменён новой редакцией {r.target_doc}")
    if rel:
        parts.append("; ".join(rel))
    sections = []
    for u in d.units:
        if u.heading and u.heading not in sections:
            sections.append(u.heading)
    if sections:
        parts.append("разделы: " + ", ".join(s.split(". ", 1)[-1] for s in sections[:12]))
    return " | ".join(parts)


def build_catalog(index: KnowledgeIndex) -> str:
    company = [d for d in index.docs if d.category == "company"]
    laws = [d for d in index.docs if d.category == "law"]
    lines = []
    our = our_company(index)
    if our:
        lines.append(f"Наша организация (пользователь — её сотрудник): {our}.")
    lines.append("Документы организации:")
    for d in sorted(company, key=lambda d: (d.doc_type, d.doc_id)):
        lines.append("- " + _company_line(d))
    by_act: dict[str, list[Document]] = defaultdict(list)
    for d in laws:
        by_act[d.act_full].append(d)
    lines.append("Нормативные правовые акты (в базе ТОЛЬКО перечисленные статьи, в действующей редакции; других статей нет):")
    for act, docs in sorted(by_act.items()):
        arts = "; ".join(f"{d.doc_id} = ст. {d.article}" + (f" «{d.article_title}»" if d.article_title else "") for d in sorted(docs, key=lambda d: _art_key(d.article)))
        lines.append(f"- {act} ({docs[0].act}): {arts}")
    return "\n".join(lines)


def _art_key(article: str | None) -> tuple:
    if not article:
        return (0,)
    out = []
    for p in article.replace("-", ".").split("."):
        out.append(int(p) if p.isdigit() else 0)
    return tuple(out)


def doc_brief(d: Document) -> str:
    if d.category == "law":
        return d.title
    extra = f", {validity_text(d)}" if validity_text(d) else ""
    return f"{d.title}{extra}" + (f", дата документа {fmt_date(d.doc_date)}" if d.doc_date and d.doc_type in ("memo", "letter") else "")
