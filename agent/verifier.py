"""Verifier: the deterministic check between the LLM agents and the CSM.

Nemotron does the reasoning (Investigator writes claims, Skeptic argues against them).
The Verifier makes sure both stay grounded in what Zetaris actually returned:

  gate_claims()    - a claim survives only if every cited fact exists in THIS account's
                     evidence with the same value, it names a real churn signal, and the
                     evidence actually shows that signal. Catches hallucinated numbers,
                     facts copied from another account, and "no problem found" non-claims.
  check_verdicts() - the Skeptic's rulings are checked against the derived evidence flags
                     (seasonal_dip_last_year, habitual_late_payer, replacement champion,
                     urgent issue still open). A ruling that contradicts the data is
                     overridden and the override is logged, so every disagreement is visible.
  risk_from()      - churn risk from the surviving claims, so the label is reproducible.
"""
from __future__ import annotations

import re
from typing import Any

SIGNALS = ("usage_decline", "champion_left", "invoice_overdue", "escalating_tickets")
VIEW_FOR = {"usage_decline": "rind.product_usage", "champion_left": "rind.contacts",
            "invoice_overdue": "rind.billing_invoices", "escalating_tickets": "rind.support_tickets"}


def _facts(evidence: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """view -> merged facts for that view (usage_trend + usage_last_year share a view)."""
    out: dict[str, dict[str, Any]] = {}
    for ev in evidence.values():
        out.setdefault(ev.get("view", "?"), {}).update(ev.get("facts", {}))
    return out


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    try:
        return abs(float(a) - float(b)) <= max(0.15, 0.01 * abs(float(b)))
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def signal_present(signal: str, f: dict[str, dict[str, Any]]) -> bool:
    u, c = f.get("rind.product_usage", {}), f.get("rind.contacts", {})
    i, t = f.get("rind.billing_invoices", {}), f.get("rind.support_tickets", {})
    if signal == "usage_decline":
        return (u.get("change_pct") or 0) <= -20
    if signal == "champion_left":
        return bool(c.get("champion_left"))
    if signal == "invoice_overdue":
        return (i.get("latest_days_overdue") or 0) > 14
    if signal == "escalating_tickets":
        return (t.get("open_high_or_urgent") or 0) >= 1 or (t.get("solved_urgent_last30") or 0) >= 1
    return False


def gate_claims(claims: list[dict[str, Any]], evidence: dict[str, dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    f = _facts(evidence)
    latest_inv = f.get("rind.billing_invoices", {}).get("latest_invoice")
    kept, rejected = [], []
    seen_signals: set[str] = set()
    for c in claims:
        reason = None
        sig = c.get("signal")
        evs = c.get("evidence") or []
        if sig not in SIGNALS:
            reason = "not one of the four churn signals"
        elif not evs:
            reason = "no evidence cited"
        else:
            for e in evs:
                view, fact = e.get("view"), e.get("fact")
                if view not in f or fact not in f[view]:
                    reason = f"cites {view}.{fact}, which this account's Zetaris results don't contain"
                    break
                if not _same(e.get("value"), f[view][fact]):
                    reason = f"cites {fact}={e.get('value')} but Zetaris returned {f[view][fact]}"
                    break
        if reason is None:
            for inv in re.findall(r"INV-\d+", c.get("statement", "")):
                if inv != latest_inv:
                    reason = f"mentions {inv}, which is not this account's invoice"
                    break
        if reason is None and not signal_present(sig, f):
            reason = f"the evidence does not show {sig}"
        if reason is None and sig in seen_signals:
            reason = "duplicate of another claim about the same signal"
        if reason:
            rejected.append({"claim": c.get("statement", ""), "reason": f"Verifier: {reason}", "signal": sig})
        else:
            seen_signals.add(sig)
            kept.append(c)
    return kept, rejected


def check_verdicts(claims: list[dict[str, Any]], verdicts: list[dict[str, Any]], evidence: dict[str, dict[str, Any]],
                   memory: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Return (final verdicts, overrides). Only 'supported'/'unsupported' are final; a
    'needs_check' that could not be run is resolved from the data that is available."""
    f = _facts(evidence)
    u, c = f.get("rind.product_usage", {}), f.get("rind.contacts", {})
    i, t = f.get("rind.billing_invoices", {}), f.get("rind.support_tickets", {})
    mem = " ".join(m.get("text", "") for m in memory).lower()
    by_id = {v.get("claim_id"): v for v in verdicts}
    final, overrides = [], []
    for cl in claims:
        v = dict(by_id.get(cl["id"]) or {"claim_id": cl["id"], "verdict": "needs_check", "reason": "Skeptic gave no ruling"})
        sig = cl["signal"]
        should, why = None, None
        if sig == "usage_decline" and "seasonal_dip_last_year" in u:
            should = not u["seasonal_dip_last_year"]
            why = (f"last year's same weeks changed {u.get('last_year_change_pct')}%"
                   + (" too, so it is seasonal" if not should else ", so this decline is new"))
        elif sig == "invoice_overdue":
            ap_note = "accounts-payable" in mem or "pays every vendor" in mem or "net-45" in mem
            should = not (i.get("habitual_late_payer") or ap_note)
            why = ("this customer has always paid late" if not should
                   else f"they normally pay on time (avg {i.get('past_avg_days_late')} days), so this is new")
        elif sig == "champion_left":
            stable = (u.get("change_pct") or 0) > -20
            should = not (c.get("replacement_champion_named") and stable)
            why = (f"replacement champion {c.get('active_champion')} is active and usage is stable" if not should
                   else "no replacement champion is active")
        elif sig == "escalating_tickets":
            should = bool(t.get("urgent_issue_still_open"))
            why = ("the urgent ticket was solved and sentiment recovered" if not should
                   else f"{t.get('open_high_or_urgent')} high/urgent tickets are still open")
        if should is not None:
            want = "supported" if should else "unsupported"
            if v.get("verdict") != want:
                overrides.append({"claim_id": cl["id"], "signal": sig, "skeptic": v.get("verdict"),
                                  "skeptic_reason": v.get("reason"), "verifier": want, "reason": why})
                v = {"claim_id": cl["id"], "verdict": want, "reason": f"Verifier: {why} (Skeptic said {v.get('verdict')})",
                     "check": None}
        final.append(v)
    return final, overrides


def risk_from(supported: list[dict[str, Any]]) -> str:
    n = len({c["signal"] for c in supported})
    return "high" if n >= 2 else ("medium" if n == 1 else "none")


PLAY_TRIED = {"training": "adoption_workshop", "workshop": "adoption_workshop", "onboard": "new_champion_onboarding",
              "escalat": "support_escalation", "billing": "billing_outreach"}


def avoid_repeat_play(play: str, memory: list[dict[str, Any]]) -> tuple[str, str | None]:
    """Playbook policy: never recommend a play memory says was already tried."""
    tried = " ".join(m.get("text", "") for m in memory if m.get("type") == "action").lower()
    for kw, p in PLAY_TRIED.items():
        if kw in tried and p == play:
            return "exec_sponsor_meeting", (f"Memory shows {p.replace('_', ' ')} was already tried and the problem persists, "
                                           "so Rind escalates to the exec sponsor instead of repeating it.")
    return play, None


def _should_support(sig: str, f: dict[str, dict[str, Any]], mem: str) -> bool:
    """Same rules as check_verdicts: is this raw signal a real risk once the obvious explanations are ruled out?"""
    u, c = f.get("rind.product_usage", {}), f.get("rind.contacts", {})
    i, t = f.get("rind.billing_invoices", {}), f.get("rind.support_tickets", {})
    if sig == "usage_decline":
        return not u.get("seasonal_dip_last_year", False)
    if sig == "invoice_overdue":
        return not (i.get("habitual_late_payer") or "accounts-payable" in mem or "pays every vendor" in mem or "net-45" in mem)
    if sig == "champion_left":
        return not (c.get("replacement_champion_named") and (u.get("change_pct") or 0) > -20)
    if sig == "escalating_tickets":
        return bool(t.get("urgent_issue_still_open"))
    return False


def missed_signals(kept: list[dict[str, Any]], evidence: dict[str, dict[str, Any]],
                   memory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Warning signs the data shows, that are not explained away, but the Investigator made no claim about."""
    f = _facts(evidence)
    mem = " ".join(m.get("text", "") for m in memory).lower()
    claimed = {c.get("signal") for c in kept}
    out = []
    for sig in SIGNALS:
        if sig in claimed or not signal_present(sig, f) or not _should_support(sig, f, mem):
            continue
        u, c = f.get("rind.product_usage", {}), f.get("rind.contacts", {})
        i, t = f.get("rind.billing_invoices", {}), f.get("rind.support_tickets", {})
        detail = {"usage_decline": f"change_pct={u.get('change_pct')}",
                  "champion_left": f"champion_left=true, departed_champion={c.get('departed_champion')}",
                  "invoice_overdue": f"latest_days_overdue={i.get('latest_days_overdue')}",
                  "escalating_tickets": f"open_high_or_urgent={t.get('open_high_or_urgent')}"}[sig]
        out.append({"signal": sig, "detail": detail})
    return out


def synthesize_claim(sig: str, evidence: dict[str, dict[str, Any]], n: int) -> dict[str, Any] | None:
    """Last resort after a revision round: a claim built directly from the Zetaris facts, labelled as such."""
    f = _facts(evidence)
    u, c = f.get("rind.product_usage", {}), f.get("rind.contacts", {})
    i, t = f.get("rind.billing_invoices", {}), f.get("rind.support_tickets", {})
    if sig == "usage_decline":
        st = (f"Weekly active users fell {abs(u.get('change_pct', 0))}% "
              f"({u.get('avg_weekly_active_users_prior8')} -> {u.get('avg_weekly_active_users_last4')}); "
              f"the same weeks last year changed {u.get('last_year_change_pct')}%, so this is new.")
        ev = [{"view": "rind.product_usage", "fact": "change_pct", "value": u.get("change_pct")}]
    elif sig == "champion_left":
        st = (f"Champion {c.get('departed_champion')} ({c.get('departed_title')}) left {c.get('days_since_departure')} days ago "
              f"for {c.get('departed_to')}, and no replacement champion is active.")
        ev = [{"view": "rind.contacts", "fact": "champion_left", "value": True}]
    elif sig == "invoice_overdue":
        amt = i.get("latest_amount_usd") or 0
        st = (f"Invoice {i.get('latest_invoice')} (${int(amt):,}) is {i.get('latest_days_overdue')} days overdue; "
              f"this customer normally pays on time (avg {i.get('past_avg_days_late')} days).")
        ev = [{"view": "rind.billing_invoices", "fact": "latest_days_overdue", "value": i.get("latest_days_overdue")}]
    elif sig == "escalating_tickets":
        subj = (t.get("open_high_subjects") or ["(no subject)"])[0]
        st = f"{t.get('open_high_or_urgent')} high/urgent tickets are still open (e.g. \"{subj}\")."
        ev = [{"view": "rind.support_tickets", "fact": "open_high_or_urgent", "value": t.get("open_high_or_urgent")}]
    else:
        return None
    return {"id": f"v{n}", "signal": sig, "severity": "high", "statement": st, "evidence": ev, "added_by": "Verifier"}
