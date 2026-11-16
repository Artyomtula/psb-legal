from __future__ import annotations

import asyncio
import json
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from ..config import Settings
from ..dialog import Turn, render_history
from ..index.store import KnowledgeIndex
from ..providers.embed import Embedder
from ..providers.llm import CALL_TRACE, LLM
from ..providers.rerank import Reranker
from ..text import stems, tokenize
from .analyze import Analysis, analyze, name_stems
from .catalog import build_catalog, our_company
from .fragments import Fragment, build_fragments, cover_documents, drop_expired, drop_unrelated_amendments, edition_groups, format_fragments, pair_editions, period_text, renumber
from .prompts import (
    ANSWER_ANALYSIS_FIELD,
    ANSWER_STEPS_FIELD,
    ANSWER_STEPS_RULE,
    ANSWER_SYSTEM,
    ANSWER_USER,
    COMPANY_NOTE,
    COMPLETE_SYSTEM,
    COMPLETE_USER,
    GRADE_SYSTEM,
    GRADE_USER,
    REVISE_USER,
    VERIFY_SYSTEM,
    VERIFY_USER,
)
from .render import Citation, normalize_markers, render
from .retrieve import Hit, Plan, Retriever, SearchCache
from .verify import SID_RE, deterministic_issues

Emit = Callable[[dict], Awaitable[None]]
Check = Callable[[str], Awaitable[list[dict]]]
Complete = Callable[[str], Awaitable[str]]
STATUSES = {"answer", "partial", "insufficient", "false_premise", "clarify", "refusal_illegal", "offtopic"}
MIN_SELECTED = 3
MAX_VERIFIED = 3
COMPLETABLE = {"answer", "partial", "false_premise"}
MAX_ADDITION_CHARS = 300
MAX_ADDITION_OVERLAP = 0.8
POLARITY_RE = re.compile(r"\W*(да|нет|только)\b", re.I)
OFFTOPIC_FRAGMENTS = 8
AMENDMENT_HINT_RE = re.compile(r"соглашени|изменени|изменял|изменил|изменён|изменен|\bдс\b", re.I)
HISTORY_HINT_RE = re.compile(r"редакц|раньше|ранее|прежн|предыдущ|изменил|изменен|изменён|стар(?:ая|ой|ую|ые|ых)\b|утратил|был[аои]?\b", re.I)
COUNTERPARTY_HINT_RE = re.compile(r"[Дд]оговор|(?<!\w)(?:ООО|АО|ПАО|ЗАО|ИП)(?!\w)|«")
UNGRADED_TOP = 5
DIGIT_RE = re.compile(r"\d")
RELATIVE_DATE_NOTE = " — вычислена системой от сегодняшней даты; в ответе называй её словами пользователя, календарную дату не пиши"


class TimedTrace(list):
    def __init__(self, start: float) -> None:
        super().__init__()
        self.start = start

    def append(self, item: dict) -> None:
        item.setdefault("at", round(time.monotonic() - self.start, 2))
        super().append(item)


def _majority(values: list) -> object:
    counts = Counter(values)
    top = max(counts.values())
    return next(v for v in values if counts[v] == top)


def _unpack(data: dict, default_status: str = "answer") -> tuple[str, str]:
    status = str(data.get("status") or default_status).strip()
    return normalize_markers(str(data.get("answer") or "").strip()), status if status in STATUSES else default_status


def rank_candidates(cands: list[dict], required: list[list[str]] | None = None) -> list[dict]:
    status = _majority([_unpack(c)[1] for c in cands])
    pool = [c for c in cands if _unpack(c)[1] == status]
    polarity = _majority([_polarity(c) for c in pool])
    agreed = [c for c in pool if _polarity(c) == polarity]
    typical = statistics.median_low(len(_unpack(c)[0]) for c in agreed)

    def cost(cand: dict) -> tuple[int, int, int]:
        answer = _unpack(cand)[0]
        cited = set(SID_RE.findall(answer))
        return sum(1 for group in required or [] if not cited & set(group)), abs(len(answer) - typical), len(answer)

    return sorted(agreed, key=cost)


