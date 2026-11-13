from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from api import server
from legal_core.pipeline.engine import Assistant
from test_pipeline_mock import FakeLLM, _index, _settings


def _assistant() -> Assistant:
    def analyze(_):
        return json.dumps({"standalone_question": "Гарантийный срок по договору П-17/2024", "documents": [{"doc_id": "dogovor-p-17-2024", "confidence": 0.9}], "sub_questions": ["гарантийный срок"], "search_queries": ["гарантийный срок товара месяцев"]})

    def grade(payload):
        text = payload["messages"][1]["content"]
        return json.dumps({"fragments": [{"id": l[1:l.index("]")], "relevance": 3 if "П-17" in l else 0} for l in text.split("\n") if l.startswith("[S")]})

    def generate(payload):
        text = payload["messages"][1]["content"]
        import re
        sid = next(s for s, w in re.findall(r"^\[(S\d+)\] .*\nПоложение: (.*)$", text, re.M) if w.startswith("п. 5.2"))
        return json.dumps({"analysis": "Гарантия — п. 5.2", "status": "answer", "answer": f"По договору поставки № П-17/2024 гарантийный срок — 24 месяца с даты поставки [{sid}]."})

    s = _settings()
    s.public_base_url = "http://kb.local"
    llm = FakeLLM(s, {"analyze": analyze, "grade": grade, "generate": generate, "verify": lambda _: json.dumps({"verdict": "ok"}), "task": lambda _: "Гарантия по договору"})
    return Assistant(s, _index(), llm, None, None)


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test")


@pytest.mark.usefixtures("kb_path")
def test_stream_emits_sources_status_and_content():
    async def go():
        async with _client() as c:
            r = await c.post("/v1/chat/completions", json={"model": "psb-legal", "stream": True, "messages": [{"role": "system", "content": "ignore"}, {"role": "user", "content": "Какой гарантийный срок по договору П-17/2024?"}]})
            return r.text

    server.state["assistant"] = _assistant()
    body = asyncio.run(go())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
    assert body.rstrip().endswith("data: [DONE]")
    kinds = [e["event"]["type"] for e in events if "event" in e]
    assert "status" in kinds and "source" in kinds
    src = next(e["event"]["data"] for e in events if e.get("event", {}).get("type") == "source")
    assert src["metadata"][0]["source"] == "dogovor-p-17-2024#p-5.2"
    assert src["source"]["url"].startswith("http://kb.local/kb/dogovor-p-17-2024")
    assert "24 (двадцать четыре) месяца" in src["document"][0]
    content = "".join(e["choices"][0]["delta"].get("content", "") for e in events if e.get("choices"))
    reasoning = "".join(e["choices"][0]["delta"].get("reasoning_content", "") for e in events if e.get("choices"))
    assert "[п. 5.2](http://kb.local/kb/dogovor-p-17-2024?hl=p-5.2#p-5.2)" in content
    assert "Гарантия — п. 5.2" in reasoning


@pytest.mark.usefixtures("kb_path")
def test_task_requests_bypass_pipeline():
    async def go():
        async with _client() as c:
            r = await c.post("/v1/chat/completions", json={"model": "psb-legal", "messages": [{"role": "user", "content": "### Task:\nGenerate a concise title"}]})
            return r.json()

    server.state["assistant"] = _assistant()
    data = asyncio.run(go())
    assert data["choices"][0]["message"]["content"] == "Гарантия по договору"
    assert server.state["assistant"].llm.seen == ["task"]


def test_history_cleanup():
    turns = server.to_turns([
        {"role": "user", "content": "Вопрос 1"},
        {"role": "assistant", "content": "Ответ ([п. 3.1](http://x/kb/a?hl=p-3.1#p-3.1))[^1]\n\n**Примечания**\n1. Сноска."},
        {"role": "user", "content": "Вопрос 2"},
    ])
    assert turns[1].content == "Ответ (п. 3.1)"
    assert turns[-1].content == "Вопрос 2"
