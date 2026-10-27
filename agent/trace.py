"""Run trace: one JSON line per step, so anyone can see how Rind reached its output,
including which model handled every request (from the provider's response)."""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT

LOG_DIR = ROOT / "logs"


class Trace:
    def __init__(self, run_id: str | None = None, mode: str = "live") -> None:
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.mode = mode
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        LOG_DIR.mkdir(exist_ok=True)
        self.path = LOG_DIR / f"run_{self.run_id}.jsonl"
        self._t0 = time.time()
        self._listeners: list = []

    def on_event(self, fn) -> None:
        self._listeners.append(fn)

    def log(self, agent: str, event: str, **fields: Any) -> dict[str, Any]:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "t_s": round(time.time() - self._t0, 2),
            "run_id": self.run_id,
            "mode": self.mode,
            "agent": agent,
            "event": event,
            **fields,
        }
        with self._lock:
            self.events.append(rec)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        for fn in self._listeners:
            try:
                fn(rec)
            except Exception:
                pass
        return rec

    def model_usage(self) -> dict[str, int]:
        """How many LLM calls each model actually served."""
        out: dict[str, int] = {}
        for e in self.events:
            if e["event"] == "llm_call":
                m = e.get("model_used") or "unknown"
                out[m] = out.get(m, 0) + 1
        return out
