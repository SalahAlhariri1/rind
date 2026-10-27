"""If the model's brief leaves out the play or the email, Rind fills only those fields from its rules and says so."""
from agent import agents


class _PartialLLM:
    def complete_json(self, *args, **kwargs):
        return {"headline": "Bluegate Academy: high churn risk", "why": [{"reason": "Champion left", "source": "Contacts"}]}


def test_missing_fields_are_completed_and_flagged():
    acct = {"account_id": "ACC-1058", "account_name": "Bluegate Academy", "arr_usd": 86600, "renewal_date": "2027-01-07",
            "health_score": 72, "health_color": "green"}
    supported = [{"signal": "champion_left", "statement": "Champion left", "evidence": [{"view": "rind.contacts"}]},
                 {"signal": "invoice_overdue", "statement": "Invoice late", "evidence": [{"view": "rind.billing_invoices"}]}]
    evidence = {"contacts": {"facts": {"active_champion": None, "admin_contact": "Chloe Abara", "exec_sponsor": "Sam Lee"}}}
    out = agents.report(_PartialLLM(), acct, supported, "high", {"escalate": False, "renewal_in_days": 80}, evidence, [])
    assert out["headline"] == "Bluegate Academy: high churn risk"          # the model's own text is kept
    assert out["recommended_play"] == "new_champion_onboarding"            # missing field filled by the rules
    assert out["draft_email"]["body"]
    assert out["completed_by_rules"] == ["recommended_play", "draft_email"]  # and the fill is disclosed
