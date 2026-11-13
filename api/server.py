from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.viewer import doc_page, index_page, unit_payload  # noqa: E402
from legal_core.config import Settings, load_env_file  # noqa: E402
from legal_core.dialog import Turn  # noqa: E402
from legal_core.pipeline.engine import AnswerResult, Assistant, build_assistant  # noqa: E402
from legal_core.providers.http import close_all  # noqa: E402

load_env_file(ROOT / ".env.dev")

MODELS = {"psb-legal": {"name": "ПСБ Юрист"}}
TASK_RE = re.compile(r"^\s*###\s*Task:", re.M)
LINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?://|/)[^)]+\)")
FOOTNOTE_BLOCK_RE = re.compile(r"\n+\*\*Примечания\*\*\n.*$", re.S)

state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = Settings.from_env()
    s.render_links = True
    kb = s.data_dir / "kb.zip"
    if not kb.exists():
        kb = next((p for p in (s.data_dir / "knowledge-base.zip", s.data_dir / "knowledge_base.zip", s.data_dir) if p.exists()), kb)
    state["settings"] = s
    if not s.llm_api_key:
        print("LLM_API_KEY не задан: впишите ключ в .env (строка LLM_API_KEY=) или передайте переменную окружения и перезапустите сервис", file=sys.stderr, flush=True)
    state["assistant"] = await build_assistant(s, kb, [s.index_dir], save_dir=s.index_dir)
    yield
    await close_all()


app = FastAPI(title="ПСБ Юридический ассистент", lifespan=lifespan)


def assistant() -> Assistant:
    return state["assistant"]


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") in (None, "text"))
    return ""


def _clean_assistant(text: str) -> str:
    text = FOOTNOTE_BLOCK_RE.sub("", text)
    text = re.sub(r"\[\^\d+\]", "", text)
    text = re.sub(r"<details.*?</details>", "", text, flags=re.S)
    return LINK_RE.sub(r"\1", text).strip()


def to_turns(messages: list[dict]) -> list[Turn]:
    turns: list[Turn] = []
    for m in messages:
        role = m.get("role")
        content = _text(m.get("content"))
        if role == "user" and content.strip():
            turns.append(Turn("user", content.strip()))
        elif role == "assistant" and content.strip():
            turns.append(Turn("assistant", _clean_assistant(content)))
    if not turns or turns[-1].role != "user":
        raise HTTPException(400, "последнее сообщение должно быть от пользователя")
    return turns[-12:]


def source_events(res: AnswerResult) -> list[dict]:
    out = []
    for c in res.citations:
        out.append({
            "type": "source",
            "data": {
                "source": {"id": f"{c.doc_id}#{c.anchor}", "name": f"{c.label} · {c.title}", "url": c.url},
                "document": [c.text],
                "metadata": [{"source": f"{c.doc_id}#{c.anchor}", "name": f"[{c.n}] {c.label} — {c.title}", "url": c.url}],
            },
        })
    return out


def final_text(res: AnswerResult) -> str:
    text = res.text
    if res.steps:
        text += "\n\n**Порядок действий**\n" + "\n".join(res.steps)
    return text