def majority(cands: list[dict], count: int) -> bool:
    if len(cands) >= count:
        return True
    groups = Counter((_unpack(c)[1], _polarity(c)) for c in cands)
    return bool(groups) and groups.most_common(1)[0][1] > count // 2


def choose(good: list[dict], count: int, required: list[list[str]], verified: Callable[[dict], bool], generating: bool) -> list[dict]:
    if not good or (generating and not majority(good, count)):
        return []
    ranked = rank_candidates(good, required)
    for cand in ranked[:MAX_VERIFIED]:
        if not verified(cand):
            return []
        if not cand.get("_issues"):
            return ranked
    return [] if generating else ranked


def _polarity(cand: dict) -> str:
    m = POLARITY_RE.match(str(cand.get("answer") or ""))
    return m.group(1).lower() if m else ""


def _article_key(article: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", article))


def _score(value: object) -> int | None:
    try:
        return max(0, min(3, int(value)))
    except (TypeError, ValueError):
        return None


def grade_scores(data: dict) -> dict[str, int]:
    rel = data.get("relevance")
    if isinstance(rel, dict):
        items = list(rel.items())
    elif isinstance(data.get("fragments"), list):
        items = [(i.get("id"), i.get("relevance")) for i in data["fragments"] if isinstance(i, dict)]
    else:
        items = []
    out: dict[str, int] = {}
    for sid, value in items:
        score = _score(value)
        if score is not None:
            out[str(sid).strip().strip("[]")] = score
    return out


@dataclass
class AnswerResult:
    text: str
    status: str
    citations: list[Citation] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    fragments: list[Fragment] = field(default_factory=list)
    analysis: Analysis | None = None
    reasoning: str = ""
    steps: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    seconds: float = 0.0
    error: str = ""

    def trace_dict(self) -> dict:
        return {
            "status": self.status,
            "analysis": self.analysis.to_dict() if self.analysis else None,
            "fragments": [{"sid": f.sid, "key": f.key, "label": f.unit.label, "title": f.doc.title, "relevance": f.relevance, "origin": f.origin, "score": round(f.score, 5)} for f in self.fragments],
            "citations": [c.__dict__ for c in self.citations],
            "footnotes": self.footnotes,
            "reasoning": self.reasoning,
            "steps": self.steps,
            "trace": self.trace,
            "seconds": round(self.seconds, 2),
            "error": self.error,
        }


async def _noop(_: dict) -> None:
    return None


class Assistant:
    def __init__(self, s: Settings, index: KnowledgeIndex, llm: LLM, embedder: Embedder | None, reranker: Reranker | None, verifier: LLM | None = None) -> None:
        self.s = s
        self.index = index
        self.llm = llm
        self.verifier = verifier or llm
        self.retriever = Retriever(index, embedder, reranker)
        self.catalog = build_catalog(index)
        self.company = our_company(index)

    def _plan(self, a: Analysis, last: str, rerank_query: str) -> Plan:
        boost: dict[str, float] = {}
        for doc_id, conf in a.documents.items():
            boost[doc_id] = max(boost.get(doc_id, 0.0), 0.25 + 0.75 * conf)
        for amb in a.ambiguous:
            for doc_id in amb["candidates"]:
                boost[doc_id] = max(boost.get(doc_id, 0.0), 0.6)
        queries = [a.standalone, last] + a.sub_questions + a.search_queries
        return Plan(queries=queries, hyde=[a.hyde] if a.hyde else [], doc_boost=boost, pinned=list(a.references), rerank_query=rerank_query)

    def _best_in_doc(self, doc_id: str, queries: list[str], k: int = 2) -> list[int]:
        ids = self.index.doc_chunks.get(doc_id, [])
        if not ids:
            return []
        total = None
        for q in queries:
            sc = self.index.bm25.scores(tokenize(q))[ids]
            total = sc if total is None else total + sc
        order = sorted(range(len(ids)), key=lambda i: -float(total[i]))
        return [ids[i] for i in order[:k] if float(total[i]) > 0]

    def _ensure_docs(self, hits: list[Hit], a: Analysis) -> list[Hit]:
        present = {self.index.chunks[h.idx].doc_id for h in hits[:20]}
        need = [d for amb in a.ambiguous for d in amb["candidates"]]
        need += [d for d, c in a.documents.items() if c >= 0.7]
        extra: list[Hit] = []
        for doc_id in dict.fromkeys(need):
            if doc_id in present:
                continue
            best = next((h for h in hits if self.index.chunks[h.idx].doc_id == doc_id), None)
            if best is not None:
                extra.append(Hit(idx=best.idx, score=best.score, channels=best.channels, origin="ensured"))
                continue
            score = hits[min(len(hits) - 1, 10)].score if hits else 0.0
            for i in self._best_in_doc(doc_id, [a.standalone] + a.search_queries[:3]):
                extra.append(Hit(idx=i, score=score, origin="ensured"))
        if not extra:
            return hits
        ids = {h.idx for h in extra}
        head = hits[:12]
        rest = [h for h in hits[12:] if h.idx not in ids]
        return [h for h in head if h.idx not in ids] + extra + rest

    def _fragments_text(self, a: Analysis, frags: list[Fragment]) -> str:
        return format_fragments(self.index, frags, a.period, self.s.today)

    def _kb_alternatives(self, a: Analysis) -> list[str]:
        asked = " ".join(f"{m.get('what') or ''} {m.get('quote') or ''}" for m in a.missing_documents)
        laws: dict[str, list[str]] = {}
        contracts: list[str] = []
        for d in self.index.by_id.values():
            if d.category == "law" and d.article and re.search(rf"(?<!\w){re.escape(d.act.split()[0])}(?!\w)", asked):
                laws.setdefault(d.act, []).append(d.article)
            elif d.doc_type == "contract":
                contracts.append(d)
        lines = [f"- Из {act} в базе есть только статьи: {', '.join(sorted(arts, key=_article_key))} — назови их пользователю" for act, arts in laws.items()]
        if not laws and contracts and COUNTERPARTY_HINT_RE.search(asked):
            wanted = stems(f"{asked} {a.standalone}")
            overlap = {d.doc_id: len(wanted & name_stems(d.title.split("№")[0])) for d in contracts}
            best = max(overlap.values())
            similar = [d for d in contracts if overlap[d.doc_id] == best] if best else contracts
            names = "; ".join(f"{d.title.split(' от ')[0]} с {d.counterparty}" if d.counterparty else d.title for d in similar[:6])
            lines.append(f"- Похожие договоры, которые есть в базе (назови их с номерами и предложи уточнить, не о них ли речь): {names}")
        return lines

    def _analysis_block(self, a: Analysis) -> str:
        lines = ["Служебная информация разбора запроса (для проверки; пользователю не показывается):", f"- Суть вопроса: {a.standalone}"]
        if a.period:
            lines.append(f"- Дата события: {period_text(a.period)} («{a.event_quote}»)" + ("" if DIGIT_RE.search(a.event_quote) else RELATIVE_DATE_NOTE))
        else:
            lines.append("- Дата события в вопросе не указана: применяй действующие редакции документов, но учитывай даты, если они есть в истории.")
        if a.user_claims:
            lines.append("- Утверждения пользователя, которые нужно проверить по фрагментам: " + "; ".join(f"«{c['quote']}» — {c['claim']}" for c in a.user_claims))
        if a.ambiguous:
            for amb in a.ambiguous:
                titles = [self.index.by_id[d].title for d in amb["candidates"] if d in self.index.by_id]
                lines.append(f"- Возможная неоднозначность «{amb['quote']}»: " + " / ".join(titles))
        if a.missing_documents:
            lines.append("- Упомянуто, но отсутствует в базе: " + "; ".join(m["what"] or m["quote"] for m in a.missing_documents))
            lines.extend(self._kb_alternatives(a))
        if a.flags:
            lines.append("- Признаки: " + ", ".join(sorted(a.flags)))
        if len(a.sub_questions) > 1:
            lines.append("- Подвопросы: " + " | ".join(a.sub_questions))
        return "\n".join(lines)

    async def _warm_search(self, question: str, cache: SearchCache) -> None:
        try:
            hits = await self.retriever.search(Plan(queries=[question]), k_channel=40, k_out=60, cache=cache)
            await self.retriever.rerank(question, hits, top_n=self.s.rerank_top_n, cache=cache)
        except Exception as exc:
            print(f"предварительный поиск: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr)

    async def _grade(self, a: Analysis, frags: list[Fragment], history: str, trace: list[dict]) -> None:
        t = time.monotonic()
        subq = "\n".join(f"{i}. {q}" for i, q in enumerate(a.sub_questions, 1))
        hb = f"История диалога:\n{history}\n" if history else ""
        res = await self.llm.chat(
            [
                {"role": "system", "content": GRADE_SYSTEM.format(today=self.s.today.strftime("%d.%m.%Y"))},
                {"role": "user", "content": GRADE_USER.format(question=a.standalone, history_block=hb, sub_questions=subq, fragments=self._fragments_text(a, frags), ids=", ".join(f.sid for f in frags))},
            ],
            stage="grade",
            json_out=True,
            temperature=0.0,
            max_tokens=800,
        )
        data = res.data if isinstance(res.data, dict) else {}
        by_sid = {f.sid: f for f in frags}
        for sid, value in grade_scores(data).items():
            f = by_sid.get(sid)
            if f is not None:
                f.relevance = value
        trace.append({"step": "grade", "seconds": round(time.monotonic() - t, 2), "relevance": {f.sid: f.relevance for f in frags}})

    def _select(self, frags: list[Fragment], limit: int, strict: bool = False) -> list[Fragment]:
        if all(f.relevance is None for f in frags):
            return frags[:limit]
        strong = sorted((f for f in frags if (f.relevance or 0) >= 2), key=lambda f: -(f.relevance or 0))
        if len(strong) < MIN_SELECTED and not strict:
            top = {f.sid for f in frags[:UNGRADED_TOP]}
            weak = [f for f in frags if f.relevance == 1 or (f.relevance is None and f.sid in top)]
            strong += weak[: MIN_SELECTED - len(strong) + 1]
        return strong[:limit]

    async def _generate(self, messages: list[dict], think: bool, variant: int = 0) -> dict:
        stage = "generate" if variant == 0 else f"generate:{variant}"
        res = await self.llm.chat(messages, stage=stage, json_out=True, temperature=0.0, max_tokens=2500, reasoning=think)
        data = res.data if isinstance(res.data, dict) else {}
        if res.reasoning:
            data["_reasoning"] = res.reasoning
        data["_raw"] = res.content
        return data

    async def _post(self, cand: dict, variant: int, check: Check | None, complete: Complete | None, trace: list[dict]) -> None:
        answer, status = _unpack(cand)
        jobs: dict[str, Awaitable] = {}
        if check is not None and status != "offtopic":
            jobs["verify"] = check(answer)
        if complete is not None and status in COMPLETABLE:
            jobs["complete"] = complete(answer)
        done = dict(zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True)))
        for name, value in done.items():
            if isinstance(value, BaseException):
                trace.append({"step": name, "error": type(value).__name__})
        if isinstance(done.get("verify"), list):
            cand["_issues"] = done["verify"]
            trace.append({"step": f"verify:{variant}", "llm": cand["_issues"]})
        if isinstance(done.get("complete"), str):
            cand["_addition"] = done["complete"]

    async def _ranked_candidates(self, messages: list[dict], think: bool, count: int, trace: list[dict], required: list[list[str]], check: Check | None, complete: Complete | None) -> list[dict]:
        check = check if count > 1 else None
        gens = {asyncio.create_task(self._generate(messages, think, i)): i for i in range(count)}
        posts: dict[int, asyncio.Task] = {}
        cands: dict[int, dict] = {}
        errors: list[BaseException] = []
        ranked: list[dict] = []
        generating = False
        try:
            while True:
                for task, i in gens.items():
                    if not task.done() or i in cands or task.cancelled():
                        continue
                    if task.exception() is not None:
                        errors.append(task.exception())
                        cands[i] = {}
                        continue
                    cands[i] = {**task.result(), "_variant": i}
                    if _unpack(cands[i])[0]:
                        posts[i] = asyncio.create_task(self._post(cands[i], i, check, complete, trace))
                good = [c for c in cands.values() if _unpack(c)[0]] if cands else []
                generating = any(not t.done() for t in gens)
                ranked = choose(good, count, required, lambda c: c["_variant"] not in posts or posts[c["_variant"]].done(), generating)
                pending = {t for t in gens if not t.done()} | {t for t in posts.values() if not t.done()}
                if ranked or not pending:
                    break
                await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in [*gens, *posts.values()]:
                task.cancel()
            await asyncio.gather(*gens, *posts.values(), return_exceptions=True)
        if not ranked:
            good = [c for c in cands.values() if _unpack(c)[0]]
            if not good:
                if errors:
                    raise errors[0]
                return list(cands.values())[:1] or [{}]
            ranked = rank_candidates(good, required)
        if count > 1:
            trace.append({"step": "candidates", "count": len(good), "statuses": [str(c.get("status")) for c in good], "agreed": len(ranked), "early": generating})
        return ranked

    async def _verify(self, a: Analysis, answer: str, frags: list[Fragment], history: str) -> list[dict]:
        hb = f"История диалога:\n{history}\n" if history else ""
        res = await self.verifier.chat(
            [
                {"role": "system", "content": VERIFY_SYSTEM.format(today=self.s.today.strftime("%d.%m.%Y"))},
                {"role": "user", "content": VERIFY_USER.format(question=a.standalone, history_block=hb, answer=answer, fragments=self._fragments_text(a, frags))},
            ],
            stage="verify",
            json_out=True,
            temperature=0.0,
            max_tokens=1500,
        )
        data = res.data if isinstance(res.data, dict) else {}
        if data.get("verdict") == "ok":
            return []
        return [i for i in data.get("issues", []) if isinstance(i, dict)] if isinstance(data.get("issues"), list) else []

    async def _complete(self, question: str, answer: str, frags: list[Fragment]) -> str:
        cited = set(SID_RE.findall(answer))
        own = [f for f in frags if f.sid in cited]
        if not own:
            return ""
        text = "\n\n".join(f"[{f.sid}] {f.doc.short} {f.unit.label}:\n{f.text}" for f in own)
        res = await self.verifier.chat(
            [
                {"role": "system", "content": COMPLETE_SYSTEM},
                {"role": "user", "content": COMPLETE_USER.format(question=question, answer=answer, fragments=text)},
            ],
            stage="complete",
            json_out=True,
            temperature=0.0,
            max_tokens=400,
        )
        data = res.data if isinstance(res.data, dict) else {}
        addition = normalize_markers(" ".join(str(data.get("addition") or "").split()))
        markers = set(SID_RE.findall(addition))
        if not addition or len(addition) > MAX_ADDITION_CHARS or not markers or not markers <= cited:
            return ""
        words = stems(addition)
        if words and len(words & stems(answer)) / len(words) >= MAX_ADDITION_OVERLAP:
            return ""
        return addition

    def _fallback(self, frags: list[Fragment], links: bool, one_line: bool) -> tuple[str, list[Citation], list[str]]:
        head = "Не удалось сформировать проверенный ответ: языковая модель сейчас недоступна."
        if not frags:
            return head + " Повторите запрос позже.", [], []
        top = frags[:3]
        items = []
        for f in top:
            first = f.unit.text.split("\n")[0]
            items.append(f"«{first[:220]}{'…' if len(first) > 220 else ''}» [{f.sid}]")
        body = head + " Наиболее релевантные положения базы знаний: " + "; ".join(items) + "."
        r = render(body, top, links=links, base_url=self.s.public_base_url, today=self.s.today, event=None, recent_days=self.s.footnote_recent_days, footnotes=False, one_line=one_line)
        return r.text, r.citations, r.footnotes

    async def answer(self, turns: list[Turn], *, mode: str = "submission", economy: bool = False, emit: Emit | None = None) -> AnswerResult:
        emit = emit or _noop
        start = time.monotonic()
        trace: list[dict] = TimedTrace(start)
        CALL_TRACE.set(trace)
        links = mode == "ui" or self.s.render_links
        one_line = mode == "submission"
        last = turns[-1].content
        context_text = "\n".join(t.content for t in turns)
        asked = "\n".join(t.content for t in turns if t.role == "user")
        history = render_history(turns)

        def elapsed() -> float:
            return time.monotonic() - start

        await emit({"type": "status", "text": "Разбираю вопрос и определяю документы"})
        t = time.monotonic()
        cache = SearchCache()
        seed = "\n".join(turn.content for turn in turns if turn.role == "user") if len(turns) > 1 else last
        warm = asyncio.create_task(self._warm_search(seed, cache))
        a = await analyze(self.llm, self.index, self.catalog, turns, self.s.today)
        trace.append({"step": "analyze", "seconds": round(time.monotonic() - t, 2), "llm_ok": a.llm_ok, "documents": a.documents, "flags": sorted(a.flags), "dropped": a.dropped})
        await emit({"type": "reasoning", "text": self._reason_analysis(a)})

        offtopic = a.scope == "offtopic" and not a.documents and "illegal_request" not in a.flags

        await emit({"type": "status", "text": "Ищу положения в базе знаний"})
        t = time.monotonic()
        plan = self._plan(a, last, seed)
        await warm
        hits = await self.retriever.search(plan, k_channel=40, k_out=60, cache=cache)
        t_search = time.monotonic()
        reused = sum(h.idx in cache.rerank for h in hits[: self.s.rerank_top_n])
        hits = await self.retriever.rerank(plan.rerank_query, hits, top_n=self.s.rerank_top_n, cache=cache)
        t_rerank = time.monotonic()
        hits = self._ensure_docs(hits, a)
        hits = self.retriever.expand(hits, limit=12)
        frags = build_fragments(self.index, hits, limit=24 if self.s.profile == "accurate" else 14)
        trace.append({"step": "retrieve", "seconds": round(time.monotonic() - t, 2), "search": round(t_search - t, 2), "rerank": round(t_rerank - t_search, 2), "reused": reused, "queries": len(plan.queries) + len(plan.hyde), "candidates": [f.key for f in frags], "dense": self.retriever.embedder is not None and not self.retriever.dense_failed, "rerank_on": self.retriever.reranker is not None and not self.retriever.rerank_failed})

        if offtopic:
            frags = frags[:OFFTOPIC_FRAGMENTS]
        if self.s.grade and frags:
            await emit({"type": "status", "text": "Оцениваю, достаточно ли найденных положений"})
            try:
                await self._grade(a, frags, history, trace)
            except Exception as exc:
                trace.append({"step": "grade", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        selected = self._select(frags, limit=12, strict=offtopic)
        if a.period:
            selected = pair_editions(selected, frags, limit=12)
        elif not HISTORY_HINT_RE.search(asked):
            selected = drop_expired(selected, self.s.today)
        if not AMENDMENT_HINT_RE.search(asked):
            selected = drop_unrelated_amendments(selected)
        if a.ambiguous:
            selected = cover_documents(selected, frags, [d for amb in a.ambiguous for d in amb["candidates"]], limit=12)
        selected = renumber(selected)
        required = edition_groups(selected) if a.period else []
        company = "" if not self.company or self.company.lower() in context_text.lower() else self.company
        await emit({"type": "reasoning", "text": "Отобраны положения: " + "; ".join(f"{f.sid} {f.doc.short} {f.unit.label}".strip() for f in selected)})

        procedural = any(w in a.standalone.lower() for w in ("порядок", "соглас", "оформ", "какие шаги", "что нужно сделать", "как действовать", "процедур"))
        think = self.s.reason_generate
        system = ANSWER_SYSTEM.format(
            company_note=COMPANY_NOTE.format(company=self.company) if self.company else "",
            today=self.s.today.strftime("%d.%m.%Y"),
            analysis_field="" if think else ANSWER_ANALYSIS_FIELD,
            steps_rule=ANSWER_STEPS_RULE if mode == "ui" and procedural else "",
            steps_field=ANSWER_STEPS_FIELD if mode == "ui" and procedural else "",
        )
        hb = f"История диалога:\n{history}\n\n" if history else ""
        ab = self._analysis_block(a)
        if offtopic:
            ab += "\n- Модуль разбора считает запрос не юридическим. Если запрос юридический, но фрагменты на него не отвечают, — status = insufficient; если запрос действительно не юридический — status = offtopic."
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": ANSWER_USER.format(history_block=hb, question=last, analysis_block=ab, fragments=self._fragments_text(a, selected) or "(фрагменты не найдены)")},
        ]
        await emit({"type": "status", "text": "Формирую варианты ответа и проверяю их по документам"})
        t = time.monotonic()

        async def check(answer: str) -> list[dict]:
            if elapsed() > self.s.question_budget * 0.7:
                return []
            return await self._verify(a, answer, selected, history)

        async def precomplete(answer: str) -> str:
            return await self._complete(a.standalone, answer, selected)

        try:
            ranked = await self._ranked_candidates(messages, think, 1 if economy else self.s.candidates, trace, required, check if self.s.verify and not economy else None, precomplete if self.s.complete and not economy else None)
        except Exception as exc:
            trace.append({"step": "generate", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            text, cites, notes = self._fallback(selected, links, one_line)
            return AnswerResult(text=text, status="answer", citations=cites, footnotes=notes, fragments=selected, analysis=a, trace=trace, seconds=elapsed(), error=str(exc)[:300])
        trace.append({"step": "generate", "seconds": round(time.monotonic() - t, 2), "status": ranked[0].get("status")})

        check_context = f"{context_text}\n{ab}"

        def formal_issues(text: str, st: str) -> list[str]:
            return deterministic_issues(text, st, selected, check_context, last, self.s.today, company, required)

        verified = [c for c in ranked[:MAX_VERIFIED] if not c.get("_issues")] or ranked[:1]
        data = next((c for c in verified if not formal_issues(*_unpack(c))), verified[0])
        answer, status = _unpack(data)
        drafted = answer
        reasoning = str(data.get("analysis") or "")
        if data.get("_reasoning"):
            reasoning = str(data["_reasoning"]) + ("\n\n" + reasoning if reasoning else "")
        await emit({"type": "reasoning", "text": reasoning})

        formal = formal_issues(answer, status)
        limit = self.s.question_budget * 0.8
        if formal and elapsed() < limit:
            feedback = "\n".join(f"- {x}" for x in formal)
            await emit({"type": "reasoning", "text": "Проверка нашла замечания, исправляю:\n" + feedback})
            t = time.monotonic()
            accepted = False
            try:
                echo = {"status": status, "answer": answer}
                if data.get("analysis"):
                    echo = {"analysis": data["analysis"], **echo}
                revised = await self._generate(messages + [{"role": "assistant", "content": json.dumps(echo, ensure_ascii=False)}, {"role": "user", "content": REVISE_USER.format(issues=feedback)}], think)
                new_answer, new_status = _unpack(revised, status)
                accepted = bool(new_answer) and len(formal_issues(new_answer, new_status)) < len(formal)
                if accepted:
                    answer, status = new_answer, new_status
                    reasoning = str(revised.get("analysis") or reasoning)
                    data["steps"] = revised.get("steps", data.get("steps"))
            except Exception as exc:
                trace.append({"step": "revise", "error": f"{type(exc).__name__}"})
            trace.append({"step": "revise", "seconds": round(time.monotonic() - t, 2), "issues": formal, "status": status, "accepted": accepted})

        ready = answer == drafted and "_addition" in data
        if self.s.complete and answer and status in COMPLETABLE and not economy and (ready or elapsed() < limit):
            t = time.monotonic()
            addition, accepted = str(data["_addition"]) if ready else "", False
            if not ready:
                try:
                    addition = await self._complete(a.standalone, answer, selected)
                except Exception as exc:
                    trace.append({"step": "complete", "error": type(exc).__name__})
            if addition:
                merged = f"{answer.rstrip()} {addition}"
                before, after = (len(formal_issues(x, status)) for x in (answer, merged))
                accepted = after <= before
                if accepted:
                    answer = merged
            trace.append({"step": "complete", "seconds": round(time.monotonic() - t, 2), "addition": addition, "accepted": accepted, "ready": ready})

        if not answer:
            text, cites, notes = self._fallback(selected, links, one_line)
            return AnswerResult(text=text, status="answer", citations=cites, footnotes=notes, fragments=selected, analysis=a, trace=trace, seconds=elapsed(), error="empty answer")

        event = a.period[1] if a.period else None
        r = render(answer, selected, links=links, base_url=self.s.public_base_url, today=self.s.today, event=event, recent_days=self.s.footnote_recent_days, one_line=one_line)
        steps = [str(x) for x in data.get("steps", []) if str(x).strip()] if isinstance(data.get("steps"), list) else []
        if steps and mode == "ui":
            rs = render("\n".join(f"{i}. {x}" for i, x in enumerate(steps, 1)), selected, links=True, base_url=self.s.public_base_url, today=self.s.today, event=event, recent_days=self.s.footnote_recent_days, footnotes=False)
            steps = rs.text.split("\n")
        return AnswerResult(text=r.text, status=status, citations=r.citations, footnotes=r.footnotes, fragments=selected, analysis=a, reasoning=reasoning, steps=steps, trace=trace, seconds=elapsed())

    def _reason_analysis(self, a: Analysis) -> str:
        parts = [f"Вопрос: {a.standalone}"]
        if a.documents:
            parts.append("Документы-кандидаты: " + ", ".join(f"{self.index.by_id[d].short} ({c:.1f})" for d, c in sorted(a.documents.items(), key=lambda x: -x[1]) if d in self.index.by_id))
        if a.ambiguous:
            parts.append("Неоднозначность: " + "; ".join(f"«{x['quote']}» → {', '.join(x['candidates'])}" for x in a.ambiguous))
        if a.period:
            parts.append(f"Дата события: {period_text(a.period)}")
        if a.user_claims:
            parts.append("Проверяю утверждения: " + "; ".join(c["claim"] for c in a.user_claims))
        if a.missing_documents:
            parts.append("Нет в базе: " + "; ".join(m["what"] or m["quote"] for m in a.missing_documents))
        return "\n".join(parts)


async def build_assistant(s: Settings, kb_path, cache_dirs, save_dir=None) -> Assistant:
    from ..providers.embed import make_embedder
    from ..providers.rerank import make_reranker

    embedder = make_embedder(s)
    reranker = make_reranker(s)
    index = await KnowledgeIndex.open(kb_path, cache_dirs, embedder, save_dir=save_dir)
    llm = LLM(s)
    verifier = LLM(s, model=s.verify_model) if s.verify_model and s.verify_model != s.llm_model else None
    probes = [llm.thinking_extra(False)]
    if s.reason_generate:
        probes.append(llm.thinking_extra(True))
    if verifier is not None:
        probes.append(verifier.thinking_extra(False))
    await asyncio.gather(*probes, warmup(embedder, reranker))
    return Assistant(s, index, llm, embedder, reranker, verifier)


async def warmup(embedder: Embedder | None, reranker: Reranker | None) -> None:
    t = time.monotonic()
    try:
        if embedder is not None:
            await embedder.embed(["прогрев модели"])
        if reranker is not None:
            await reranker.rerank("прогрев", ["прогрев модели"])
    except Exception as exc:
        print(f"Прогрев моделей не удался ({type(exc).__name__}); продолжим без него", file=sys.stderr)
        return
    if embedder is not None or reranker is not None:
        print(f"Прогрев моделей: {time.monotonic() - t:.1f}с", file=sys.stderr)
