"""The five Rind agents. Each LLM agent has a system prompt, a JSON contract, and a
deterministic offline stand-in used only in SAMPLE_MODE (or if Nightshift fails mid-run)."""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from .data import CHECKS
from .llm import LLM

SIGNALS = ["usage_decline", "champion_left", "invoice_overdue", "escalating_tickets", "other"]


def _fmt_money(x: Any) -> str:
    try:
        return f"${int(x):,}"
    except (TypeError, ValueError):
        return str(x)


def _compact(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), default=str)


# ============================================================================ Planner

PLANNER_SYS = """You are the Planner in Rind, a churn-investigation team for a B2B SaaS customer success manager (CSM).
Input: one row per account combining the CRM health score with fresh signals from usage, support, billing and contacts,
plus notes remembered from earlier weeks. The CRM health score is recalculated monthly and is often stale.
Goal: choose which accounts deserve a deep investigation this week, especially accounts whose CRM score is green
but whose fresh signals disagree ("watermelon" accounts: green outside, red inside).
The warning_signs column lists the raw warning signs code detected (some will turn out to be harmless:
seasonal dips, habitual late payers, replaced champions, solved tickets). Your job: rank the flagged accounts
by risk x revenue x renewal urgency, give each a one-sentence reason, and state the hypotheses to test
using only these signal names: usage_decline, champion_left, invoice_overdue, escalating_tickets.
Pick each account at most once. Never pick an account whose warning_signs is "none".
Respond with JSON only:
{"deep_dives":[{"account_id":"ACC-1001","priority":1,"why":"one sentence","hypotheses":["usage_decline"]}],
 "summary":"one sentence about the book of business"}"""


def plan(llm: LLM, snap: list[dict[str, Any]], memory_index: dict[str, list[dict[str, Any]]], max_dives: int) -> dict[str, Any]:
    header = list(snap[0].keys())
    flagged_rows = [r for r in snap if r.get("warning_signs", "none") != "none"]
    lines = [",".join(header)] + [",".join(str(r[h]) for h in header) for r in flagged_rows]
    mem_lines = [f"{aid}: " + " | ".join(m["text"] for m in ms) for aid, ms in memory_index.items() if ms]
    user = (f"Pick at most {max_dives} accounts. {len(snap) - len(flagged_rows)} other accounts have no warning signs and are omitted.\n\nFLAGGED ACCOUNTS (csv):\n" + "\n".join(lines)
            + "\n\nREMEMBERED NOTES:\n" + ("\n".join(mem_lines) or "none"))

    def offline() -> dict[str, Any]:
        picks = []
        for r in snap:
            hyp = [h for h in r.get("warning_signs", "none").split("|") if h in SIGNALS]
            if hyp:
                urgency = max(0, 180 - r["renewal_in_days"]) / 180
                score = len(hyp) * 2 + urgency * 2 + r["arr_usd"] / 150000 + (1 if r["crm_color"] == "green" else 0)
                picks.append((score, r, hyp))
        picks.sort(key=lambda x: -x[0])
        return {"deep_dives": [{"account_id": r["account_id"], "priority": i + 1,
                                "why": f"{'Green CRM score but ' if r['crm_color'] == 'green' else ''}fresh signals: {', '.join(h)}",
                                "hypotheses": h} for i, (_, r, h) in enumerate(picks[:max_dives])],
                "summary": f"{len(picks)} of {len(snap)} accounts show at least one fresh warning signal."}

    out = llm.complete_json("Planner", "deep", PLANNER_SYS, user, offline, max_tokens=2000)
    out = out if isinstance(out, dict) else {"deep_dives": out if isinstance(out, list) else []}
    flagged = {r["account_id"]: r for r in snap if r.get("warning_signs", "none") != "none"}
    dives, seen = [], set()
    for d in out.get("deep_dives", []):
        aid = d.get("account_id")
        if aid in flagged and aid not in seen:  # valid, flagged, de-duplicated
            seen.add(aid)
            hyps = [h for h in d.get("hypotheses", []) if h in SIGNALS] or flagged[aid]["warning_signs"].split("|")
            dives.append({**d, "hypotheses": hyps})
    # Coverage guarantee: every flagged account is investigated, in the Planner's order first.
    missed = [r for aid, r in flagged.items() if aid not in seen]
    missed.sort(key=lambda r: (-len(r["warning_signs"].split("|")), r["renewal_in_days"], -r["arr_usd"]))
    for r in missed:
        dives.append({"account_id": r["account_id"], "priority": len(dives) + 1, "why": "added by coverage rule: has warning signs",
                      "hypotheses": r["warning_signs"].split("|"), "added_by": "coverage"})
    out["coverage_added"] = len(missed)
    out["deep_dives"] = dives[:max_dives]
    return out


