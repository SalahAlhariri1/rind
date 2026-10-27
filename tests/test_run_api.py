"""run.py (the programmatic entry point) on the three shipped examples, in sample mode.
Different inputs must give different, input-specific outputs."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(example: str) -> dict:
    env = {**os.environ, "SAMPLE_MODE": "true", "RIND_HMEM_DIR": str(ROOT / "memory" / "test_run_api")}
    p = subprocess.run([sys.executable, "run.py", "--sample", "--quiet", "--input", f"input_examples/{example}.json"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-800:]
    return json.loads(p.stdout)


def test_examples_differ_and_match_their_inputs():
    r1, r2, r3 = _run("example_1"), _run("example_2"), _run("example_3")
    ids = lambda r: {a["account_id"] for a in r["output"]["accounts"]}  # noqa: E731
    assert ids(r1) == {"ACC-1047", "ACC-1058", "ACC-1051"}
    assert ids(r2) == {"ACC-1006", "ACC-1055", "ACC-1031", "ACC-1026"}
    assert ids(r3) == {"ACC-1020", "ACC-1027"}
    assert all(a["rind_says"]["risk"] in ("high", "medium") and a["crm_says"]["color"] == "green" for a in r1["output"]["accounts"])
    assert all(a["rind_says"]["risk"] == "none" and a["ruled_out"] for a in r2["output"]["accounts"])
    assert any(a["memory_recalled"] for a in r3["output"]["accounts"])
    for r in (r1, r2, r3):
        assert r["status"] == "success" and Path(ROOT / r["log_file"]).exists()


def test_bad_input_returns_structured_error():
    env = {**os.environ, "SAMPLE_MODE": "true", "RIND_HMEM_DIR": str(ROOT / "memory" / "test_run_api")}
    req = ROOT / "logs" / "_bad_request.json"
    req.write_text(json.dumps({"input": {"accounts": ["NOT-AN-ACCOUNT"]}}))
    p = subprocess.run([sys.executable, "run.py", "--sample", "--quiet", "--input", str(req)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    req.unlink()
    out = json.loads(p.stdout)
    assert p.returncode == 1 and out["status"] == "error" and out["error"]["type"] == "BadInput"
