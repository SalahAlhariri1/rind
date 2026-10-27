"""Generate Rind's synthetic customer-success dataset.

Five "systems" that a CSM normally checks in five browser tabs, each exported
as JSON in the shape Zetaris REST tables read best:  {"system": ..., "data": [ ... ]}

  crm_accounts.json      CRM (account record, health score, renewal, CSM notes)
  product_usage.json     product analytics (weekly active users, this year + last year)
  support_tickets.json   help desk (tickets keyed by the customer's email domain)
  billing_invoices.json  billing (invoices keyed by billing customer number)
  contacts.json          people directory with job-change signals

Each system uses its own key for the customer, as real systems do:
  CRM account_id "ACC-1001" | usage account_ref "acc1001" | support org_domain |
  billing customer_no + legal_name | contacts company_domain
The CRM row carries the mapping (domain, billing_customer_no), so it is the hub.

Planted ground truth (written to eval/ground_truth.json, NOT served through Zetaris):
  watermelon (8)              green CRM score, but 2-3 real churn signals
  red_known (5)               already red in the CRM
  decoy_seasonal (4)          usage dropped, but the same dip happened last year
  decoy_ticket (3)            one angry urgent ticket, solved fast, sentiment recovered
  decoy_ap (3)                invoice late, but this customer has always paid late
  decoy_champion_replaced (2) champion left, replacement already active, usage stable
  healthy (35)                normal noise

All companies, people and numbers are fictional. Deterministic (seed 42).
Run:  python scripts/generate_data.py
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
AS_OF = date(2026, 10, 19)  # Monday the CSM runs the weekly check
WEEKS = 16
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "sources"
EVAL = ROOT / "eval"

rng = random.Random(SEED)

PREFIX = ["Bluegate", "Harborline", "Cinderfield", "Maplecrest", "Quarrystone", "Lumenway",
          "Tidewater", "Granitepeak", "Orchidbay", "Pioneerpoint", "Copperline", "Sablewood",
          "Riverbend", "Halcyon", "Ironwood", "Brightpath", "Cobaltridge", "Juniperhill",
          "Summitview", "Westmarch", "Northfold", "Kestrel", "Ambergate", "Silverlake",
          "Foxhollow", "Driftwood", "Redcliff", "Stonebridge", "Clearwater", "Highmoor",
          "Oakhaven", "Larkspur", "Meridian", "Thornbury", "Bramblewood", "Eastvale",
          "Glenrock", "Wrenfield", "Ashcombe", "Pinecrest", "Saltmarsh", "Corvid",
          "Hollowell", "Marigold", "Ravenshaw", "Fernleaf", "Coldspring", "Starling",
          "Birchmont", "Goldfinch", "Windward", "Tallgrass", "Bayshore", "Cedarline",
          "Heronsgate", "Mossbank", "Elmstead", "Quillon", "Valewood", "Brookhaven"]

INDUSTRY = {
    "Logistics": ["Logistics", "Freight", "Haulage"],
    "Healthcare": ["Health", "Clinics", "Dental Group"],
    "Manufacturing": ["Manufacturing", "Fabrication", "Components"],
    "Financial Services": ["Credit Union", "Capital", "Insurance"],
    "Retail": ["Retail", "Outfitters", "Markets"],
    "Software": ["Labs", "Analytics", "Systems"],
    "Hospitality": ["Hotels", "Resorts", "Hospitality"],
    "Agriculture": ["Ag Co-op", "Growers", "Farm Supply"],
    "Education": ["Academy", "Learning", "College"],
    "Energy": ["Energy", "Power", "Utilities"],
}
SEASONAL_INDUSTRIES = ["Agriculture", "Education", "Hospitality"]

FIRST = ["Maya", "Jordan", "Priya", "Lucas", "Amara", "Diego", "Hannah", "Kenji", "Fatima", "Owen",
         "Sofia", "Marcus", "Leila", "Ethan", "Grace", "Tomas", "Nadia", "Ryan", "Chloe", "Samir",
         "Elena", "Isaac", "Zara", "Caleb", "Mei", "Andre", "Ruth", "Victor", "Ines", "Noah",
         "Talia", "Bruno", "Keira", "Felix", "Anya", "Malik", "Olivia", "Hugo", "Wren", "Dev"]
LAST = ["Okafor", "Lindqvist", "Patel", "Moreau", "Nakamura", "Alvarez", "Brennan", "Kowalski",
        "Haddad", "Fischer", "Osei", "Romano", "Chen", "Duarte", "Novak", "Gallagher", "Iyer",
        "Sato", "Mbeki", "Larsen", "Quinn", "Varga", "Abara", "Holt", "Petrov", "Reyes",
        "Sandoval", "Tanaka", "Whitfield", "Yilmaz"]

TICKET_SUBJECTS_BAD = [
    "Workflow automations failing silently since last release",
    "SSO login loop blocking half our team",
    "Data export missing rows - need this fixed before audit",
    "Dashboard load times over 30 seconds",
    "Integration with our ERP stopped syncing",
    "Still waiting on a fix for the permissions bug",
]
TICKET_SUBJECTS_OK = [
    "How do I add a custom field to a template?",
    "Request: bulk-invite users via CSV",
    "Question about API rate limits",
    "Change billing contact",
    "Feature request: dark mode for mobile",
    "Help setting up a recurring report",
    "Can we rename a workspace?",
]

NOTES_HEALTHY = [
    "QBR went well, exploring add-on seats for Q1.",
    "Champion happy with rollout; asked about advanced reporting.",
    "Routine check-in, no concerns raised.",
    "Expanding to a second department next quarter.",
    "Positive call; shared roadmap preview.",
]
NOTES_WATERMELON = [
    "Quick check-in, all good per champion.",
    "Renewal conversation scheduled; no red flags mentioned.",
    "Champion busy, rescheduled QBR.",
    "Sent renewal quote, awaiting reply.",
]


def wk(i: int, year_offset: int = 0) -> date:
    """Monday of week i (0 = oldest) in the 16-week window, optionally a year earlier."""
    start = AS_OF - timedelta(weeks=WEEKS)
    d = start + timedelta(weeks=i)
    if year_offset:
        d = d - timedelta(weeks=52 * year_offset)
    return d


def person(used: set[str]) -> str:
    while True:
        n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if n not in used:
            used.add(n)
            return n


def main() -> None:
    labels = (["watermelon"] * 8 + ["red_known"] * 5 + ["decoy_seasonal"] * 4 + ["decoy_ticket"] * 3
              + ["decoy_ap"] * 3 + ["decoy_champion_replaced"] * 2 + ["healthy"] * 35)
    rng.shuffle(labels)

    # Watermelon signal combos: every signal type appears at least 3 times.
    wm_combos = [
        {"usage_decline", "champion_left"},
        {"usage_decline", "invoice_overdue"},
        {"champion_left", "escalating_tickets"},
        {"usage_decline", "escalating_tickets", "champion_left"},
        {"invoice_overdue", "escalating_tickets"},
        {"usage_decline", "invoice_overdue", "champion_left"},
        {"champion_left", "invoice_overdue"},
        {"usage_decline", "escalating_tickets"},
    ]
    red_combos = [
        {"usage_decline", "escalating_tickets"},
        {"usage_decline", "invoice_overdue", "champion_left"},
        {"escalating_tickets", "champion_left"},
        {"usage_decline", "champion_left"},
        {"usage_decline", "invoice_overdue", "escalating_tickets"},
    ]

    names = rng.sample(PREFIX, 60)
    crm, usage, tickets, invoices, contacts, truth = [], [], [], [], [], []
    used_people: set[str] = set()
    t_id, inv_id, c_id = 50001, 90001, 7001
    wm_i = red_i = 0

    for idx in range(60):
        label = labels[idx]
        acct_num = 1001 + idx
        account_id = f"ACC-{acct_num}"
        if label == "decoy_seasonal":
            industry = SEASONAL_INDUSTRIES[idx % len(SEASONAL_INDUSTRIES)]
        else:
            industry = rng.choice([k for k in INDUSTRY if k not in SEASONAL_INDUSTRIES] * 3 + SEASONAL_INDUSTRIES)
        suffix = rng.choice(INDUSTRY[industry])
        name = f"{names[idx]} {suffix}"
        domain = f"{names[idx].lower()}{suffix.split()[0].lower()}.example"
        legal_name = f"{name}, {rng.choice(['Inc.', 'LLC', 'Ltd.', 'Corp.'])}"
        segment = rng.choices(["SMB", "Mid-Market", "Enterprise"], weights=[4, 4, 2])[0]
        seats = {"SMB": rng.randint(15, 60), "Mid-Market": rng.randint(60, 250), "Enterprise": rng.randint(250, 900)}[segment]
        price = {"SMB": 420, "Mid-Market": 380, "Enterprise": 330}[segment]
        arr = int(round(seats * price, -2))
        customer_no = f"C-{rng.randint(10000, 99999)}"

        signals: set[str] = set()
        if label == "watermelon":
            signals = wm_combos[wm_i]; wm_i += 1
        elif label == "red_known":
            signals = red_combos[red_i]; red_i += 1

        # Renewal: at-risk accounts skew to near-term renewals (that's what makes them urgent).
        if label in ("watermelon", "red_known"):
            renewal = AS_OF + timedelta(days=rng.randint(28, 150))
        else:
            renewal = AS_OF + timedelta(days=rng.randint(20, 360))

        # ---------------- Product usage (this year + last year) ----------------
        base = seats * rng.uniform(0.62, 0.86)
        seasonal = label == "decoy_seasonal"
        declining = "usage_decline" in signals
        for year_offset in (0, 1):
            yr_base = base * (0.9 if year_offset else 1.0)
            for i in range(WEEKS):
                f = 1.0 + rng.uniform(-0.06, 0.06)
                if seasonal and i >= 11:
                    f *= rng.uniform(0.52, 0.62)  # same dip both years
                if declining and year_offset == 0 and i >= 8:
                    f *= max(0.38, 1.0 - 0.09 * (i - 7))  # steady slide in the last 8 weeks
                if label == "decoy_champion_replaced" and year_offset == 0 and 9 <= i <= 10:
                    f *= 0.88  # brief blip during the handover, then recovers
                au = max(1, int(yr_base * f))
                usage.append({
                    "account_ref": f"acc{acct_num}",
                    "week_start": wk(i, year_offset).isoformat(),
                    "active_users": au,
                    "seats_purchased": seats,
                    "sessions": int(au * rng.uniform(5.5, 9.5)),
                    "workflows_run": int(au * rng.uniform(2.0, 4.5) * (0.7 if declining and year_offset == 0 and i >= 10 else 1.0)),
                })

        # ---------------- Support tickets ----------------
        n_routine = rng.randint(1, 5)
        for _ in range(n_routine):
            opened = AS_OF - timedelta(days=rng.randint(3, 110))
            solved = opened + timedelta(days=rng.randint(0, 4))
            tickets.append({
                "ticket_id": f"T-{t_id}", "org_domain": domain, "opened_at": opened.isoformat(),
                "priority": rng.choice(["low", "normal", "normal"]), "status": "solved" if solved < AS_OF else "pending",
                "subject": rng.choice(TICKET_SUBJECTS_OK), "sentiment": round(rng.uniform(0.1, 0.8), 2),
                "solved_at": solved.isoformat() if solved < AS_OF else None,
                "first_response_hours": round(rng.uniform(0.5, 8), 1),
            }); t_id += 1
        if "escalating_tickets" in signals:
            for k in range(rng.randint(2, 4)):
                opened = AS_OF - timedelta(days=rng.randint(4, 30))
                tickets.append({
                    "ticket_id": f"T-{t_id}", "org_domain": domain, "opened_at": opened.isoformat(),
                    "priority": "urgent" if k == 0 else rng.choice(["high", "urgent"]),
                    "status": rng.choice(["open", "pending", "open"]),
                    "subject": rng.choice(TICKET_SUBJECTS_BAD), "sentiment": round(rng.uniform(-0.9, -0.45), 2),
                    "solved_at": None, "first_response_hours": round(rng.uniform(10, 40), 1),
                }); t_id += 1
        if label == "decoy_ticket":
            opened = AS_OF - timedelta(days=rng.randint(10, 25))
            tickets.append({
                "ticket_id": f"T-{t_id}", "org_domain": domain, "opened_at": opened.isoformat(),
                "priority": "urgent", "status": "solved", "subject": rng.choice(TICKET_SUBJECTS_BAD),
                "sentiment": -0.72, "solved_at": (opened + timedelta(days=1)).isoformat(),
                "first_response_hours": 0.8,
            }); t_id += 1
            tickets.append({
                "ticket_id": f"T-{t_id}", "org_domain": domain,
                "opened_at": (opened + timedelta(days=3)).isoformat(), "priority": "low", "status": "solved",
                "subject": "Thanks - the fix works, closing this out", "sentiment": 0.85,
                "solved_at": (opened + timedelta(days=3)).isoformat(), "first_response_hours": 1.2,
            }); t_id += 1

        # ---------------- Billing invoices (quarterly, last 12 months) ----------------
        slow_payer = label == "decoy_ap"
        for q in range(4):
            issued = AS_OF - timedelta(days=rng.randint(70, 82) + 91 * (3 - q))  # latest invoice fell due ~6 weeks ago
            due = issued + timedelta(days=30)
            latest = q == 3
            if "invoice_overdue" in signals and latest:
                paid = None
            elif slow_payer:
                paid = None if latest else due + timedelta(days=rng.randint(34, 48))
            else:
                paid = due - timedelta(days=rng.randint(0, 12))
            if paid is not None and paid > AS_OF:
                paid = None
            status = "paid" if paid else ("overdue" if AS_OF > due else "open")
            invoices.append({
                "invoice_id": f"INV-{inv_id}", "customer_no": customer_no, "legal_name": legal_name,
                "issued_on": issued.isoformat(), "due_on": due.isoformat(), "amount_usd": arr // 4,
                "paid_on": paid.isoformat() if paid else None, "status": status,
            }); inv_id += 1

        # ---------------- Contacts ----------------
        champ = person(used_people)
        champ_left = "champion_left" in signals or label == "decoy_champion_replaced"
        left_on = AS_OF - timedelta(days=rng.randint(12, 45)) if champ_left else None
        contacts.append({
            "contact_id": f"P-{c_id}", "company_domain": domain, "full_name": champ,
            "title": rng.choice(["VP Operations", "Director of Operations", "Head of Revenue Ops", "IT Director"]),
            "role": "champion", "status": "left_company" if champ_left else "active",
            "changed_on": left_on.isoformat() if left_on else None,
            "new_company": f"{rng.choice(PREFIX)} {rng.choice(['Group', 'Partners', 'Holdings'])}" if champ_left else None,
            "last_engaged_on": ((left_on - timedelta(days=rng.randint(5, 20))) if left_on else AS_OF - timedelta(days=rng.randint(3, 30))).isoformat(),
        }); c_id += 1
        contacts.append({
            "contact_id": f"P-{c_id}", "company_domain": domain, "full_name": person(used_people),
            "title": rng.choice(["CFO", "COO", "Chief of Staff", "SVP Operations"]), "role": "exec_sponsor",
            "status": "active", "changed_on": None, "new_company": None,
            "last_engaged_on": (AS_OF - timedelta(days=rng.randint(30, 200))).isoformat(),
        }); c_id += 1
        contacts.append({
            "contact_id": f"P-{c_id}", "company_domain": domain, "full_name": person(used_people),
            "title": "Systems Administrator", "role": "admin", "status": "active", "changed_on": None,
            "new_company": None, "last_engaged_on": (AS_OF - timedelta(days=rng.randint(2, 40))).isoformat(),
        }); c_id += 1
        if label == "decoy_champion_replaced":
            contacts.append({
                "contact_id": f"P-{c_id}", "company_domain": domain, "full_name": person(used_people),
                "title": "VP Operations", "role": "champion", "status": "active",
                "changed_on": (left_on + timedelta(days=4)).isoformat(), "new_company": None,
                "last_engaged_on": (AS_OF - timedelta(days=rng.randint(2, 6))).isoformat(),
            }); c_id += 1

        # ---------------- CRM account (the stale health score) ----------------
        if label == "red_known":
            health = rng.randint(24, 44)
        elif label == "healthy" and rng.random() < 0.2:
            health = rng.randint(55, 69)
        else:
            health = rng.randint(71, 90)
        color = "green" if health >= 70 else ("amber" if health >= 50 else "red")
        note = (rng.choice(NOTES_WATERMELON) if label == "watermelon" else
                "Escalation call held; customer frustrated with open issues." if label == "red_known" else
                rng.choice(NOTES_HEALTHY))
        nps = (rng.randint(8, 10) if label in ("watermelon", "healthy", "decoy_ticket", "decoy_ap", "decoy_seasonal", "decoy_champion_replaced")
               else rng.randint(1, 5))
        crm.append({
            "account_id": account_id, "account_name": name, "domain": domain, "billing_customer_no": customer_no,
            "industry": industry, "segment": segment, "arr_usd": arr, "seats_purchased": seats,
            "renewal_date": renewal.isoformat(), "csm_owner": "Dana Reyes",
            "health_score": health, "health_color": color,
            # The score is recalculated monthly from logins + last NPS survey, so it lags reality.
            "health_updated_on": (AS_OF - timedelta(days=rng.randint(24, 40))).isoformat(),
            "nps_last": nps, "nps_date": (AS_OF - timedelta(days=rng.randint(60, 140))).isoformat(),
            "last_csm_touch": (AS_OF - timedelta(days=rng.randint(4, 35))).isoformat(), "csm_note": note,
        })
        truth.append({"account_id": account_id, "account_name": name, "label": label,
                      "at_risk": label in ("watermelon", "red_known"), "signals": sorted(signals)})

    OUT.mkdir(parents=True, exist_ok=True)
    EVAL.mkdir(parents=True, exist_ok=True)
    exported = f"{AS_OF.isoformat()}T06:00:00Z"
    files = {
        "crm_accounts.json": ("CRM", crm),
        "product_usage.json": ("Product analytics", usage),
        "support_tickets.json": ("Support desk", tickets),
        "billing_invoices.json": ("Billing", invoices),
        "contacts.json": ("Contact directory", contacts),
    }
    for fname, (system, rows) in files.items():
        (OUT / fname).write_text(json.dumps({"system": system, "exported_at": exported, "record_count": len(rows), "data": rows}, indent=1))
        print(f"{fname:24s} {len(rows):5d} rows")
    (EVAL / "ground_truth.json").write_text(json.dumps({"as_of": AS_OF.isoformat(), "accounts": truth}, indent=1))
    counts: dict[str, int] = {}
    for t in truth:
        counts[t["label"]] = counts.get(t["label"], 0) + 1
    print("labels:", counts)


if __name__ == "__main__":
    main()
