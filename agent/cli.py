"""Command line (agents and scripts can drive Rind too).

  python -m agent.cli check          # can we reach Zetaris and Nightshift?
  python -m agent.cli seed-memory    # load the demo's "previous weeks" memory
  python -m agent.cli run            # weekly triage, prints the ranked list
  python -m agent.cli memory ACC-1020
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import SETTINGS


def _print_run(out: dict) -> None:
    s = out["summary"]
    print(f"\nRind weekly check  as_of={out['as_of']}  mode={out['mode']}  run={out['run_id']}")
    print(f"{s['accounts']} accounts | {s['investigated']} investigated | {s['at_risk']} at risk "
          f"({s['watermelons']} hidden behind a green score) | ${s['arr_at_risk_usd']:,} ARR at risk | "
          f"{s['false_alarms_stopped']} false alarms stopped by the Skeptic | {s.get('reused_unchanged', 0)} unchanged accounts reused")
    print(f"Zetaris queries: {s['zetaris_queries']} | model calls: {s['llm_calls']} | models: {out['model_usage']}")
    fb = sum(n for m, n in out["model_usage"].items() if "offline" in m)
    if out["mode"] == "live" and fb:
        print(f"WARNING: {fb} of {s['llm_calls']} steps fell back to offline rules because Nightshift calls failed. "
              "See llm_retry events in the trace. Do not record the demo from this run.")
    print()
    for r in out["accounts"]:
        flag = "WATERMELON " if r["watermelon"] else ""
        print(f"[{r['risk'].upper():6s}] {flag}{r['account_name']} ({r['account_id']})  CRM {r['crm_color']} {r['crm_health']}  "
              f"ARR ${int(r['arr_usd']):,}  renews in {r['renewal_in_days']}d" + ("  -> ESCALATE" if r["escalation"]["escalate"] else ""))
        for c in r["supported_claims"]:
            print(f"      + {c['statement']}")
        for d in r["dropped_claims"]:
            print(f"      - dropped: {d['claim']}  ({d['reason']})")
        if r.get("brief") and r["brief"].get("memory_used"):
            print(f"      memory: {r['brief']['memory_used']}")
    print(f"\nTrace: {out['trace_file']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rind")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("seed-memory")
    r = sub.add_parser("run")
    r.add_argument("--json", action="store_true")
    m = sub.add_parser("memory")
    m.add_argument("account_id")
    a = p.parse_args(argv)

    if a.cmd == "check":
        from .data import DataLayer
        from .llm import LLM
        from .trace import Trace
        t = Trace(mode="check")
        ok = True
        try:
            dl = DataLayer(t)
            print(("PASS" if dl.ping() else "FAIL") + f" data layer ({dl.engine.name}) answers SELECT 1")
            n = len(dl.roster("check"))
            print(("PASS" if n == 60 else "FAIL") + f" rind.crm_accounts returns {n} accounts")
        except Exception as e:
            ok = False
            print(f"FAIL data layer: {e}")
        try:
            llm = LLM(t)
            if SETTINGS.sample_mode:
                print("SKIP Nightshift (SAMPLE_MODE=true)")
            else:
                print("PASS Nightshift models:", ", ".join(llm.models()[:8]))
                out = llm.complete_json("check", "fast", "Reply with JSON only.", 'Return {"ok": true}', lambda: {"ok": False})
                used = [e for e in t.events if e["event"] == "llm_call"][-1]["model_used"]
                print(("PASS" if out.get("ok") else "FAIL") + f" Nightshift chat completion served by: {used}")
        except Exception as e:
            ok = False
            print(f"FAIL Nightshift: {e}")
        return 0 if ok else 1

    if a.cmd == "seed-memory":
        from .memory import get_memory, seed_demo_memory
        mem = get_memory()
        print(f"seeded {seed_demo_memory(mem)} memory records into {mem.stats()}")
        return 0

    if a.cmd == "memory":
        from .memory import get_memory
        print(json.dumps(get_memory().recall(a.account_id, k=20), indent=1))
        return 0

    if a.cmd == "run":
        from .orchestrator import run_weekly
        out = run_weekly(progress=lambda s: print("…", s, file=sys.stderr))
        if a.json:
            print(json.dumps(out, indent=1, default=str))
        else:
            _print_run(out)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
