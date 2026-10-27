"""Account memory across sessions (the Meterless H-MEM feature).

What it does for the user: Rind remembers what was decided and tried for each
account in earlier weekly runs ("offered training on Oct 5, usage kept falling"),
recalls it on the next run, and the Planner/Reporter change course because of it.

Backend: the Meterless H-MEM engine in hmem/ (TypeScript, adapted from the Meterless
reference, passes the official conformance suite 13/13: evidence/hmem_conformance.txt),
reached through hmem/bridge.ts. `LocalJsonMemory` is a plain-JSON baseline kept for
comparison (RIND_MEMORY_BACKEND=local). tests/test_memory.py is the cross-session test.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import ROOT

DEFAULT_PATH = ROOT / "memory" / "account_memory.json"


class MemoryStore(Protocol):
    backend: str

    def recall(self, account_id: str, query: str = "", k: int = 5) -> list[dict[str, Any]]: ...
    def remember(self, account_id: str, record: dict[str, Any]) -> None: ...
    def stats(self) -> dict[str, Any]: ...


class LocalJsonMemory:
    backend = "local-json"

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or os.getenv("RIND_MEMORY_PATH", DEFAULT_PATH))
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text() or "{}")

    def recall(self, account_id: str, query: str = "", k: int = 5) -> list[dict[str, Any]]:
        items = self._load().get(account_id, [])
        return sorted(items, key=lambda r: r.get("recorded_at", ""), reverse=True)[:k]

    def remember(self, account_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            data = self._load()
            rec = {"recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
            data.setdefault(account_id, []).append(rec)
            self.path.write_text(json.dumps(data, indent=1))

    def stats(self) -> dict[str, Any]:
        data = self._load()
        return {"backend": self.backend, "accounts": len(data), "records": sum(len(v) for v in data.values())}


class HMemStore:
    """Meterless H-MEM backend: talks to the H-MEM engine (hmem/src, conformance 13/13)
    through hmem/bridge.ts, a long-running Node process speaking JSON lines.

    Mapping to the H-MEM model: one memory record per decision/action/context note,
    scoped to the account with chatId=account_id, provenance source "rind:<who>",
    tags "kind:<type>" and "week:<date>". recall() is chat-scoped hybrid retrieval
    (the spec's ranking formula); helpful() applies the spec's feedback delta."""
    backend = "meterless-h-mem"

    def __init__(self, path: Path | None = None) -> None:
        import shutil
        import subprocess
        self.dir = Path(path or os.getenv("RIND_HMEM_DIR", ROOT / "memory" / "hmem"))
        self.dir.mkdir(parents=True, exist_ok=True)
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not npx:
            raise RuntimeError("H-MEM needs Node.js (npx). Install Node 20+ and run `npm install` in hmem/.")
        self._lock = threading.Lock()
        self._p = subprocess.Popen([npx, "tsx", str(ROOT / "hmem" / "bridge.ts"), str(self.dir)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding="utf-8", cwd=str(ROOT / "hmem"))
        ready = json.loads(self._p.stdout.readline() or "{}")
        if not ready.get("ready"):
            raise RuntimeError("H-MEM bridge failed to start: " + (self._p.stderr.read()[:300] if self._p.stderr else ""))

    def _call(self, **req: Any) -> Any:
        with self._lock:
            self._p.stdin.write(json.dumps(req) + "\n")
            self._p.stdin.flush()
            out = json.loads(self._p.stdout.readline())
        if not out.get("ok"):
            raise RuntimeError(f"H-MEM {req.get('op')}: {out.get('error')}")
        return out["result"]

    @staticmethod
    def _to_dict(m: dict[str, Any], relevance: float | None = None) -> dict[str, Any]:
        tags = m.get("tags", [])
        kind = next((t.split(":", 1)[1] for t in tags if t.startswith("kind:")), "note")
        week = next((t.split(":", 1)[1] for t in tags if t.startswith("week:")), None)
        return {"id": m["id"], "text": m["content"], "type": kind, "week_of": week, "by": m.get("source"),
                "layer": m.get("layer"), "confidence": round(m.get("confidence", 0), 3),
                "relevance": None if relevance is None else round(relevance, 3), "domain": m.get("domain"),
                "entities": m.get("entities", [])}

    def recall(self, account_id: str, query: str = "", k: int = 5) -> list[dict[str, Any]]:
        q = f"{account_id} {query or 'churn risk decisions actions context'}"
        # Scoped to the account (chatId), so threshold 0: every in-scope memory is a candidate,
        # ranked by the spec's hybrid formula.
        r = self._call(op="query", query=q, chatId=account_id, topN=k, threshold=0)
        return [self._to_dict(x["memory"], x["relevance"]) for x in r["memories"] if x["memory"].get("chatId") == account_id]

    def remember(self, account_id: str, record: dict[str, Any]) -> None:
        kind = record.get("type", "note")
        layer = "long_term" if kind in ("context", "action") else "working"
        tags = [f"kind:{kind}", f"account:{account_id}"] + ([f"week:{record['week_of']}"] if record.get("week_of") else [])
        if record.get("risk"):
            tags.append(f"risk:{record['risk']}")
        self._call(op="add", content=f"{account_id}: {record.get('text', '')}", type="factual", layer=layer, tags=tags,
                   confidence=0.8 if kind in ("context", "action") else 0.7,
                   source=f"rind:{record.get('by', 'Rind')}", chatId=account_id,
                   provenance={"origin": "manual" if kind in ("context", "action") else "plan_completion",
                               "learnedAt": 0, "label": f"{record.get('by', 'Rind')} week of {record.get('week_of', '?')}"})

    def helpful(self, memory_id: str) -> None:
        self._call(op="feedback", id=memory_id, kind="helpful")

    def stats(self) -> dict[str, Any]:
        return {"backend": self.backend, **self._call(op="stats")}

    def close(self) -> None:
        try:
            self._p.stdin.close()
            self._p.wait(timeout=5)
        except Exception:
            self._p.kill()


_HMEM: HMemStore | None = None


def get_memory() -> MemoryStore:
    """H-MEM is the default backend. RIND_MEMORY_BACKEND=local uses the plain JSON baseline."""
    global _HMEM
    if os.getenv("RIND_MEMORY_BACKEND", "hmem").lower() == "local":
        return LocalJsonMemory()
    path = os.getenv("RIND_HMEM_DIR")
    if _HMEM is None or (path and Path(path) != _HMEM.dir):
        _HMEM = HMemStore(Path(path) if path else None)
    return _HMEM  # type: ignore[return-value]


def seed_demo_memory(store: MemoryStore) -> int:
    """Load the 'previous weeks' memory used in the demo (data/sample/memory_seed.json)."""
    seed = json.loads((ROOT / "data" / "sample" / "memory_seed.json").read_text())
    n = 0
    for account_id, records in seed.items():
        for r in records:
            store.remember(account_id, r)
            n += 1
    return n