def _chunk(cid: str, model: str, delta: dict | None = None, finish: str | None = None, extra: dict | None = None) -> str:
    payload: dict[str, Any] = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if extra:
        payload.update(extra)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _task_passthrough(body: dict) -> dict:
    llm = assistant().llm
    res = await llm.chat([{"role": m.get("role", "user"), "content": _text(m.get("content"))} for m in body.get("messages", [])], stage="task", max_tokens=400, temperature=0.2)
    return {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion", "created": int(time.time()), "model": body.get("model"), "choices": [{"index": 0, "message": {"role": "assistant", "content": res.content}, "finish_reason": "stop"}]}


@app.get("/health")
async def health() -> dict:
    a = assistant()
    r = a.retriever
    return {
        "ok": True,
        "llm_key": bool(a.s.llm_api_key),
        "documents": len(a.index.docs),
        "fragments": len(a.index.chunks),
        "dense_search": r.embedder is not None and not r.dense_failed,
        "embedder": r.embedder.name if r.embedder else None,
        "reranker": r.reranker.name if r.reranker else None,
        "model": a.s.llm_model,
        "thinking": a.llm.thinking_modes,
    }


@app.get("/v1/models")
async def models() -> dict:
    return {"object": "list", "data": [{"id": mid, "object": "model", "owned_by": "psb-legal", "name": m["name"]} for mid, m in MODELS.items()]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model") or "psb-legal"
    messages = body.get("messages") or []
    last_user = next((_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
    if TASK_RE.search(last_user):
        data = await _task_passthrough(body)
        if body.get("stream"):
            async def task_stream():
                cid = data["id"]
                yield _chunk(cid, model, {"role": "assistant", "content": data["choices"][0]["message"]["content"]})
                yield _chunk(cid, model, {}, "stop")
                yield "data: [DONE]\n\n"
            return StreamingResponse(task_stream(), media_type="text/event-stream")
        return JSONResponse(data)
    turns = to_turns(messages)
    cid = f"chatcmpl-{uuid.uuid4().hex}"
    if not body.get("stream"):
        res = await assistant().answer(turns, mode="ui")
        return JSONResponse({
            "id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": final_text(res), "reasoning_content": res.reasoning}, "finish_reason": "stop"}],
            "sources": [e["data"] for e in source_events(res)],
            "legal_trace": res.trace_dict(),
        })

    queue: asyncio.Queue = asyncio.Queue()

    async def emit(ev: dict) -> None:
        await queue.put(ev)

    async def worker() -> None:
        try:
            res = await assistant().answer(turns, mode="ui", emit=emit)
            await queue.put({"type": "result", "result": res})
        except Exception as exc:
            print(f"chat error: {type(exc).__name__}: {exc}", file=sys.stderr)
            await queue.put({"type": "error", "text": f"Не удалось сформировать ответ: {type(exc).__name__}"})

    async def stream():
        task = asyncio.create_task(worker())
        yield _chunk(cid, model, {"role": "assistant", "content": ""})
        try:
            while True:
                ev = await queue.get()
                kind = ev.get("type")
                if kind == "status":
                    yield _chunk(cid, model, extra={"event": {"type": "status", "data": {"description": ev["text"], "done": False}}})
                elif kind == "reasoning" and ev.get("text"):
                    yield _chunk(cid, model, {"reasoning_content": ev["text"].strip() + "\n\n"})
                elif kind == "error":
                    yield _chunk(cid, model, extra={"event": {"type": "status", "data": {"description": "Ошибка", "done": True}}})
                    yield _chunk(cid, model, {"content": ev["text"]})
                    break
                elif kind == "result":
                    res: AnswerResult = ev["result"]
                    for se in source_events(res):
                        yield _chunk(cid, model, extra={"event": se})
                    n = len(res.citations)
                    yield _chunk(cid, model, extra={"event": {"type": "status", "data": {"description": f"Готово · источников: {n} · {res.seconds:.1f} с", "done": True}}})
                    text = final_text(res)
                    for i in range(0, len(text), 160):
                        yield _chunk(cid, model, {"content": text[i:i + 160]})
                    break
            yield _chunk(cid, model, {}, "stop")
            yield "data: [DONE]\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/kb", response_class=HTMLResponse)
async def kb_index() -> str:
    return index_page(assistant().index)


@app.get("/kb/{doc_id}", response_class=HTMLResponse)
async def kb_doc(doc_id: str, hl: str | None = None) -> str:
    doc = assistant().index.by_id.get(doc_id)
    if doc is None:
        raise HTTPException(404, "документ не найден")
    return doc_page(assistant().index, doc, hl)


@app.get("/api/kb/{doc_id}/{anchor}")
async def kb_unit(doc_id: str, anchor: str) -> dict:
    idx = assistant().index
    doc = idx.by_id.get(doc_id)
    data = unit_payload(idx, doc, anchor) if doc else {}
    if not data:
        raise HTTPException(404, "положение не найдено")
    return data
