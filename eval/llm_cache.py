from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from legal_core.providers.llm import LLM, LLMResult

stats = {"hits": 0, "misses": 0}


def _key(base_url: str, payload: dict, stage: str = "") -> str:
    body = {"base": base_url, "payload": payload}
    if ":" in stage:
        body["stage"] = stage
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def install(cache_dir: str | os.PathLike | None = None) -> Path | None:
    cache_dir = cache_dir or os.environ.get("EVAL_LLM_CACHE")
    if not cache_dir:
        return None
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    original = LLM._raw

    async def cached(self: LLM, payload: dict, stage: str) -> LLMResult:
        key = _key(self.s.llm_base_url, payload, stage)
        path = root / key[:2] / f"{key}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            stats["hits"] += 1
            self.usage.add(stage, data.get("usage") or {}, 0.0)
            return LLMResult(content=data["content"], reasoning=data.get("reasoning", ""), usage=data.get("usage") or {}, latency=0.0)
        res = await original(self, payload, stage)
        stats["misses"] += 1
        if not res.content.strip() or not (res.usage or {}).get("prompt_tokens"):
            return res
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps({"content": res.content, "reasoning": res.reasoning, "usage": res.usage, "latency": res.latency}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        return res

    LLM._raw = cached
    return root
