"""Rind's control loop.

  Planner ──picks accounts + hypotheses──▶ Investigator ──queries Zetaris──▶ claims
     ▲                                          ▲                              │
     │ memory (H-MEM)                           └── needs_check: run X ◀── Skeptic
     │                                                                         │ supported claims + risk
     └──────────── remember decision ◀── Reporter ◀── Escalation policy ◀──────┘

Bounded: at most RIND_MAX_DEEP_DIVES accounts, RIND_MAX_SKEPTIC_ROUNDS review rounds,
timeouts and limited retries on every Zetaris and Nightshift call."""
from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import agents, verifier
from .config import ROOT, SETTINGS
from .data import DataLayer, snapshot
from .llm import LLM
from .memory import MemoryStore, get_memory
from .trace import Trace

_cache_lock = __import__('threading').Lock()
DECISION_VERSION = "2026-10-26.5"  # bump when prompts/rules change so cached decisions are redone
RISK_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}


def _fingerprint(acct: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> str:
    basis = {"v": DECISION_VERSION, "mode": "sample" if SETTINGS.sample_mode else "live",
             "models": [SETTINGS.model_fast, SETTINGS.model_deep],
             "crm": {k: acct.get(k) for k in ("health_score", "renewal_date", "arr_usd")},
             "facts": {k: v["facts"] for k, v in sorted(evidence.items())}}
    return hashlib.sha1(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _cache_path():
    return Path(os.getenv("RIND_HMEM_DIR", ROOT / "memory" / "hmem")).parent / "decision_cache.json"


def investigate_account(dl: DataLayer, llm: LLM, mem: MemoryStore, trace: Trace, acct: dict[str, Any],
                        dive: dict[str, Any], bulk: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    aid = acct["account_id"]
    memory = mem.recall(aid, query="churn risk decisions and actions")
    if memory:
        trace.log("Memory", "recall", account_id=aid, backend=getattr(mem, "backend", "?"), items=len(memory),
                  texts=[m.get("text") for m in memory], ids=[m.get("id") for m in memory],
                  relevance=[m.get("relevance") for m in memory])
    # Evidence for every check, cut from this run's Zetaris pulls (no per-account round trips).
    evidence = dl.evidence_from_bulk(acct, bulk)
    trace.log("Investigator", "evidence", account_id=aid, checks=sorted(evidence),
              rows={k: len(v["rows"]) for k, v in evidence.items()}, source="Zetaris rind.* views (this run)")

    # Sustainable re-runs: if nothing about this account changed since its last decision, reuse it.
    fp = _fingerprint(acct, evidence)
    cache_file = _cache_path()
    if os.getenv("RIND_REUSE_UNCHANGED", "true").lower() in ("1", "true", "yes"):
        try:
            cached = json.loads(cache_file.read_text()).get(aid) if cache_file.exists() else None
        except Exception:
            cached = None
        if cached and cached.get("fp") == fp:
            trace.log("Memory", "reuse_unchanged", account_id=aid, fingerprint=fp, risk=cached["result"]["risk"])
            return {**cached["result"], "reused": True}

    claims: dict[str, Any] = {"claims": []}
    verdict: dict[str, Any] = {"verdicts": [], "risk": "none"}
    rounds = []
    gated_out: list[dict[str, Any]] = []
    overrides: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    feedback: list[str] | None = None
    for round_no in range(1, SETTINGS.max_skeptic_rounds + 1):
        claims = agents.write_claims(llm, acct, evidence, memory, feedback)
        trace.log("Investigator", "claims", account_id=aid, round=round_no, claims=claims["claims"],
                  counter_evidence=claims["counter_evidence"], revision=bool(feedback))
        kept, gated_out = verifier.gate_claims(claims["claims"], evidence)
        if gated_out:
            trace.log("Verifier", "claims_rejected", account_id=aid, round=round_no, rejected=gated_out)
        claims = {**claims, "claims": kept}
        verdict = agents.review(llm, acct, claims, evidence, memory, round_no) if kept else {"verdicts": [], "risk": "none"}
        trace.log("Skeptic", "review", account_id=aid, round=round_no, verdicts=verdict["verdicts"],
                  risk=verdict["risk"], confidence=verdict.get("confidence"))
        rounds.append({"round": round_no, "claims": kept, "rejected": gated_out,
                       "verdicts": verdict["verdicts"], "risk": verdict["risk"]})
        # Send the draft back only when there is something concrete to fix.
        notes = [f"Verifier rejected: {g['claim']} -> {g['reason']}" for g in gated_out]
        notes += [f"Skeptic on {v['claim_id']}: {v.get('reason')}" for v in verdict["verdicts"] if v.get("verdict") == "needs_check"]
        missed = verifier.missed_signals(kept, evidence, memory)
        if missed:
            trace.log("Verifier", "missed_signals", account_id=aid, round=round_no, missed=missed)
            notes += [f"Verifier: the evidence shows {m['signal']} ({m['detail']}) but your draft has no claim about it. "
                      "Add a claim for it, citing that fact." for m in missed]
        if not notes or round_no == SETTINGS.max_skeptic_rounds:
            break
        feedback = notes
        trace.log("Skeptic", "send_back", account_id=aid, round=round_no, notes=notes)

    for m in verifier.missed_signals(kept, evidence, memory):  # still missing after the revision round
        c = verifier.synthesize_claim(m["signal"], evidence, len(kept) + 1)
        if c:
            kept.append(c)
            trace.log("Verifier", "claim_added", account_id=aid, claim=c)
    final, overrides = verifier.check_verdicts(kept, verdict.get("verdicts", []), evidence, memory)
    if overrides:
        trace.log("Verifier", "skeptic_overridden", account_id=aid, overrides=overrides)
    by_id = {c["id"]: c for c in kept}
    supported = [by_id[v["claim_id"]] for v in final if v["verdict"] == "supported" and v["claim_id"] in by_id]
    dropped = [{"claim": by_id[v["claim_id"]]["statement"], "reason": v.get("reason")}
               for v in final if v["verdict"] != "supported" and v["claim_id"] in by_id]
    dropped += [{"claim": g["claim"], "reason": g["reason"]} for g in gated_out]
    risk = verifier.risk_from(supported)
    if verdict.get("risk") and verdict.get("risk") != risk:
        trace.log("Verifier", "risk_set", account_id=aid, skeptic_risk=verdict.get("risk"), risk=risk,
                  reason=f"{len({c['signal'] for c in supported})} distinct supported signal(s)")
    esc = agents.escalate(acct, risk, dl.as_of)
    trace.log("Escalation", "decision", account_id=aid, risk=risk, **esc)

    brief = None
    if risk in ("high", "medium"):
        brief = agents.report(llm, acct, supported, risk, esc, evidence, memory)
        play, note = verifier.avoid_repeat_play(brief.get("recommended_play", ""), memory)
        if note:
            trace.log("Verifier", "play_changed", account_id=aid, was=brief.get("recommended_play"), now=play, reason=note)
            brief["recommended_play"], brief["memory_used"] = play, note
        trace.log("Reporter", "brief", account_id=aid, headline=brief.get("headline"), play=brief.get("recommended_play"),
                  completed_by_rules=brief.get("completed_by_rules"))

    # H-MEM trust loop: memories that changed a decision get the spec's "helpful" feedback.
    if hasattr(mem, "helpful"):
        used = []
        if brief and brief.get("memory_used"):
            used += [m for m in memory if m.get("type") == "action"]
        if dropped:
            used += [m for m in memory if m.get("type") == "context"]
        for m in {m["id"]: m for m in used if m.get("id")}.values():
            mem.helpful(m["id"])
            trace.log("Memory", "feedback_helpful", account_id=aid, memory_id=m["id"], text=m.get("text"))

    mem.remember(aid, {"week_of": dl.as_of.isoformat(), "type": "rind_decision", "by": "Rind",
                       "text": f"Risk {risk}; play {brief.get('recommended_play') if brief else 'none'}; escalated={esc['escalate']}.",
                       "risk": risk, "signals": sorted({c['signal'] for c in supported})})
    trace.log("Memory", "remember", account_id=aid, backend=getattr(mem, "backend", "?"), risk=risk)

    result = {
        "account_id": aid, "account_name": acct["account_name"], "arr_usd": acct["arr_usd"],
        "renewal_date": acct["renewal_date"], "renewal_in_days": esc["renewal_in_days"],
        "crm_health": acct["health_score"], "crm_color": acct["health_color"],
        "planner_why": dive.get("why"), "hypotheses": dive["hypotheses"],
        "risk": risk, "confidence": verdict.get("confidence"), "watermelon": acct["health_color"] == "green" and risk in ("high", "medium"),
        "supported_claims": supported, "dropped_claims": dropped, "rounds": rounds,
        "escalation": esc, "brief": brief, "verifier_overrides": overrides, "memory_recalled": [m.get("text") for m in memory],
        "evidence": {k: {"view": v["view"], "facts": v["facts"]} for k, v in evidence.items()},
    }
    if not llm.degraded:  # never cache a decision made on fallback rules
        with _cache_lock:
            data = json.loads(cache_file.read_text()) if cache_file.exists() else {}
            data[aid] = {"fp": fp, "result": result}
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data, default=str))
    return result


def run_weekly(progress: Callable[[str], None] | None = None, trace: Trace | None = None,
               accounts: list[str] | None = None, max_deep_dives: int | None = None) -> dict[str, Any]:
    """accounts: investigate exactly these account IDs (the caller replaces the Planner's pick).
    max_deep_dives: override RIND_MAX_DEEP_DIVES for this run."""
    trace = trace or Trace(mode="sample" if SETTINGS.sample_mode else "live")
    max_dives = max(1, int(max_deep_dives or SETTINGS.max_deep_dives))
    say = progress or (lambda s: None)
    dl = DataLayer(trace)
    llm = LLM(trace)
    mem = get_memory()
    trace.log("Orchestrator", "start", as_of=SETTINGS.as_of, data_engine=dl.engine.name,
              provider="sample-replay" if SETTINGS.sample_mode else SETTINGS.meterless_base_url,
              model_fast=SETTINGS.model_fast, model_deep=SETTINGS.model_deep, memory=mem.stats())

    say("Pulling all five systems through Zetaris…")
    roster = dl.roster("Planner")
    bulk = dl.bulk("Planner")
    try:
        dl.account_360("Planner")  # federated cross-system view, logged as proof of the join
    except Exception as e:
        trace.log("Planner", "account_360_unavailable", error=str(e)[:200])
    snap = snapshot(roster, bulk, dl.as_of)
    memory_index = {r["account_id"]: mem.recall(r["account_id"]) for r in roster}

    if accounts:
        known = {r["account_id"]: r for r in snap}
        unknown = [a for a in accounts if a not in known]
        picked = [a for a in dict.fromkeys(accounts) if a in known][:max_dives]
        plan = {"deep_dives": [{"account_id": a, "priority": i + 1, "why": "requested by the caller",
                                "hypotheses": [h for h in known[a].get("warning_signs", "none").split("|") if h != "none"]}
                               for i, a in enumerate(picked)],
                "summary": f"Caller asked for {len(accounts)} specific account(s); the Planner's pick was skipped."}
        trace.log("Planner", "plan_override", requested=accounts, unknown=unknown, deep_dives=plan["deep_dives"])
        if not picked:
            raise ValueError(f"None of the requested accounts exist: {unknown}")
    else:
        say("Planner is choosing which accounts to investigate…")
        plan = agents.plan(llm, snap, memory_index, max_dives)
        trace.log("Planner", "plan", deep_dives=plan["deep_dives"], summary=plan.get("summary"))

    by_id = {a["account_id"]: a for a in roster}
    results: list[dict[str, Any]] = []
    say(f"Investigating {len(plan['deep_dives'])} accounts (Investigator ↔ Skeptic)…")
    with ThreadPoolExecutor(max_workers=max(1, SETTINGS.concurrency)) as pool:
        futs = {pool.submit(investigate_account, dl, llm, mem, trace, by_id[d["account_id"]], d, bulk): d for d in plan["deep_dives"]}
        for f in as_completed(futs):
            d = futs[f]
            try:
                r = f.result()
                results.append(r)
                say(f"{r['account_name']}: {r['risk'].upper()}")
            except Exception as e:
                trace.log("Orchestrator", "account_failed", account_id=d["account_id"], error=str(e)[:300])

    results.sort(key=lambda r: (RISK_ORDER.get(r["risk"], 9), -int(r["arr_usd"])))
    investigated = {r["account_id"] for r in results}
    not_flagged = [{"account_id": a["account_id"], "account_name": a["account_name"], "crm_health": a["health_score"],
                    "crm_color": a["health_color"]} for a in roster if a["account_id"] not in investigated]
    at_risk = [r for r in results if r["risk"] in ("high", "medium")]
    out = {
        "run_id": trace.run_id, "mode": trace.mode, "as_of": SETTINGS.as_of,
        "summary": {
            "accounts": len(roster), "investigated": len(results), "at_risk": len(at_risk),
            "watermelons": sum(1 for r in at_risk if r["watermelon"]),
            "arr_at_risk_usd": sum(int(r["arr_usd"]) for r in at_risk),
            "escalations": sum(1 for r in at_risk if r["escalation"]["escalate"]),
            "reused_unchanged": sum(1 for r in results if r.get("reused")),
            "false_alarms_stopped": sum(1 for r in results if r["risk"] in ("none", "low") and r["dropped_claims"]),
            "zetaris_queries": dl.queries, "llm_calls": llm.calls, "degraded": llm.degraded,
            "planner_summary": plan.get("summary"),
        },
        "model_usage": trace.model_usage(),
        "accounts": results, "not_flagged": not_flagged, "trace_file": str(trace.path.relative_to(ROOT)),
    }
    trace.log("Orchestrator", "done", **out["summary"])
    (ROOT / "logs" / f"result_{trace.run_id}.json").write_text(json.dumps(out, indent=1, default=str))
    return out