# ============================================================================ Investigator

INVESTIGATOR_PICK_SYS = """You are the Investigator in Rind. You gather evidence from company systems through Zetaris.
Given an account, the Planner's hypotheses and any requests from the Skeptic, choose which checks to run.
Available checks:
""" + "\n".join(f"- {k}: {v}" for k, v in CHECKS.items()) + """
Respond with JSON only: {"checks":["usage_trend"],"reason":"one sentence"}"""

INVESTIGATOR_CLAIMS_SYS = """You are the Investigator in Rind. Turn the evidence into churn-risk claims for a CSM.
Rules:
- Only write a claim for a warning sign the evidence SHOWS. Never write a claim saying something is absent or fine.
- At most one claim per signal. Signal names: usage_decline, champion_left, invoice_overdue, escalating_tickets.
- Every claim cites at least one evidence item copied exactly from the evidence (view + fact name + value).
- Never invent numbers or IDs. Usage numbers are weekly ACTIVE USERS (not minutes).
- Thresholds: usage_decline needs change_pct <= -20; invoice_overdue needs latest_days_overdue > 14;
  escalating_tickets needs open_high_or_urgent >= 1; champion_left needs champion_left true.
Also report counter-evidence you see (e.g. last year's identical dip, a long habit of paying late, a named replacement champion).
Respond with JSON only:
{"claims":[{"id":"c1","signal":"usage_decline","severity":"high|medium|low",
 "statement":"plain-English sentence with the numbers",
 "evidence":[{"view":"rind.product_usage","fact":"change_pct","value":-48.2}]}],
 "counter_evidence":["sentence"]}"""

CHECK_FOR = {"usage_decline": "usage_trend", "champion_left": "contacts",
             "invoice_overdue": "invoices", "escalating_tickets": "tickets"}


def pick_checks(llm: LLM, acct: dict[str, Any], hypotheses: list[str], requests: list[str], done: set[str]) -> list[str]:
    user = _compact({"account": {k: acct[k] for k in ("account_id", "account_name", "industry", "health_score")},
                     "hypotheses": hypotheses, "skeptic_requests": requests, "already_ran": sorted(done)})

    def offline() -> dict[str, Any]:
        want = [CHECK_FOR[h] for h in hypotheses if h in CHECK_FOR] + list(requests)
        return {"checks": list(dict.fromkeys(want)), "reason": "map hypotheses and skeptic requests to checks"}

    out = llm.complete_json("Investigator", "fast", INVESTIGATOR_PICK_SYS, user, offline, acct["account_id"], 400)
    checks = [c for c in out.get("checks", []) if c in CHECKS]
    for r in requests:  # Skeptic requests are mandatory, the model cannot drop them
        if r in CHECKS and r not in checks:
            checks.append(r)
    return [c for c in checks if c not in done]


