from __future__ import annotations

import hashlib
import json
import os
import re

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
FAIL_AUX = os.getenv("MOCK_FAIL_AUX") == "1"
FAIL_LLM_EVERY = int(os.getenv("MOCK_FAIL_LLM_EVERY", "0"))
counter = {"n": 0}


def _vec(text: str) -> list[float]:
    seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
    v = np.random.default_rng(seed).standard_normal(1024).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


@app.post("/embeddings")
async def embeddings(req: Request):
    if FAIL_AUX:
        return JSONResponse({"error": "boom"}, status_code=500)
    body = await req.json()
    return {"data": [{"index": i, "embedding": _vec(t)} for i, t in enumerate(body["input"])]}


@app.post("/rerank")
async def rerank(req: Request):
    if FAIL_AUX:
        return JSONResponse({"error": "boom"}, status_code=500)
    body = await req.json()
    return {"results": [{"index": i, "relevance_score": 1.0 / (i + 1)} for i in range(len(body["documents"]))]}


@app.post("/chat/completions")
async def chat(req: Request):
    counter["n"] += 1
    if FAIL_LLM_EVERY and counter["n"] % FAIL_LLM_EVERY == 0:
        return JSONResponse({"error": "overloaded"}, status_code=503)
    body = await req.json()
    system = body["messages"][0]["content"] if body["messages"] else ""
    user = body["messages"][-1]["content"]
    if "модуль разбора" in system:
        content = json.dumps({"standalone_question": user.split("Последнее сообщение пользователя:")[-1].strip()[:300], "scope": "legal", "sub_questions": [], "search_queries": []})
    elif "модуль оценки" in system:
        sids = re.findall(r"^\[(S\d+)\]", user, re.M)
        content = json.dumps({"fragments": [{"id": s, "relevance": 3 if i < 2 else 0} for i, s in enumerate(sids)]})
    elif "строгий проверяющий" in system:
        content = json.dumps({"verdict": "ok", "issues": []})
    elif "юридический ИИ-ассистент" in system:
        sids = re.findall(r"^\[(S\d+)\]", "\n".join(m["content"] for m in body["messages"] if m["role"] == "user"), re.M)
        content = json.dumps({"status": "answer", "answer": f"Ответ по найденному положению [{sids[0]}]." if sids else "В базе нет данных."})
    else:
        content = "ok"
    message = {"role": "assistant", "content": content}
    if (body.get("thinking") or {}).get("type") != "disabled":
        message["reasoning_content"] = "Рассуждение модели."
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
