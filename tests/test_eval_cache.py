from __future__ import annotations

import asyncio

from legal_core.config import Settings
from legal_core.providers.llm import LLM, LLMResult


def test_llm_cache_replays_identical_requests(tmp_path, monkeypatch):
    calls = []

    async def fake_raw(self, payload, stage):
        calls.append(stage)
        self.usage.add(stage, {"prompt_tokens": 7, "completion_tokens": 3}, 0.5)
        return LLMResult(content=f"ответ {payload['messages'][0]['content']}", usage={"prompt_tokens": 7, "completion_tokens": 3})

    monkeypatch.setattr(LLM, "_raw", fake_raw)
    from eval import llm_cache

    monkeypatch.setattr(llm_cache, "stats", {"hits": 0, "misses": 0})
    assert llm_cache.install(tmp_path) == tmp_path
    s = Settings()
    s.llm_base_url = "http://fake"
    s.reasoning = "off"
    llm = LLM(s)
    first = asyncio.run(llm.chat([{"role": "user", "content": "a"}], stage="x"))
    again = asyncio.run(llm.chat([{"role": "user", "content": "a"}], stage="x"))
    other = asyncio.run(llm.chat([{"role": "user", "content": "b"}], stage="x"))
    assert first.content == again.content == "ответ a" and other.content == "ответ b"
    assert calls == ["x", "x"]
    assert llm_cache.stats == {"hits": 1, "misses": 2}
    assert llm.usage.by_stage["x"]["calls"] == 3 and llm.usage.by_stage["x"]["prompt"] == 21


def test_llm_cache_keeps_candidate_variants_apart(tmp_path, monkeypatch):
    calls = []

    async def fake_raw(self, payload, stage):
        calls.append(stage)
        return LLMResult(content=f"ответ {stage}", usage={"prompt_tokens": 7, "completion_tokens": 3})

    monkeypatch.setattr(LLM, "_raw", fake_raw)
    from eval import llm_cache

    llm_cache.install(tmp_path)
    s = Settings()
    s.llm_base_url = "http://fake"
    s.reasoning = "off"
    llm = LLM(s)
    msgs = [{"role": "user", "content": "a"}]
    first = asyncio.run(llm.chat(msgs, stage="generate:1"))
    second = asyncio.run(llm.chat(msgs, stage="generate:2"))
    assert first.content != second.content
    assert asyncio.run(llm.chat(msgs, stage="generate:1")).content == first.content