def write_claims(llm: LLM, acct: dict[str, Any], evidence: dict[str, dict[str, Any]], memory: list[dict[str, Any]],
                 feedback: list[str] | None = None) -> dict[str, Any]:
    ev = {k: {"view": v["view"], "facts": v["facts"]} for k, v in evidence.items()}
    payload = {"account": {k: acct[k] for k in ("account_id", "account_name", "industry", "arr_usd", "renewal_date", "health_score", "health_color")},
               "evidence": ev, "memory": [m.get("text") for m in memory]}
    if feedback:
        payload["feedback_on_your_previous_draft"] = feedback  # revision round: Skeptic + Verifier notes
    user = _compact(payload)

    def offline() -> dict[str, Any]:
        claims, counter = [], []
        f = {k: v["facts"] for k, v in evidence.items()}
        u, ly, t, i, c = f.get("usage_trend", {}), f.get("usage_last_year", {}), f.get("tickets", {}), f.get("invoices", {}), f.get("contacts", {})
        if u.get("change_pct") is not None and u["change_pct"] <= -20:
            claims.append({"id": f"c{len(claims)+1}", "signal": "usage_decline", "severity": "high" if u["change_pct"] <= -35 else "medium",
                           "statement": f"Weekly active users fell {abs(u['change_pct'])}% (avg {u['avg_weekly_active_users_prior8']} -> {u['avg_weekly_active_users_last4']}); seat utilization is now {u['seat_utilization_pct']}%.",
                           "evidence": [{"view": "rind.product_usage", "fact": "change_pct", "value": u["change_pct"]}]})
        if ly.get("last_year_change_pct") is not None and ly["last_year_change_pct"] <= -25:
            counter.append(f"The same weeks last year also dropped {abs(ly['last_year_change_pct'])}%, so the dip may be seasonal.")
        if c.get("champion_left"):
            claims.append({"id": f"c{len(claims)+1}", "signal": "champion_left", "severity": "high",
                           "statement": f"Champion {c.get('departed_champion')} ({c.get('departed_title')}) left {c.get('days_since_departure')} days ago for {c.get('departed_to')}.",
                           "evidence": [{"view": "rind.contacts", "fact": "champion_left", "value": True}]})
            if c.get("replacement_champion_named"):
                counter.append(f"A replacement champion, {c.get('active_champion')}, is named and was last engaged {c.get('active_champion_last_engaged')}.")
        if (i.get("latest_days_overdue") or 0) > 14:
            claims.append({"id": f"c{len(claims)+1}", "signal": "invoice_overdue", "severity": "medium",
                           "statement": f"Invoice {i['latest_invoice']} ({_fmt_money(i['latest_amount_usd'])}) is {i['latest_days_overdue']} days overdue.",
                           "evidence": [{"view": "rind.billing_invoices", "fact": "latest_days_overdue", "value": i["latest_days_overdue"]}]})
            if (i.get("past_avg_days_late") or 0) >= 25:
                counter.append(f"This customer paid {i['past_invoices_paid_late']} of {i['past_invoices']} past invoices late (avg {i['past_avg_days_late']} days), so lateness is a habit.")
        if (t.get("open_high_or_urgent") or 0) >= 1:
            claims.append({"id": f"c{len(claims)+1}", "signal": "escalating_tickets", "severity": "high" if t["open_high_or_urgent"] >= 2 else "medium",
                           "statement": f"{t['open_high_or_urgent']} high/urgent tickets are still open (e.g. \"{t['open_high_subjects'][0]}\"); 30-day sentiment {t['avg_sentiment_last30']}.",
                           "evidence": [{"view": "rind.support_tickets", "fact": "open_high_or_urgent", "value": t["open_high_or_urgent"]}]})
        elif (t.get("solved_urgent_last30") or 0) >= 1:
            claims.append({"id": f"c{len(claims)+1}", "signal": "escalating_tickets", "severity": "low",
                           "statement": f"An urgent ticket was opened in the last 30 days (\"{t.get('latest_ticket_subject')}\").",
                           "evidence": [{"view": "rind.support_tickets", "fact": "solved_urgent_last30", "value": t["solved_urgent_last30"]}]})
            counter.append(f"That urgent ticket was solved and the latest ticket sentiment is {t.get('latest_ticket_sentiment')}.")
        return {"claims": claims, "counter_evidence": counter}

    out = llm.complete_json("Investigator", "fast", INVESTIGATOR_CLAIMS_SYS, user, offline, acct["account_id"], 1200)
    out = out if isinstance(out, dict) else {"claims": out if isinstance(out, list) else []}
    out.setdefault("claims", [])
    out.setdefault("counter_evidence", [])
    return out


