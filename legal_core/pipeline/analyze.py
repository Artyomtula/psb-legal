from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import date

from ..dialog import Turn, render_history
from ..index.store import KnowledgeIndex
from ..kb.models import Document
from ..providers.llm import LLM
from ..text import fold, norm_id, quoted_name, stems, tokenize
from .prompts import ANALYZE_SYSTEM, ANALYZE_USER
from .units import resolve_unit

try:
    from rapidfuzz import fuzz
except Exception:
    fuzz = None

CONTRACT_NO_RE = re.compile(r"(?<![\w-])([A-Za-zА-Яа-яЁё]{1,4}-?\d{1,4}[/-]\d{2,4})(?![\w])")
ARTICLE_RE = re.compile(r"(?:\bст\.?|\bстать[яиеюй]\w*)\s*(\d+(?:\.\d+)?)(?:\s+(ТК|НК|КоАП|ГК|УК|ЖК|БК|ЗК|СК|ГПК|АПК)\b)?", re.I)
FZ_RE = re.compile(r"(\d{1,4}(?:-\d)?)\s*-?\s*ФЗ|ФЗ\s*-?\s*№?\s*(\d{1,4})", re.I)
CB_RE = re.compile(r"(\d{2,5}-[ПУИ])\b")
TIME_HINT_RE = re.compile(r"\b(?:19|20)\d{2}\b|\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*|\bгод[ау]?\b|\bквартал\w*", re.I)
MAX_EVENT_DAYS = 366
EXPLICIT_MATCH = 0.8
GENERIC_STEMS = frozenset(("догов",))


@dataclass
class Analysis:
    standalone: str
    scope: str = "legal"
    flags: set[str] = field(default_factory=set)
    documents: dict[str, float] = field(default_factory=dict)
    ambiguous: list[dict] = field(default_factory=list)
    references: list[tuple[str, str]] = field(default_factory=list)
    missing_documents: list[dict] = field(default_factory=list)
    event_from: date | None = None
    event_to: date | None = None
    event_quote: str = ""
    user_claims: list[dict] = field(default_factory=list)
    sub_questions: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    hyde: str = ""
    dropped: list[str] = field(default_factory=list)
    regex_hits: list[str] = field(default_factory=list)
    llm_ok: bool = True

    @property
    def period(self) -> tuple[date, date] | None:
        if not (self.event_from and self.event_to) or (self.event_to - self.event_from).days > MAX_EVENT_DAYS:
            return None
        if not TIME_HINT_RE.search(CONTRACT_NO_RE.sub(" ", self.event_quote)):
            return None
        return self.event_from, self.event_to

    def to_dict(self) -> dict:
        return {
            "standalone": self.standalone, "scope": self.scope, "flags": sorted(self.flags), "documents": self.documents,
            "ambiguous": self.ambiguous, "references": [f"{d}#{a}" for d, a in self.references],
            "missing_documents": self.missing_documents, "event_from": str(self.event_from) if self.event_from else None,
            "event_to": str(self.event_to) if self.event_to else None, "event_quote": self.event_quote,
            "user_claims": self.user_claims, "sub_questions": self.sub_questions, "search_queries": self.search_queries,
            "hyde": self.hyde,
            "dropped": self.dropped, "regex_hits": self.regex_hits, "llm_ok": self.llm_ok,
        }


def quote_ok(quote: str, haystack: str) -> bool:
    q = fold(quote).strip(" .,:;«»\"'")
    if not q:
        return False
    if q in haystack:
        return True
    if fuzz is not None and len(q) >= 6:
        return fuzz.partial_ratio(q, haystack) >= 90
    return False


def name_stems(text: str) -> set[str]:
    return stems(text) - GENERIC_STEMS


def _words(text: str) -> list[str]:
    return [token for token in tokenize(text) if token.isalpha()]


def _has_phrase(words: list[str], phrase: list[str]) -> bool:
    size = len(phrase)
    return bool(size) and any(words[i:i + size] == phrase for i in range(len(words) - size + 1))


def names_several(quote: str, docs: list[Document]) -> bool:
    if any(d.doc_type != "contract" for d in docs):
        return True
    words = _words(quote)
    if sum(_has_phrase(words, _words(quoted_name(d.counterparty or ""))) for d in docs) == 1:
        return False
    asked = name_stems(quote)
    return sum(bool(asked & name_stems(d.title)) for d in docs) >= 2


def _date(value) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _list(value) -> list:
    return value if isinstance(value, list) else []


