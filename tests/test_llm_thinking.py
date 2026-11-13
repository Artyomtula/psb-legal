from __future__ import annotations

import asyncio

from legal_core.config import Settings
from legal_core.providers.http import ApiError
from legal_core.providers.llm import LLM, LLMResult, THINKING_HEADROOM


class ScriptedLLM(LLM):
    def __init__(self, behaviour):
        s = Settings()
        s.llm_base_url = "http://fake"
        super().__init__(s)
        self.behaviour = behaviour
        self.payloads = []

    async def _raw(self, payload, stage):
        self.payloads.append((stage, payload))
        return LLMResult(content="{}", reasoning="думаю" if self.behaviour(payload) else "")


def deepseek(payload):
    t = payload.get("thinking", {}).get("type")
    return t != "disabled"


def openrouter(payload):
    if "thinking" in payload:
        raise ApiError(400, "unknown field thinking")
    return payload.get("reasoning", {}).get("enabled", True)


def plain(_):
    return False


def test_deepseek_style_thinking_control():
    llm = ScriptedLLM(deepseek)
    asyncio.run(llm.chat([{"role": "user", "content": "x"}], stage="analyze", json_out=True, max_tokens=500))
    asyncio.run(llm.chat([{"role": "user", "content": "x"}], stage="generate", json_out=True, max_tokens=500, reasoning=True))
    fast = next(p for st, p in llm.payloads if st == "analyze")
    deep = next(p for st, p in llm.payloads if st == "generate")
    assert fast["thinking"] == {"type": "disabled"} and "temperature" in fast and fast["max_tokens"] == 500
    assert deep["thinking"] == {"type": "enabled"} and "temperature" not in deep and deep["max_tokens"] == 500 + THINKING_HEADROOM
    assert llm.thinking_modes == {"off": {"thinking": {"type": "disabled"}}, "on": {"thinking": {"type": "enabled"}}}


def test_falls_through_rejected_and_ignored_variants():
    llm = ScriptedLLM(openrouter)
    assert asyncio.run(llm.thinking_extra(False)) == {"reasoning": {"enabled": False}}
    assert asyncio.run(llm.thinking_extra(True)) == {"reasoning_effort": "high"}


def test_no_reasoning_support_keeps_defaults():
    llm = ScriptedLLM(plain)
    assert asyncio.run(llm.thinking_extra(True)) == {}
    asyncio.run(llm.chat([{"role": "user", "content": "x"}], stage="generate", max_tokens=500, reasoning=True))
    payload = llm.payloads[-1][1]
    assert "temperature" in payload and payload["max_tokens"] == 500


def test_probe_failure_is_cached_and_not_retried():
    def down(_):
        raise ApiError(503, "overloaded")

    llm = ScriptedLLM(down)
    assert asyncio.run(llm.thinking_extra(False)) == {}
    assert asyncio.run(llm.thinking_extra(False)) == {}
    assert sum(1 for st, _ in llm.payloads if st == "probe") == 1


def test_reasoning_off_sends_nothing():
    llm = ScriptedLLM(deepseek)
    llm.s.reasoning = "off"
    asyncio.run(llm.chat([{"role": "user", "content": "x"}], stage="analyze"))
    assert [st for st, _ in llm.payloads] == ["analyze"]
    assert "thinking" not in llm.payloads[0][1]