# ============================================================================ Skeptic

SKEPTIC_SYS = """You are the Skeptic in Rind. Your job is to stop false alarms and unsupported claims before they reach the CSM.
For each claim decide: supported, unsupported, or needs_check.
- unsupported: no cited evidence, numbers don't match the evidence, or counter-evidence explains it
  (seasonal dip that also happened last year; customer has always paid late; a replacement champion is active
  and usage is stable; the urgent ticket was solved and sentiment recovered; a remembered note explains it).
Use the derived flags in the evidence, they are computed from the data:
  seasonal_dip_last_year=true means the same dip happened last year (seasonal, not a new problem);
  seasonal_dip_last_year=false means last year was flat or up, so the decline IS new.
  habitual_late_payer=true means late payment is normal for them; overdue_is_new_behaviour=true means it is new.
  replacement_champion_named=true plus stable usage means the champion change is handled.
  urgent_issue_still_open=false means the urgent ticket was solved.
- needs_check: plausible but an obvious alternative explanation hasn't been checked yet. Name ONE check:
  usage_decline -> usage_last_year, invoice_overdue -> invoices, champion_left -> contacts, escalating_tickets -> tickets.
Then give overall churn risk from supported claims only: high (2+ strong signals, or 1 severe near renewal),
medium (1 supported signal), low, or none. Confidence 0-1.
Respond with JSON only:
{"verdicts":[{"claim_id":"c1","verdict":"supported","reason":"one sentence","check":null}],
 "risk":"high","confidence":0.8,"summary":"one sentence"}"""


