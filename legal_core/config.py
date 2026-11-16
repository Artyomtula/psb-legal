from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value and value != name:
            return value
    return default


def env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(env(name))))
    except ValueError:
        return default


def env_float(name: str, default: float, low: float = 0.0, high: float = 2.0) -> float:
    try:
        return max(low, min(high, float(env(name))))
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = env(name).lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on", "да")


def load_env_file(path: str | Path) -> None:
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _key(names: tuple[str, ...], file_var: str) -> str:
    value = env(*names)
    if value:
        return value
    path = env(file_var)
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip()
    return ""


@dataclass
class Settings:
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    judge_model: str = ""
    verify_model: str = ""
    emb_base_url: str = ""
    emb_api_key: str = ""
    emb_model: str = "baai/bge-m3"
    emb_provider: str = "auto"
    rerank_base_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = ""
    rerank_provider: str = "auto"
    temperature: float = 0.0
    max_tokens: int = 2048
    max_workers: int = 6
    llm_concurrency: int = 12
    request_timeout: float = 90.0
    question_budget: float = 150.0
    reasoning: str = "auto"
    json_mode: bool = True
    today: date = field(default_factory=date.today)
    render_links: bool = False
    public_base_url: str = "http://localhost:8000"
    footnote_recent_days: int = 365
    data_dir: Path = Path("data")
    index_dir: Path = Path("artifacts/index")
    local_emb_model: str = "BAAI/bge-m3"
    local_rerank_model: str = "BAAI/bge-reranker-v2-m3"
    local_device: str = "auto"
    verify: bool = True
    complete: bool = True
    grade: bool = True
    reason_generate: bool = True
    candidates: int = 3
    rerank_top_n: int = 24
    profile: str = "accurate"

    @classmethod
    def from_env(cls) -> "Settings":
        base = env("LLM_BASE_URL", "BASE_URL", "OPENAI_BASE_URL").rstrip("/")
        key = _key(("LLM_API_KEY", "OPENAI_API_KEY"), "LLM_API_KEY_FILE")
        today_raw = env("TODAY")
        return cls(
            llm_base_url=base,
            llm_api_key=key,
            llm_model=env("LLM_MODEL", "GEN_MODEL", "OPENAI_MODEL", default="deepseek/deepseek-v4.1-flash"),
            judge_model=env("JUDGE_MODEL"),
            verify_model=env("VERIFY_MODEL"),
            emb_base_url=env("EMB_BASE_URL", default=base).rstrip("/"),
            emb_api_key=_key(("EMBEDDER_API_KEY",), "EMBEDDER_API_KEY_FILE") or key,
            emb_model=env("EMB_MODEL", default="baai/bge-m3"),
            emb_provider=env("EMB_PROVIDER", default="auto").lower(),
            rerank_base_url=env("RERANK_BASE_URL", default=base).rstrip("/"),
            rerank_api_key=_key(("RERANKER_API_KEY",), "RERANKER_API_KEY_FILE") or key,
            rerank_model=env("RERANKER_MODEL", default="cohere/rerank-5-fast"),
            rerank_provider=env("RERANK_PROVIDER", default="auto").lower(),
            temperature=env_float("TEMPERATURE", 0.0),
            max_tokens=env_int("MAX_TOKENS", 2048, 256, 16384),
            max_workers=env_int("MAX_WORKERS", 6, 1, 32),
            llm_concurrency=env_int("LLM_CONCURRENCY", 12, 1, 64),
            request_timeout=env_float("REQUEST_TIMEOUT", 90.0, 5.0, 600.0),
            question_budget=env_float("QUESTION_BUDGET", 150.0, 10.0, 1200.0),
            reasoning=env("REASONING", default="auto").lower(),
            json_mode=env_bool("JSON_MODE", True),
            today=date.fromisoformat(today_raw) if today_raw else date.today(),
            render_links=env_bool("RENDER_LINKS", False),
            public_base_url=env("PUBLIC_BASE_URL", default="http://localhost:8000").rstrip("/"),
            footnote_recent_days=env_int("FOOTNOTE_RECENT_DAYS", 365, 0, 3650),
            data_dir=Path(env("DATA_DIR", default="data")),
            index_dir=Path(env("INDEX_DIR", default="artifacts/index")),
            local_emb_model=env("LOCAL_EMB_MODEL", default="BAAI/bge-m3"),
            local_rerank_model=env("LOCAL_RERANK_MODEL", default="BAAI/bge-reranker-v2-m3"),
            local_device=env("LOCAL_DEVICE", default="auto").lower(),
            verify=env_bool("VERIFY", True),
            complete=env_bool("COMPLETE", True),
            grade=env_bool("GRADE", True),
            reason_generate=env_bool("REASON_GENERATE", True),
            candidates=env_int("CANDIDATES", 3, 1, 9),
            rerank_top_n=env_int("RERANK_TOP_N", 24, 8, 100),
            profile=env("PROFILE", default="accurate").lower(),
        )
