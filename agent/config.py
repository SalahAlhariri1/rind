"""Settings, loaded from .env (never hard-code secrets)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # sample mode can run without python-dotenv
    load_dotenv = None

ROOT = Path(__file__).resolve().parents[1]
if load_dotenv:
    load_dotenv(ROOT / ".env")


def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    sample_mode: bool
    # Zetaris (data layer)
    zetaris_rest_url: str
    zetaris_org_id: str
    zetaris_api_key: str
    zetaris_timeout_s: int
    # Meterless Nightshift (AI provider)
    meterless_base_url: str
    meterless_token: str
    meterless_app: str
    model_fast: str
    model_deep: str
    llm_timeout_s: int
    llm_max_retries: int
    llm_min_tokens: int
    llm_backoff_s: float
    llm_parallel: int
    nemotron_thinking_off: bool
    # Agent budgets (bounded runs)
    max_deep_dives: int
    max_skeptic_rounds: int
    concurrency: int
    record_llm: bool
    as_of: str


def load() -> Settings:
    default_model = os.getenv("METERLESS_MODEL", "meterless/auto")
    return Settings(
        sample_mode=_flag("SAMPLE_MODE", "false"),
        zetaris_rest_url=os.getenv("ZETARIS_REST_URL", "http://localhost:8888/api/v1.0").rstrip("/"),
        zetaris_org_id=os.getenv("ZETARIS_ORG_ID", "1"),
        zetaris_api_key=os.getenv("ZETARIS_API_KEY", ""),
        zetaris_timeout_s=int(os.getenv("ZETARIS_TIMEOUT_S", "120")),
        meterless_base_url=os.getenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1").rstrip("/"),
        meterless_token=os.getenv("METERLESS_RUNTIME_TOKEN", ""),
        meterless_app=os.getenv("METERLESS_APP", "rind"),
        model_fast=os.getenv("METERLESS_MODEL_FAST", default_model),
        model_deep=os.getenv("METERLESS_MODEL_DEEP", default_model),
        llm_timeout_s=int(os.getenv("LLM_TIMEOUT_S", "180")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
        llm_min_tokens=int(os.getenv("LLM_MIN_TOKENS", "4096")),
        llm_backoff_s=float(os.getenv("LLM_BACKOFF_S", "4")),
        llm_parallel=int(os.getenv("LLM_PARALLEL", "2")),
        nemotron_thinking_off=_flag("NEMOTRON_THINKING_OFF", "true"),
        max_deep_dives=int(os.getenv("RIND_MAX_DEEP_DIVES", "26")),
        max_skeptic_rounds=int(os.getenv("RIND_MAX_SKEPTIC_ROUNDS", "2")),
        concurrency=int(os.getenv("RIND_CONCURRENCY", "4")),
        record_llm=_flag("RIND_RECORD_LLM", "false"),
        as_of=os.getenv("RIND_AS_OF", "2026-10-19"),
    )


SETTINGS = load()