def review(llm: LLM, acct: dict[str, Any], claims: dict[str, Any], evidence: dict[str, dict[str, Any]],
           memory: list[dict[str, Any]], round_no: int) -> dict[str, Any]:
    ev = {k: v["facts"] for k, v in evidence.items()}
    user = _compact({"round": round_no, "account": {k: acct[k] for k in ("account_id", "account_name", "industry", "arr_usd", "renewal_date")},
                     "claims": claims.get("claims", []), "counter_evidence": claims.get("counter_evidence", []),
                     "evidence": ev, "memory": [m.get("text") for m in memory]})

    def offline() -> dict[str, Any]:
        verdicts, supported = [], []
        mem_text = " ".join(m.get("text", "") for m in memory).lower()
        for c in claims.get("claims", []):
            v, reason, chk = "supported", "Evidence cited and consistent.", None
            if not c.get("evidence"):
                v, reason = "unsupported", "No evidence cited."
            elif c["signal"] == "usage_decline":
                ly = ev.get("usage_last_year")
                if ly is None:
                    v, reason, chk = "needs_check", "Could be seasonal; compare with the same weeks last year.", "usage_last_year"
                elif (ly.get("last_year_change_pct") or 0) <= -25:
                    v, reason = "unsupported", f"Same dip last year ({ly['last_year_change_pct']}%), so it is seasonal."
                else:
                    reason = f"Last year the same weeks changed {ly.get('last_year_change_pct')}%, so this decline is new."
            elif c["signal"] == "invoice_overdue":
                inv = ev.get("invoices", {})
                if "accounts-payable" in mem_text or "pays every vendor" in mem_text:
                    v, reason = "unsupported", "Remembered note: this customer's AP always pays late."
                elif (inv.get("past_avg_days_late") or 0) >= 25 and (inv.get("past_invoices_paid_late") or 0) >= 2:
                    v, reason = "unsupported", f"Habitual late payer (avg {inv['past_avg_days_late']} days late historically)."
                else:
                    reason = "This customer normally pays on time, so the overdue invoice is new behaviour."
            elif c["signal"] == "champion_left":
                con = ev.get("contacts", {})
                u = ev.get("usage_trend", {})
                if con.get("replacement_champion_named") and (u.get("change_pct") or 0) > -20:
                    v, reason = "unsupported", f"Replacement champion {con.get('active_champion')} is active and usage is stable."
            elif c["signal"] == "escalating_tickets":
                t = ev.get("tickets", {})
                if (t.get("open_high_or_urgent") or 0) == 0:
                    v, reason = "unsupported", "The urgent ticket was solved and sentiment recovered."
            verdicts.append({"claim_id": c["id"], "verdict": v, "reason": reason, "check": chk})
            if v == "supported":
                supported.append(c)
        strong = sum(1 for c in supported if c.get("severity") in ("high", "medium"))
        risk = "high" if strong >= 2 else ("medium" if strong == 1 else ("low" if supported else "none"))
        pending = any(v["verdict"] == "needs_check" for v in verdicts)
        return {"verdicts": verdicts, "risk": risk, "confidence": 0.6 if pending else 0.85,
                "summary": f"{len(supported)} supported claim(s) after review."}

    out = llm.complete_json("Skeptic", "deep", SKEPTIC_SYS, user, offline, acct["account_id"], 1200)
    out = out if isinstance(out, dict) else {"verdicts": out if isinstance(out, list) else []}
    out.setdefault("verdicts", [])
    if out.get("risk") not in ("high", "medium", "low", "none"):
        out["risk"] = "medium"
    for v in out["verdicts"]:
        if v.get("check") not in CHECKS:
            v["check"] = None
    return out


# ============================================================================ Escalation (policy agent)

def escalate(acct: dict[str, Any], risk: str, as_of: date) -> dict[str, Any]:
    """Playbook policy, deterministic on purpose: escalation must be predictable and auditable."""
    days = (date.fromisoformat(str(acct["renewal_date"])) - as_of).days
    arr = int(acct["arr_usd"])
    if risk == "high" and (arr >= 100_000 or days <= 60):
        return {"escalate": True, "to": "CS Director + exec sponsor", "rule": "high risk and (ARR >= $100k or renewal <= 60 days)",
                "renewal_in_days": days}
    if risk == "medium" and days <= 45:
        return {"escalate": True, "to": "CS Director", "rule": "medium risk and renewal <= 45 days", "renewal_in_days": days}
    return {"escalate": False, "rule": "below escalation thresholds", "renewal_in_days": days}


# ============================================================================ Reporter

REPORTER_SYS = """You are the Reporter in Rind. Write the account brief a busy CSM reads on Monday morning.
Use only supported claims. Every reason must name its source system. If memory shows a play was already tried
and the problem persists, recommend a different play and say why. Plays to choose from:
exec_sponsor_meeting, new_champion_onboarding, adoption_workshop, support_escalation, billing_outreach, renewal_risk_review.
Draft a short, human email (no hype) to the right contact.
Use only facts in the input. Usage numbers are weekly ACTIVE USERS (people), not minutes.
Always include recommended_play and draft_email.
Respond with JSON only:
{"headline":"one line","why":[{"reason":"sentence with numbers","source":"Product analytics"}],
 "recommended_play":"exec_sponsor_meeting","play_rationale":"one sentence","memory_used":"sentence or null",
 "draft_email":{"to":"name","subject":"...","body":"..."}}"""

SOURCE_NAME = {"rind.product_usage": "Product analytics", "rind.support_tickets": "Support desk",
               "rind.billing_invoices": "Billing", "rind.contacts": "Contact directory", "rind.crm_accounts": "CRM"}


