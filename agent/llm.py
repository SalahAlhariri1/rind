"""AI provider: Meterless Nightshift (OpenAI-compatible Runtime), primary and only live provider.

Two lanes, both pinned to NVIDIA Nemotron models in Nightshift:
  fast -> quick structured steps (Investigator check selection, claim writing)
  deep -> judgement (Planner, Skeptic, Reporter)

Every call is logged with the model the Runtime says actually served it.
SAMPLE_MODE replays recorded responses (data/sample/llm_cassette.json); anything
not recorded falls back to a deterministic offline stand-in, clearly labelled
`offline-heuristic` in the trace (it is not an LLM)."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import ROOT, SETTINGS
from .trace import Trace

CASSETTE = ROOT / "data" / "sample" / "llm_cassette.json"
_lock = threading.Lock()
_gate = threading.BoundedSemaphore(max(1, SETTINGS.llm_parallel))


class LLMError(RuntimeError):
    pass


def _key(lane: str, system: str, user: str) -> str:
    return hashlib.sha256(f"{lane}\n{system}\n{user}".encode()).hexdigest()[:24]


def extract_json(text: str) -> Any:
    """Models sometimes wrap JSON in prose or code fences; pull out the first JSON value."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"no JSON in model output: {text[:200]!r}")


class LLM:
    def __init__(self, trace: Trace) -> None:
        self.trace = trace
        self.calls = 0
        self.degraded = False
        self._cassette: dict[str, Any] = {}
        if CASSETTE.exists():
            self._cassette = json.loads(CASSETTE.read_text())
        self._client = None
        if not SETTINGS.sample_mode:
            from openai import OpenAI
            if not SETTINGS.meterless_token:
                raise LLMError("METERLESS_RUNTIME_TOKEN is not set (copy it from Nightshift > Console).")
            self._client = OpenAI(
                base_url=SETTINGS.meterless_base_url,
                api_key=SETTINGS.meterless_token,
                timeout=SETTINGS.llm_timeout_s,
                max_retries=0,
                default_headers={"meterless-app": SETTINGS.meterless_app, "meterless-scope": "user"},
            )

    def models(self) -> list[str]:
        if not self._client:
            return []
        return [m.id for m in self._client.models.list().data]

    def model_for(self, lane: str) -> str:
        return SETTINGS.model_deep if lane == "deep" else SETTINGS.model_fast

    def complete_json(self, agent: str, lane: str, system: str, user: str,
                      offline: Callable[[], Any], account_id: str | None = None,
                      max_tokens: int = 1200) -> Any:
        key = _key(lane, system, user)
        requested = self.model_for(lane)
        t0 = time.time()

        if SETTINGS.sample_mode or self._client is None:
            if key in self._cassette:
                rec = self._cassette[key]
                self._log(agent, lane, requested, rec["model"] + " (recorded)", t0, account_id, rec.get("usage"))
                return extract_json(rec["text"])
            out = offline()
            self._log(agent, lane, requested, "offline-heuristic", t0, account_id, None)
            return out

        last: Exception | None = None
        thinking_off = SETTINGS.nemotron_thinking_off
        budget = max(max_tokens, SETTINGS.llm_min_tokens)  # reasoning models spend tokens before answering
        for attempt in range(SETTINGS.llm_max_retries + 1):
            try:
                kwargs: dict[str, Any] = {}
                if thinking_off:
                    # Nemotron chat templates accept enable_thinking; it stops the model spending the whole
                    # token budget on hidden reasoning and returning an empty answer.
                    kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
                with _gate:  # bounded parallelism toward the provider (free endpoints throttle bursts)
                    resp = self._client.chat.completions.create(
                        model=requested,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        temperature=0.2,
                        max_tokens=budget,
                        **kwargs,
                    )
                msg = resp.choices[0].message
                text = msg.content or ""
                if not text.strip():  # some routes put the answer next to the reasoning
                    extra = getattr(msg, "model_extra", None) or {}
                    text = extra.get("reasoning_content") or extra.get("reasoning") or ""
                used = getattr(resp, "model", None) or requested
                usage = resp.usage.model_dump() if getattr(resp, "usage", None) else None
                parsed = extract_json(text)
                self._log(agent, lane, requested, used, t0, account_id, usage, attempt=attempt)
                if SETTINGS.record_llm:
                    with _lock:
                        self._cassette[key] = {"model": used, "text": text, "usage": usage}
                        CASSETTE.parent.mkdir(parents=True, exist_ok=True)
                        CASSETTE.write_text(json.dumps(self._cassette, indent=1))
                return parsed
            except Exception as e:  # network, timeout, provider 5xx, bad JSON -> bounded retry
                last = e
                msg_e = str(e)
                if thinking_off and ("400" in msg_e[:20] or "chat_template_kwargs" in msg_e):
                    thinking_off = False  # route rejected the switch: retry without it, don't count the attempt
                    self.trace.log(agent, "llm_retry", lane=lane, attempt=attempt, error="thinking switch rejected; retrying without it", account_id=account_id)
                    continue
                if "empty_response" in msg_e:
                    budget = min(budget * 2, 16000)  # answer got cut off by reasoning: give it more room
                self.trace.log(agent, "llm_retry", lane=lane, attempt=attempt, error=msg_e[:240], account_id=account_id, next_max_tokens=budget)
                time.sleep(SETTINGS.llm_backoff_s * (attempt + 1))
        # Graceful fallback: keep the run alive, mark it degraded in the trace.
        self.degraded = True
        self.trace.log(agent, "llm_fallback", lane=lane, error=str(last)[:240], account_id=account_id)
        out = offline()
        self._log(agent, lane, requested, "offline-heuristic (fallback)", t0, account_id, None)
        return out

    def _log(self, agent, lane, requested, used, t0, account_id, usage, attempt: int = 0) -> None:
        self.calls += 1
        self.trace.log(agent, "llm_call", lane=lane, model_requested=requested, model_used=used,
                       latency_ms=int((time.time() - t0) * 1000), account_id=account_id, usage=usage, attempt=attempt)
