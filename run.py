"""Rind's programmatic entry point: JSON in, JSON out, no UI needed.

  python run.py                                                    # weekly triage, the Planner picks accounts
  python run.py --input input_examples/example_1.json              # run one example
  python run.py --input input_examples/example_1.json --output output_examples/example_1.json
  python run.py --serve --port 8000                                # HTTP API: POST /run (same JSON), GET /health

Input (every field is optional):
  {
    "run_id": "example-1",
    "input":   {"accounts": ["ACC-1047", "ACC-1058"], "max_deep_dives": 5},
    "options": {"sample_mode": true, "reuse_unchanged": false}
  }
  accounts        investigate exactly these accounts (otherwise the Planner chooses)
  max_deep_dives  cap on deep investigations this run (otherwise RIND_MAX_DEEP_DIVES)
  sample_mode     true = local copy of the data + recorded/offline answers, no credentials needed
  reuse_unchanged false = re-investigate even if an account's data has not changed since last week

Readable progress lines go to stdout while the run happens (use --quiet to print only the JSON).
Every step is also written to logs/run_<run_id>.jsonl, including which model served each request.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent

AGENTS = [
    {"name": "Planner", "kind": "AI (Nemotron via Nightshift)", "role": "Chooses which accounts to investigate and the hypotheses to test"},
    {"name": "Investigator", "kind": "AI (Nemotron via Nightshift)", "role": "Writes churn-risk claims, each citing a Zetaris fact"},
    {"name": "Verifier", "kind": "Deterministic rules", "role": "Rejects claims the data does not support, sends drafts back when a signal is missed, checks the Skeptic's rulings"},
    {"name": "Skeptic", "kind": "AI (Nemotron via Nightshift)", "role": "Argues against every claim and looks for innocent explanations"},
    {"name": "Escalation", "kind": "Deterministic policy", "role": "Escalates high-risk, high-revenue or near-renewal accounts"},
    {"name": "Reporter", "kind": "AI (Nemotron via Nightshift)", "role": "Writes the brief, the recommended play and a draft email"},
    {"name": "Memory", "kind": "Meterless H-MEM engine", "role": "Recalls earlier weeks per account, stores this week's verdict, rewards helpful memories"},
]


def _err(run_id: str, kind: str, message: str, t0: float, recoverable: bool = True) -> dict[str, Any]:
    return {"run_id": run_id, "status": "error", "error": {"type": kind, "message": message, "recoverable": recoverable},
            "execution_time_seconds": round(time.time() - t0, 1)}


def _account_view(r: dict[str, Any]) -> dict[str, Any]:
    brief = r.get("brief") or {}
    src = {"rind.product_usage": "Product usage", "rind.support_tickets": "Support desk",
           "rind.billing_invoices": "Billing", "rind.contacts": "Contacts", "rind.crm_accounts": "CRM"}
    return {
        "account_id": r["account_id"], "account_name": r["account_name"], "arr_usd": r["arr_usd"],
        "renewal_date": r["renewal_date"], "renewal_in_days": r["renewal_in_days"],
        "crm_says": {"health_score": r["crm_health"], "color": r["crm_color"]},
        "rind_says": {"risk": r["risk"], "watermelon": r["watermelon"]},
        "reasons": [{"signal": c["signal"], "statement": c["statement"],
                     "source": src.get((c.get("evidence") or [{}])[0].get("view", ""), "Zetaris"),
                     "evidence": c.get("evidence"), "added_by": c.get("added_by", "Investigator")}
                    for c in r.get("supported_claims", [])],
        "ruled_out": r.get("dropped_claims", []),
        "verifier_overrides": r.get("verifier_overrides", []),
        "memory_recalled": r.get("memory_recalled", []),
        "recommended_play": brief.get("recommended_play"),
        "play_changed_by_memory": brief.get("memory_used"),
        "escalation": r.get("escalation"),
        "draft_email": brief.get("draft_email"),
        "review_rounds": len(r.get("rounds") or []),
        "reused_unchanged_verdict": bool(r.get("reused")),
    }


def execute(req: dict[str, Any], quiet: bool = False) -> dict[str, Any]:
    """Run Rind once for one request. Agent modules must already be importable (mode is fixed per process)."""
    t0 = time.time()
    run_id = str(req.get("run_id") or "")
    inp, opts = req.get("input") or {}, req.get("options") or {}
    from agent.config import SETTINGS
    if "sample_mode" in opts and bool(opts["sample_mode"]) != SETTINGS.sample_mode:
        return _err(run_id, "ModeMismatch", f"This process runs with SAMPLE_MODE={str(SETTINGS.sample_mode).lower()}. "
                    "Restart with the other SAMPLE_MODE to change it.", t0)
    accounts = inp.get("accounts")
    if accounts is not None and (not isinstance(accounts, list) or not all(isinstance(a, str) for a in accounts)):
        return _err(run_id, "BadInput", "input.accounts must be a list of account IDs like \"ACC-1047\".", t0, False)
    if "reuse_unchanged" in opts:
        os.environ["RIND_REUSE_UNCHANGED"] = "true" if opts["reuse_unchanged"] else "false"

    from agent.memory import get_memory, seed_demo_memory
    from agent.orchestrator import run_weekly
    from agent.trace import Trace

    trace = Trace(mode="sample" if SETTINGS.sample_mode else "live")
    if not quiet:
        def echo(e: dict[str, Any]) -> None:
            if e["event"] == "llm_call":
                where = "Nightshift" if trace.mode == "live" else "sample-mode model step"
                line = f"{e['agent']} -> {where} ({e.get('lane')}) served by {e.get('model_used')} in {e.get('latency_ms')} ms"
            elif e["event"] == "sql":
                where = "Zetaris" if e.get("engine") == "zetaris" else "local sample copy of"
                line = f"{e['agent']} queried {where} {e.get('view')}: {e.get('rows')} rows in {e.get('ms')} ms"
            else:
                bits = {k: v for k, v in e.items() if k in ("account_id", "risk", "round", "items", "play", "escalate")}
                line = f"{e['agent']} {e['event']} " + " ".join(f"{k}={v}" for k, v in bits.items())
            print(f"[INFO] run={e['run_id']} t={e['t_s']}s {line}", flush=True)
        trace.on_event(echo)

    try:
        mem = get_memory()
        if int(mem.stats().get("records", 0)) == 0:
            n = seed_demo_memory(mem)
            trace.log("Memory", "seeded_demo_memory", records=n, source="data/sample/memory_seed.json")
        out = run_weekly(progress=None if quiet else (lambda s: print(f"[INFO] {s}", flush=True)), trace=trace,
                         accounts=accounts, max_deep_dives=inp.get("max_deep_dives"))
    except ValueError as e:
        return _err(run_id or trace.run_id, "BadInput", str(e), t0, False)
    except Exception as e:  # Zetaris/Nightshift/H-MEM unreachable etc.
        return _err(run_id or trace.run_id, type(e).__name__, str(e)[:500] + " (try SAMPLE_MODE=true)", t0)

    s = out["summary"]
    fallback = sum(n for m, n in out["model_usage"].items() if "offline" in m)
    return {
        "run_id": run_id or out["run_id"],
        "status": "degraded" if s.get("degraded") else "success",
        "mode": out["mode"],
        "as_of": out["as_of"],
        "output": {
            "summary": s,
            "accounts": [_account_view(r) for r in out["accounts"]],
            "not_investigated": len(out["not_flagged"]),
        },
        "agents": AGENTS,
        "models": out["model_usage"],
        "notes": ([f"{fallback} step(s) used the offline heuristic instead of a model"] if fallback else []),
        "trace_id": out["run_id"],
        "log_file": out["trace_file"],
        "execution_time_seconds": round(time.time() - t0, 1),
    }


def serve(port: int) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict[str, Any]) -> None:
            data = json.dumps(body, indent=1, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            from agent.config import SETTINGS
            if self.path.rstrip("/") == "/health":
                self._send(200, {"status": "ok", "sample_mode": SETTINGS.sample_mode})
            else:
                self._send(404, {"status": "error", "error": {"type": "NotFound", "message": "Use POST /run or GET /health"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/run":
                return self._send(404, {"status": "error", "error": {"type": "NotFound", "message": "Use POST /run"}})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._send(400, {"status": "error", "error": {"type": "BadJSON", "message": "Body must be JSON"}})
            res = execute(req)
            self._send(200 if res["status"] != "error" else 400, res)

    print(f"[INFO] Rind API on http://0.0.0.0:{port}  (POST /run, GET /health)", flush=True)
    HTTPServer(("0.0.0.0", port), H).serve_forever()


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Rind without the UI.")
    ap.add_argument("--input", help="request JSON file (see input_examples/)")
    ap.add_argument("--output", help="also write the result JSON here")
    ap.add_argument("--sample", action="store_true", help="force SAMPLE_MODE=true")
    ap.add_argument("--quiet", action="store_true", help="print only the result JSON")
    ap.add_argument("--serve", action="store_true", help="start the HTTP API instead of running once")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    a = ap.parse_args()

    req: dict[str, Any] = {}
    if a.input:
        req = json.loads(Path(a.input).read_text(encoding="utf-8"))
    # The data layer and model client are chosen at import time, so set the mode before importing the agent.
    if a.sample or (req.get("options") or {}).get("sample_mode") is True:
        os.environ["SAMPLE_MODE"] = "true"
    elif (req.get("options") or {}).get("sample_mode") is False:
        os.environ["SAMPLE_MODE"] = "false"
    sys.path.insert(0, str(ROOT))

    if a.serve:
        serve(a.port)
        return 0
    res = execute(req, quiet=a.quiet)
    text = json.dumps(res, indent=1, default=str)
    if a.output:
        outp = Path(a.output)
        outp.parent.mkdir(parents=True, exist_ok=True)
        trace_src = ROOT / str(res.get("log_file") or "")
        if res.get("log_file") and trace_src.is_file():  # keep the trace next to the output so it ships with it
            trace_dst = outp.with_suffix(".trace.jsonl")
            trace_dst.write_text(trace_src.read_text(encoding="utf-8"), encoding="utf-8")
            res["trace_copy"] = trace_dst.as_posix()
            text = json.dumps(res, indent=1, default=str)
        outp.write_text(text + "\n", encoding="utf-8")
    if not a.quiet:
        s = (res.get("output") or {}).get("summary") or {}
        print(f"[INFO] status={res['status']} accounts={len((res.get('output') or {}).get('accounts', []))} "
              f"at_risk={s.get('at_risk')} models={res.get('models')} log={res.get('log_file')}"
              + (f" saved={a.output}" if a.output else ""), flush=True)
    print(text)
    return 0 if res["status"] != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
