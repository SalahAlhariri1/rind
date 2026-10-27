"""H-MEM feature tests (the Meterless Engine requirement).

1. The engine itself passes the official Meterless H-MEM conformance suite (13/13).
2. Cross-session: a decision written in session 1 is recalled by a NEW bridge process in session 2.
3. The recalled memory changes what Rind recommends.
4. The trust loop: marking a memory helpful applies the spec's exact delta and is in the audit ledger.
"""
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

os.environ["SAMPLE_MODE"] = "true"
ROOT = Path(__file__).resolve().parents[1]
NPX = shutil.which("npx") or shutil.which("npx.cmd")
needs_node = pytest.mark.skipif(not NPX or not (ROOT / "hmem" / "node_modules").exists(),
                                reason="needs Node.js and `npm install` in hmem/")

from agent import agents  # noqa: E402
from agent.llm import LLM  # noqa: E402
from agent.memory import HMemStore  # noqa: E402
from agent.trace import Trace  # noqa: E402


@needs_node
def test_hmem_engine_passes_official_conformance():
    impl = (ROOT / "hmem" / "src" / "index.ts").resolve().as_posix()
    r = subprocess.run([NPX, "tsx", "conformance/runner.ts"], cwd=ROOT / "hmem", capture_output=True, text=True,
                       env={**os.environ, "HMEM_IMPL": impl}, timeout=180)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-1000:]
    assert "13/13 checks passed" in r.stdout


@needs_node
def test_memory_survives_a_new_session_and_feedback_is_audited(tmp_path):
    s1 = HMemStore(tmp_path)
    s1.remember("ACC-9999", {"type": "action", "by": "Dana Reyes (CSM)", "week_of": "2026-10-05",
                             "text": "Offered admin training on Oct 8; usage still falling."})
    s1.close()
    s2 = HMemStore(tmp_path)  # brand-new process: only disk persistence can carry the memory
    got = s2.recall("ACC-9999")
    assert got and "training" in got[0]["text"] and got[0]["type"] == "action"
    before = got[0]["confidence"]
    s2.helpful(got[0]["id"])
    after = s2.recall("ACC-9999")[0]
    assert abs(after["confidence"] - min(1.0, before + 0.05)) < 1e-9
    history = s2._call(op="history", id=got[0]["id"])
    assert [h["action"] for h in history][:1] == ["create"] and "feedback" in [h["action"] for h in history]
    assert s2.recall("ACC-0001") == []  # scoped: another account sees nothing
    s2.close()


def test_memory_changes_the_recommended_play():
    llm = LLM(Trace(mode="test"))
    acct = {"account_id": "ACC-9999", "account_name": "Test Co", "arr_usd": 50000, "renewal_date": "2026-12-01",
            "health_score": 80, "health_color": "green"}
    claim = [{"id": "c1", "signal": "usage_decline", "severity": "high", "statement": "Usage fell 50%.",
              "evidence": [{"view": "rind.product_usage", "fact": "change_pct", "value": -50}]}]
    esc = agents.escalate(acct, "medium", date(2026, 10, 19))
    without = agents.report(llm, acct, claim, "medium", esc, {}, [])
    with_mem = agents.report(llm, acct, claim, "medium", esc, {},
                             [{"type": "action", "text": "Offered admin training on Oct 8."}])
    assert without["recommended_play"] == "adoption_workshop"
    assert with_mem["recommended_play"] != "adoption_workshop"
    assert with_mem["memory_used"]