def regex_scan(index: KnowledgeIndex, text: str) -> tuple[dict[str, float], list[dict], list[str]]:
    docs: dict[str, float] = {}
    missing: list[dict] = []
    hits: list[str] = []
    contracts = {norm_id(d.number): d.doc_id for d in index.docs if d.number and d.doc_type in ("contract", "supplement", "memo", "letter", "order")}
    for m in CONTRACT_NO_RE.finditer(text):
        key = norm_id(m.group(1))
        if key in contracts:
            docs[contracts[key]] = max(docs.get(contracts[key], 0), 0.9)
            hits.append(f"номер {m.group(1)} → {contracts[key]}")
    for m in ARTICLE_RE.finditer(text):
        art, code = m.group(1), (m.group(2) or "").lower()
        cands = [d for d in index.docs if d.category == "law" and d.article == art and (not code or fold(d.act).startswith(code))]
        if cands:
            for d in cands:
                docs[d.doc_id] = max(docs.get(d.doc_id, 0), 0.8 if len(cands) == 1 else 0.5)
            hits.append(f"ст. {art} {code.upper()} → {[d.doc_id for d in cands]}")
        elif code:
            missing.append({"quote": m.group(0), "what": f"статья {art} {code.upper()} РФ отсутствует в базе", "source": "regex"})
    for m in FZ_RE.finditer(text):
        no = (m.group(1) or m.group(2) or "").split("-")[0]
        cands = [d for d in index.docs if d.category == "law" and d.number and d.number.split("-")[0] == no]
        if cands:
            for d in cands:
                docs[d.doc_id] = max(docs.get(d.doc_id, 0), 0.8)
            hits.append(f"{no}-ФЗ → {[d.doc_id for d in cands]}")
        else:
            missing.append({"quote": m.group(0), "what": f"Федеральный закон № {no}-ФЗ отсутствует в базе", "source": "regex"})
    for m in CB_RE.finditer(text):
        no = m.group(1).upper()
        cands = [d for d in index.docs if d.number and d.number.upper() == no]
        if cands:
            for d in cands:
                docs[d.doc_id] = max(docs.get(d.doc_id, 0), 0.8)
        else:
            missing.append({"quote": m.group(0), "what": f"акт № {no} отсутствует в базе", "source": "regex"})
    return docs, missing, hits


async def analyze(llm: LLM, index: KnowledgeIndex, catalog: str, turns: list[Turn], today: date) -> Analysis:
    question = turns[-1].content
    history = render_history(turns)
    haystack = fold("\n".join(t.content for t in turns))
    res = None
    try:
        res = await llm.chat(
            [
                {"role": "system", "content": ANALYZE_SYSTEM.format(today=today.strftime("%d.%m.%Y"), catalog=catalog)},
                {"role": "user", "content": ANALYZE_USER.format(history=history or "(нет)", question=question)},
            ],
            stage="analyze",
            json_out=True,
            temperature=0.0,
            max_tokens=2500,
        )
        data = res.data if isinstance(res.data, dict) else {}
    except Exception as exc:
        data = {}
        print(f"analyze: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
    a = Analysis(standalone=str(data.get("standalone_question") or question).strip(), llm_ok=bool(data))
    a.scope = "offtopic" if data.get("scope") == "offtopic" else "legal"
    a.flags = {f for f in _list(data.get("flags")) if isinstance(f, str)}
    for item in _list(data.get("documents")):
        if isinstance(item, dict) and item.get("doc_id") in index.by_id:
            try:
                conf = float(item.get("confidence", 0.6))
            except (TypeError, ValueError):
                conf = 0.6
            a.documents[item["doc_id"]] = max(a.documents.get(item["doc_id"], 0.0), min(1.0, max(0.0, conf)))
    for item in _list(data.get("ambiguous")):
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote", ""))
        cands = [c for c in _list(item.get("candidates")) if c in index.by_id]
        if len(cands) < 2:
            continue
        if quote_ok(quote, haystack) and names_several(quote, [index.by_id[c] for c in cands]):
            a.ambiguous.append({"quote": quote, "candidates": cands})
        else:
            a.dropped.append(f"ambiguous:{quote}")
    for item in _list(data.get("references")):
        if not isinstance(item, dict):
            continue
        doc = index.by_id.get(item.get("doc_id"))
        if doc is None or not quote_ok(str(item.get("quote", "")), haystack):
            a.dropped.append(f"reference:{item.get('doc_id')}:{item.get('unit')}")
            continue
        anchor = resolve_unit(doc, str(item.get("unit", "")))
        if anchor:
            a.references.append((doc.doc_id, anchor))
        a.documents[doc.doc_id] = max(a.documents.get(doc.doc_id, 0.0), 0.8)
    for item in _list(data.get("missing_documents")):
        if isinstance(item, dict) and quote_ok(str(item.get("quote", "")), haystack):
            a.missing_documents.append({"quote": item.get("quote"), "what": item.get("what", ""), "source": "llm"})
    ev = data.get("event_date") if isinstance(data.get("event_date"), dict) else {}
    if ev:
        quote = str(ev.get("quote") or "")
        if (quote and quote_ok(quote, haystack)) or (not quote and (ev.get("from") or ev.get("to"))):
            a.event_from, a.event_to, a.event_quote = _date(ev.get("from")), _date(ev.get("to")), quote
            if a.event_from and not a.event_to:
                a.event_to = a.event_from
            if a.event_to and not a.event_from:
                a.event_from = a.event_to
    for item in _list(data.get("user_claims")):
        if isinstance(item, dict) and quote_ok(str(item.get("quote", "")), haystack):
            a.user_claims.append({"quote": item.get("quote"), "claim": item.get("claim", "")})
    a.sub_questions = [str(x) for x in _list(data.get("sub_questions")) if str(x).strip()][:4]
    a.search_queries = [str(x) for x in _list(data.get("search_queries")) if str(x).strip()][:4]
    a.hyde = str(data.get("hypothetical_provision") or "")[:600]
    rdocs, rmissing, rhits = regex_scan(index, "\n".join(t.content for t in turns))
    a.regex_hits = rhits
    for did, conf in rdocs.items():
        a.documents[did] = max(a.documents.get(did, 0.0), conf)
    explicit = {did for did, conf in rdocs.items() if conf >= EXPLICIT_MATCH}
    a.ambiguous = [amb for amb in a.ambiguous if len(explicit.intersection(amb["candidates"])) != 1]
    known_missing = {fold(m["quote"]) for m in a.missing_documents}
    for m in rmissing:
        if fold(m["quote"]) not in known_missing:
            a.missing_documents.append(m)
    if not a.sub_questions:
        a.sub_questions = [a.standalone]
    return a
