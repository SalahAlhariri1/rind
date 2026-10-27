"""End-to-end in SAMPLE_MODE: no Zetaris, no Nightshift, no credentials."""
import json
import os
from pathlib import Path

os.environ["SAMPLE_MODE"] = "true"

ROOT = Path(__file__).resolve().parents[1]


def test_weekly_run_finds_watermelons_and_stops_decoys(tmp_path, monkeypatch):
    monkeypatch.setenv("RIND_MEMORY_PATH", str(tmp_path / "mem.json"))
    monkeypatch.setenv("RIND_HMEM_DIR", str(tmp_path / "hmem"))
    monkeypatch.setenv("RIND_REUSE_UNCHANGED", "false")
    from agent.memory import get_memory, seed_demo_memory
    seed_demo_memory(get_memory())
    from agent.orchestrator import run_weekly
    out = run_weekly()
    gt = {a["account_id"]: a for a in json.loads((ROOT / "eval" / "ground_truth.json").read_text())["accounts"]}
    flagged = {r["account_id"] for r in out["accounts"] if r["risk"] in ("high", "medium")}
    watermelons = {k for k, v in gt.items() if v["label"] == "watermelon"}
    decoys = {k for k, v in gt.items() if v["label"].startswith("decoy")}
    assert watermelons <= flagged
    assert not (flagged & decoys)
    # every supported claim carries evidence from a Zetaris view
    for r in out["accounts"]:
        for c in r["supported_claims"]:
            assert c["evidence"] and c["evidence"][0]["view"].startswith("rind.")
    # the Skeptic changed outcomes: decoys were investigated and their claims dropped
    assert sum(1 for r in out["accounts"] if r["dropped_claims"] and r["risk"] == "none") >= 8
    # second run with unchanged data reuses decisions instead of re-investigating
    monkeypatch.setenv("RIND_REUSE_UNCHANGED", "true")
    again = run_weekly()
    assert again["summary"]["reused_unchanged"] == again["summary"]["investigated"]
