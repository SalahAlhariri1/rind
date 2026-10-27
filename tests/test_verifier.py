"""The Verifier catches the exact LLM failure modes seen in the first live run."""
from agent import verifier

EVID = {
    "usage_trend": {"view": "rind.product_usage", "facts": {"change_pct": -48.9, "seat_utilization_pct": 36.5}},
    "usage_last_year": {"view": "rind.product_usage", "facts": {"last_year_change_pct": 3.3, "seasonal_dip_last_year": False}},
    "invoices": {"view": "rind.billing_invoices", "facts": {"latest_invoice": "INV-90204", "latest_days_overdue": 47,
                                                             "habitual_late_payer": False, "past_avg_days_late": -2.0}},
    "tickets": {"view": "rind.support_tickets", "facts": {"open_high_or_urgent": 0, "solved_urgent_last30": 0,
                                                          "urgent_issue_still_open": False}},
}


def test_rejects_facts_from_another_account_and_non_claims():
    claims = [
        {"id": "c1", "signal": "invoice_overdue", "statement": "Invoice INV-90044 is 49 days overdue.",
         "evidence": [{"view": "rind.billing_invoices", "fact": "latest_days_overdue", "value": 49}]},
        {"id": "c2", "signal": "other", "statement": "No champion departure data was found.",
         "evidence": [{"view": "rind.contacts", "fact": "champion_left", "value": False}]},
        {"id": "c3", "signal": "escalating_tickets", "statement": "Latest ticket is 'Change billing contact'.",
         "evidence": [{"view": "rind.support_tickets", "fact": "open_high_or_urgent", "value": 0}]},
        {"id": "c4", "signal": "usage_decline", "statement": "Weekly active users fell 48.9%.",
         "evidence": [{"view": "rind.product_usage", "fact": "change_pct", "value": -48.9}]},
    ]
    kept, rejected = verifier.gate_claims(claims, EVID)
    assert [c["id"] for c in kept] == ["c4"]
    assert len(rejected) == 3


def test_overrides_a_skeptic_that_misreads_last_year():
    claims = [{"id": "c4", "signal": "usage_decline", "statement": "Weekly active users fell 48.9%.",
               "evidence": [{"view": "rind.product_usage", "fact": "change_pct", "value": -48.9}]}]
    wrong = [{"claim_id": "c4", "verdict": "unsupported", "reason": "contradicted by last year's 3.3% increase"}]
    final, overrides = verifier.check_verdicts(claims, wrong, EVID, [])
    assert final[0]["verdict"] == "supported" and overrides


def test_memory_blocks_repeating_a_failed_play():
    play, note = verifier.avoid_repeat_play("adoption_workshop", [{"type": "action", "text": "Offered admin training Oct 8"}])
    assert play == "exec_sponsor_meeting" and note


def test_missed_signals_are_caught_and_grounded():
    ev = {
        "usage_trend": {"view": "rind.product_usage", "facts": {"change_pct": -48.9, "avg_weekly_active_users_prior8": 111.5,
                                                                "avg_weekly_active_users_last4": 57.0}},
        "usage_last_year": {"view": "rind.product_usage", "facts": {"last_year_change_pct": 3.3, "seasonal_dip_last_year": False}},
        "contacts": {"view": "rind.contacts", "facts": {"champion_left": True, "departed_champion": "Nadia Sandoval",
                                                        "replacement_champion_named": False, "days_since_departure": 36}},
        "invoices": {"view": "rind.billing_invoices", "facts": {"latest_invoice": "INV-90204", "latest_days_overdue": 47,
                                                                 "latest_amount_usd": 14825, "habitual_late_payer": False,
                                                                 "past_avg_days_late": -2.0}},
        "tickets": {"view": "rind.support_tickets", "facts": {"open_high_or_urgent": 0, "solved_urgent_last30": 0,
                                                              "urgent_issue_still_open": False}},
    }
    usage = {"id": "c1", "signal": "usage_decline", "statement": "Weekly active users fell 48.9%.",
             "evidence": [{"view": "rind.product_usage", "fact": "change_pct", "value": -48.9}]}
    missed = {m["signal"] for m in verifier.missed_signals([usage], ev, [])}
    assert missed == {"champion_left", "invoice_overdue"}
    added = [verifier.synthesize_claim(s, ev, i + 2) for i, s in enumerate(sorted(missed))]
    kept, rejected = verifier.gate_claims([usage] + added, ev)
    assert len(kept) == 3 and not rejected
    final, _ = verifier.check_verdicts(kept, [], ev, [])
    assert verifier.risk_from([c for c, v in zip(kept, final) if v["verdict"] == "supported"]) == "high"
