from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from .http import ApiError, post_json

THINKING_VARIANTS: dict[bool, list[dict[str, Any]]] = {
    True: [{"thinking": {"type": "enabled"}}, {"reasoning_effort": "high"}, {"reasoning": {"enabled": True}}],
    False: [{"thinking": {"type": "disabled"}}, {"reasoning_effort": "none"}, {"reasoning": {"enabled": False}}],
}
THINKING_HEADROOM = 12000
CALL_TRACE: ContextVar[list[dict] | None] = ContextVar("llm_call_trace", default=None)
EMPTY_RETRIES = 2
PROBE_PROMPT = [{"role": "user", "content": "Сколько будет 12*13? Ответь числом."}]


@dataclass
class LLMResult:
    content: str
    reasoning: str = ""
    usage: dict = field(default_factory=dict)
    latency: float = 0.0
    data: Any = None


@dataclass
class Usage:
    calls: int = 0
    prompt: int = 0
    completion: int = 0
    seconds: float = 0.0
    by_stage: dict = field(default_factory=dict)

    def add(self, stage: str, usage: dict, seconds: float) -> None:
        p, c = int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)
        self.calls += 1
        self.prompt += p
        self.completion += c
        self.seconds += seconds
        st = self.by_stage.setdefault(stage, {"calls": 0, "prompt": 0, "completion": 0, "seconds": 0.0, "max_prompt": 0})
        st["calls"] += 1
        st["prompt"] += p
        st["completion"] += c
        st["seconds"] += seconds
        st["max_prompt"] = max(st["max_prompt"], p)


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    text = text.strip()
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no json object in response")
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("unterminated json")
    candidate = text[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
        return json.loads(cleaned)


class LLM:
    def __init__(self, s: Settings, model: str | None = None) -> None:
        self.s = s
        self.model = model or s.llm_model
        self.sem = asyncio.Semaphore(s.llm_concurrency)
        self.usage = Usage()
        self._thinking: dict[bool, dict] = {}
        self._json_supported = {False: s.json_mode, True: s.json_mode}
        self._probe_lock = asyncio.Lock()

    async def _raw(self, payload: dict, stage: str) -> LLMResult:
        for attempt in range(EMPTY_RETRIES + 1):
            res = await self._call(payload, stage)
            if res.content.strip() or res.reasoning.strip():
                return res
            print(f"{stage}: пустой ответ модели, повтор {attempt + 1}", file=sys.stderr)
        raise ApiError(502, "empty completion")

    async def _call(self, payload: dict, stage: str) -> LLMResult:
        async with self.sem:
            t = time.monotonic()
            data = await post_json(self.s.llm_base_url, "/chat/completions", self.s.llm_api_key, payload, self.s.request_timeout)
            dt = time.monotonic() - t
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
        usage = data.get("usage") or {}
        self.usage.add(stage, usage, dt)
        reasoning = reasoning if isinstance(reasoning, str) else ""
        sink = CALL_TRACE.get()
        if sink is not None:
            sink.append({"step": f"llm:{stage}", "seconds": round(dt, 2), "prompt": usage.get("prompt_tokens"), "completion": usage.get("completion_tokens"), "reasoning_chars": len(reasoning)})
        return LLMResult(content=content, reasoning=reasoning, usage=usage, latency=dt)

    @property
    def thinking_modes(self) -> dict[str, dict]:
        return {("on" if k else "off"): v for k, v in self._thinking.items()}

    async def thinking_extra(self, on: bool) -> dict:
        if self.s.reasoning == "off":
            return {}
        if on not in self._thinking:
            async with self._probe_lock:
                if on not in self._thinking:
                    self._thinking[on] = await self._probe_thinking(on)
        return self._thinking[on]

    async def _probe_thinking(self, on: bool) -> dict:
        what = "включения" if on else "отключения"
        base = {"model": self.model, "messages": PROBE_PROMPT, "max_tokens": 1024}
        for extra in THINKING_VARIANTS[on]:
            try:
                res = await self._raw({**base, **extra}, "probe")
            except ApiError as exc:
                if exc.status in (400, 404, 422):
                    continue
                print(f"Проверка {what} рассуждения не удалась: {exc}", file=sys.stderr)
                return {}
            except Exception as exc:
                print(f"Проверка {what} рассуждения не удалась: {type(exc).__name__}", file=sys.stderr)
                return {}
            if bool(res.reasoning) == on:
                print(f"Способ {what} рассуждения: {json.dumps(extra)}", file=sys.stderr)
                return extra
        print(f"API не поддерживает управление рассуждением ({what}); используется режим по умолчанию", file=sys.stderr)
        return {}

    async def chat(
        self,
        messages: list[dict],
        *,
        stage: str,
        json_out: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning: bool | None = False,
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.s.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.s.max_tokens,
        }
        extra = await self.thinking_extra(reasoning) if reasoning is not None else {}
        thinking = bool(reasoning and extra)
        payload.update(extra)
        if thinking:
            payload.pop("temperature", None)
            payload["max_tokens"] += THINKING_HEADROOM
        if json_out and self._json_supported[thinking]:
            payload["response_format"] = {"type": "json_object"}
        try:
            res = await self._raw(payload, stage)
        except ApiError as exc:
            if json_out and "response_format" in payload and exc.status in (400, 422):
                self._json_supported[thinking] = False
                payload.pop("response_format", None)
                res = await self._raw(payload, stage)
            else:
                raise
        if not json_out:
            return res
        try:
            res.data = extract_json(res.content)
            return res
        except (ValueError, json.JSONDecodeError):
            fix = messages + [
                {"role": "assistant", "content": res.content[:6000]},
                {"role": "user", "content": "Ответ не является корректным JSON. Верни только корректный JSON-объект по заданной схеме, без пояснений."},
            ]
            payload["messages"] = fix
            res2 = await self._raw(payload, stage + ":fix")
            res2.data = extract_json(res2.content)
            res2.reasoning = res.reasoning
            return res2