def report(llm: LLM, acct: dict[str, Any], supported: list[dict[str, Any]], risk: str, esc: dict[str, Any],
           evidence: dict[str, dict[str, Any]], memory: list[dict[str, Any]]) -> dict[str, Any]:
    contacts = evidence.get("contacts", {}).get("facts", {})
    user = _compact({"account": {k: acct[k] for k in ("account_id", "account_name", "arr_usd", "renewal_date", "health_score", "health_color")},
                     "risk": risk, "escalation": esc, "supported_claims": supported, "contacts": contacts,
                     "memory": memory})

    def offline() -> dict[str, Any]:
        signals = {c["signal"] for c in supported}
        tried = " ".join(m.get("text", "") for m in memory if m.get("type") == "action").lower()
        if "champion_left" in signals:
            play = "new_champion_onboarding"
        elif "escalating_tickets" in signals:
            play = "support_escalation"
        elif "usage_decline" in signals:
            play = "adoption_workshop"
        elif "invoice_overdue" in signals:
            play = "billing_outreach"
        else:
            play = "renewal_risk_review"
        memory_used = None
        if "training" in tried and play == "adoption_workshop":
            play = "exec_sponsor_meeting"
            memory_used = "Admin training was already tried on Oct 8 and usage kept falling, so escalate to the exec sponsor instead of repeating training."
        if esc.get("escalate") and play not in ("exec_sponsor_meeting",) and len(signals) >= 2:
            play = "exec_sponsor_meeting"
        to = contacts.get("exec_sponsor") if play == "exec_sponsor_meeting" else (contacts.get("active_champion") or contacts.get("admin_contact") or contacts.get("exec_sponsor"))
        green = acct["health_color"] == "green"
        return {
            "headline": f"{acct['account_name']}: {'looks green (' + str(acct['health_score']) + ') but ' if green else ''}{risk.upper()} churn risk, renews in {esc['renewal_in_days']} days ({_fmt_money(acct['arr_usd'])} ARR).",
            "why": [{"reason": c["statement"], "source": SOURCE_NAME.get(c["evidence"][0]["view"], "Zetaris")} for c in supported],
            "recommended_play": play,
            "play_rationale": f"Addresses the strongest supported signal ({', '.join(sorted(signals)) or 'none'}).",
            "memory_used": memory_used,
            "draft_email": {
                "to": to or "primary contact",
                "subject": f"Checking in ahead of your renewal - {acct['account_name']}",
                "body": (f"Hi {(to or 'there').split()[0]},\n\nI wanted to reach out personally ahead of your renewal. "
                         "I've noticed a few changes on your account recently and would value 30 minutes to hear how things "
                         "are going and make sure the team is getting what it needs from us.\n\nWould Thursday or Friday work?\n\nBest,\nDana"),
            },
        }

    out = llm.complete_json("Reporter", "deep", REPORTER_SYS, user, offline, acct["account_id"], 1200)
    out = out if isinstance(out, dict) else {"why": out if isinstance(out, list) else []}
    # The model sometimes leaves a field out. Fill only the missing fields from the rule-based brief, and say so.
    base, filled = offline(), []
    plays = {"exec_sponsor_meeting", "new_champion_onboarding", "adoption_workshop", "support_escalation",
             "billing_outreach", "renewal_risk_review"}
    if out.get("recommended_play") not in plays:
        out["recommended_play"], out["play_rationale"] = base["recommended_play"], base["play_rationale"]
        filled.append("recommended_play")
    em = out.get("draft_email")
    if not isinstance(em, dict) or not em.get("body"):
        out["draft_email"] = base["draft_email"]
        filled.append("draft_email")
    for k in ("headline", "play_rationale", "why"):
        if not out.get(k):
            out[k] = base[k]
            filled.append(k)
    if filled:
        out["completed_by_rules"] = filled
    return out
