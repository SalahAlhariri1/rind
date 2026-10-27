"""Zetaris Unified Semantic Layer (USL) report for Rind.

  python scripts/zetaris_usl_report.py            # against your live Zetaris (uses .env)
  python scripts/zetaris_usl_report.py --sample   # same SQL on the local DuckDB copy (no Zetaris needed)

Covers the Zetaris evaluation checklist in one run and writes the evidence to
evidence/zetaris_usl_report.md (+ .json):
  1. Sources onboarded and verified (row count per source)
  2. Exploratory questions answered per source (3 per source, 15 total)
  3. The USL in action (3 questions only the unified layer can answer)
  4. Data quality rules: creates one rind.dq_* view per rule (scripts/zetaris_quality_rules.sql), then runs each
  5. Bonus: a hand-coded version of rind.account_360 in plain Python, compared row by row with the USL
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCES = {
    "CRM": "rind.crm_accounts",
    "Product usage": "rind.product_usage",
    "Support desk": "rind.support_tickets",
    "Billing": "rind.billing_invoices",
    "Contacts": "rind.contacts",
}

EXPLORE: dict[str, list[tuple[str, str]]] = {
    "CRM": [
        ("How is the book of business split by CRM health colour?",
         "SELECT health_color, COUNT(*) AS accounts, SUM(arr_usd) AS arr_usd FROM rind.crm_accounts GROUP BY health_color ORDER BY accounts DESC"),
        ("Which green accounts renew in the next 90 days?",
         "SELECT account_name, health_score, renewal_date, arr_usd FROM rind.crm_accounts "
         "WHERE health_color = 'green' AND renewal_date <= '2027-01-17' ORDER BY renewal_date"),
        ("How old are the health scores the CSM is relying on?",
         "SELECT MIN(health_updated_on) AS oldest_score, MAX(health_updated_on) AS newest_score, COUNT(*) AS accounts FROM rind.crm_accounts"),
    ],
    "Product usage": [
        ("How much usage history is there?",
         "SELECT COUNT(DISTINCT account_ref) AS accounts, COUNT(DISTINCT week_start) AS weeks, MIN(week_start) AS first_week, "
         "MAX(week_start) AS last_week FROM rind.product_usage"),
        ("Total weekly active users across all customers, last 6 weeks",
         "SELECT week_start, SUM(active_users) AS active_users FROM rind.product_usage GROUP BY week_start ORDER BY week_start DESC LIMIT 6"),
        ("Which customers use the fewest of the seats they pay for (latest week)?",
         "SELECT account_ref, week_start, active_users, seats_purchased, utilisation_pct FROM ("
         "SELECT account_ref, week_start, active_users, seats_purchased, ROUND(100.0 * active_users / seats_purchased, 1) AS utilisation_pct, "
         "DENSE_RANK() OVER (ORDER BY week_start DESC) AS week_rank FROM rind.product_usage) t "
         "WHERE week_rank = 1 ORDER BY utilisation_pct ASC LIMIT 5"),
    ],
    "Support desk": [
        ("Ticket volume by priority and status",
         "SELECT priority, status, COUNT(*) AS tickets FROM rind.support_tickets GROUP BY priority, status ORDER BY tickets DESC"),
        ("Who has the most open high/urgent tickets?",
         "SELECT org_domain, COUNT(*) AS open_high_or_urgent FROM rind.support_tickets "
         "WHERE status <> 'solved' AND priority IN ('high', 'urgent') GROUP BY org_domain ORDER BY open_high_or_urgent DESC LIMIT 5"),
        ("How do sentiment and first response time vary by priority?",
         "SELECT priority, ROUND(AVG(sentiment), 2) AS avg_sentiment, ROUND(AVG(first_response_hours), 1) AS avg_first_response_hours, "
         "COUNT(*) AS tickets FROM rind.support_tickets GROUP BY priority ORDER BY avg_sentiment"),
    ],
    "Billing": [
        ("Invoices by status",
         "SELECT status, COUNT(*) AS invoices, SUM(amount_usd) AS amount_usd FROM rind.billing_invoices GROUP BY status ORDER BY invoices DESC"),
        ("Largest overdue invoices",
         "SELECT invoice_id, legal_name, amount_usd, due_on FROM rind.billing_invoices WHERE status = 'overdue' ORDER BY amount_usd DESC LIMIT 5"),
        ("Which customers habitually pay after the due date?",
         "SELECT customer_no, MAX(legal_name) AS legal_name, COUNT(*) AS paid_invoices, "
         "SUM(CASE WHEN paid_on > due_on THEN 1 ELSE 0 END) AS paid_late FROM rind.billing_invoices "
         "WHERE status = 'paid' GROUP BY customer_no ORDER BY paid_late DESC LIMIT 5"),
    ],
    "Contacts": [
        ("Key contacts by role and status",
         "SELECT role, status, COUNT(*) AS contacts FROM rind.contacts GROUP BY role, status ORDER BY contacts DESC"),
        ("Which champions left, and where did they go?",
         "SELECT company_domain, full_name, title, changed_on, new_company FROM rind.contacts "
         "WHERE role = 'champion' AND status = 'left_company' ORDER BY changed_on DESC"),
        ("Which customers have no active champion?",
         "SELECT company_domain, SUM(CASE WHEN role = 'champion' AND status = 'active' THEN 1 ELSE 0 END) AS active_champions "
         "FROM rind.contacts GROUP BY company_domain HAVING SUM(CASE WHEN role = 'champion' AND status = 'active' THEN 1 ELSE 0 END) = 0"),
    ],
}

USL: list[tuple[str, str]] = [
    ("Watermelon candidates: green in the CRM, but red in billing or support (one query, three systems, three keys)",
     "SELECT account_name, health_score, arr_usd, overdue_invoices, open_high_tickets FROM rind.account_360 "
     "WHERE health_color = 'green' AND (overdue_invoices > 0 OR open_high_tickets > 0) ORDER BY arr_usd DESC"),
    ("Revenue exposure: how many accounts of each CRM colour have a red signal elsewhere?",
     "SELECT health_color, COUNT(*) AS accounts, SUM(arr_usd) AS arr_usd, "
     "SUM(CASE WHEN overdue_invoices > 0 OR open_high_tickets > 0 THEN 1 ELSE 0 END) AS with_red_signal_elsewhere "
     "FROM rind.account_360 GROUP BY health_color ORDER BY accounts DESC"),
    ("Four systems in one query: green accounts whose champion has left",
     "SELECT a.account_name, a.health_score, c.full_name AS departed_champion, c.new_company, x.overdue_invoices, x.open_high_tickets "
     "FROM rind.crm_accounts a JOIN rind.contacts c ON c.company_domain = a.domain "
     "JOIN rind.account_360 x ON x.account_id = a.account_id "
     "WHERE a.health_color = 'green' AND c.role = 'champion' AND c.status = 'left_company' ORDER BY a.arr_usd DESC"),
]


# ---------------------------------------------------------------- hand-coded comparison (bonus for 2a)

def handcoded_account_360(src: Path) -> dict[str, dict[str, Any]]:
    """What rind.account_360 takes without a semantic layer: load three exports, map three different
    customer keys by hand, aggregate, and left-join. Same output columns as the USL view."""
    load = lambda f: json.loads((src / f).read_text(encoding="utf-8"))["data"]  # noqa: E731
    crm, invoices, tickets = load("crm_accounts.json"), load("billing_invoices.json"), load("support_tickets.json")
    overdue: dict[str, int] = defaultdict(int)
    seen_customers = set()
    for i in invoices:
        seen_customers.add(i["customer_no"])
        overdue[i["customer_no"]] += 1 if i["status"] == "overdue" else 0
    open_high: dict[str, int] = defaultdict(int)
    seen_domains = set()
    for t in tickets:
        seen_domains.add(t["org_domain"])
        open_high[t["org_domain"]] += 1 if (t["status"] != "solved" and t["priority"] in ("high", "urgent")) else 0
    out = {}
    for a in crm:
        out[a["account_id"]] = {
            "account_id": a["account_id"], "account_name": a["account_name"], "arr_usd": a["arr_usd"],
            "health_score": a["health_score"], "health_color": a["health_color"],
            "overdue_invoices": overdue[a["billing_customer_no"]] if a["billing_customer_no"] in seen_customers else None,
            "open_high_tickets": open_high[a["domain"]] if a["domain"] in seen_domains else None,
        }
    return out


# ---------------------------------------------------------------- engine

def make_engine(sample: bool):
    os.environ["SAMPLE_MODE"] = "true" if sample else "false"
    from agent.data import LocalEngine, ZetarisEngine
    return LocalEngine() if sample else ZetarisEngine()


def cols_of(sql: str) -> list[str]:
    """Output column names of a SELECT (alias after AS, else the bare column), so rows map to names
    even when the engine returns lists instead of objects."""
    m = re.match(r"\s*SELECT\s+(.*?)\s+FROM\s", sql, re.S | re.I)
    if not m:
        return []
    parts, depth, cur = [], 0, ""
    for ch in m.group(1):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    out = []
    for p in parts:
        p = p.strip()
        a = re.search(r"\bAS\s+(\w+)\s*$", p, re.I)
        out.append(a.group(1) if a else p.split(".")[-1].strip())
    return out


def run_sql(engine, sql: str) -> dict[str, Any]:
    t = time.time()
    try:
        rows = engine.query(sql, cols_of(sql), 500)
        return {"ok": True, "rows": rows, "n": len(rows), "ms": int((time.time() - t) * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:400], "rows": [], "n": 0, "ms": int((time.time() - t) * 1000)}


def dq_statements() -> list[tuple[str, str]]:
    """(view name, CREATE statement) for each rule in scripts/zetaris_quality_rules.sql."""
    sql = (ROOT / "scripts" / "zetaris_quality_rules.sql").read_text(encoding="utf-8")
    sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    out = []
    for stmt in [x.strip() for x in re.split(r";\s*(?:\n|$)", sql) if x.strip()]:
        m = re.match(r"CREATE SCHEMASTORE VIEW (\w+) WITH CONTAINER rind AS", stmt)
        if m:
            out.append((m.group(1), stmt))
    return out


def md_table(rows: list[dict[str, Any]], limit: int = 8) -> str:
    if not rows:
        return "_(no rows)_\n"
    cols = list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:limit]:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    if len(rows) > limit:
        out.append(f"\n_…{len(rows) - limit} more rows_")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="run on the local DuckDB copy instead of Zetaris")
    a = ap.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    sample = a.sample
    engine = make_engine(sample)
    label = "local DuckDB copy (sample mode)" if sample else "Zetaris (live, REST API)"
    report: dict[str, Any] = {"engine": label, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    md = [f"# Zetaris Unified Semantic Layer report\n",
          f"Engine: **{label}** · generated {report['generated_at']} by `scripts/zetaris_usl_report.py`. "
          "Every result below was produced by running the SQL shown; nothing is typed in by hand.\n",
          "USL definition (DDL): `scripts/register_zetaris_sources.sql` (5 Lightning databases, 5 REST tables, "
          "5 SchemaStore views and the federated `rind.account_360` view, all in container `rind`) and "
          "`scripts/zetaris_quality_rules.sql` (the `rind.dq_*` data quality views, one per rule).\n"]

    # 1. sources
    print(f"Engine: {label}\n\n1. Sources")
    md.append("## 1. Sources onboarded and verified\n\n| System | USL view | Rows | Status |\n|---|---|---|---|")
    report["sources"] = []
    for system, view in SOURCES.items():
        r = run_sql(engine, f"SELECT COUNT(*) AS n FROM {view}")
        n = list(r["rows"][0].values())[0] if r["ok"] and r["rows"] else None
        status = "PASS" if r["ok"] and n and int(n) > 0 else "FAIL"
        print(f"  {status} {view}: {n} rows")
        md.append(f"| {system} | `{view}` | {n} | {status} |")
        report["sources"].append({"system": system, "view": view, "rows": n, "status": status, "error": r.get("error")})
    md.append("")

    # 2. exploratory queries
    print("\n2. Exploratory questions")
    md.append("## 2. Exploratory questions, 3 per source\n")
    report["exploratory"] = []
    for system, qs in EXPLORE.items():
        md.append(f"### {system}\n")
        for q, sql in qs:
            r = run_sql(engine, sql)
            print(f"  {'OK  ' if r['ok'] else 'FAIL'} [{system}] {q} -> {r['n']} rows, {r['ms']} ms"
                  + ("" if r["ok"] else f"\n       {str(r.get('error'))[:300]}"))
            md.append(f"**Q: {q}**\n\n```sql\n{sql}\n```\n")
            md.append(md_table(r["rows"]) if r["ok"] else f"Error: `{r['error']}`\n")
            report["exploratory"].append({"system": system, "question": q, "sql": sql, **{k: r[k] for k in ("ok", "n", "ms")},
                                          "rows": r["rows"][:20], "error": r.get("error")})

    # 3. USL in action
    print("\n3. The unified layer in action")
    md.append("## 3. The USL in action\n\nThese questions cross systems that use different customer keys "
              "(CRM `account_id`, billing `customer_no`, help-desk `org_domain`, directory `company_domain`). "
              "The USL joins them, so each answer is one query.\n")
    report["usl"] = []
    for q, sql in USL:
        r = run_sql(engine, sql)
        print(f"  {'OK  ' if r['ok'] else 'FAIL'} {q[:70]} -> {r['n']} rows, {r['ms']} ms"
              + ("" if r["ok"] else f"\n       {str(r.get('error'))[:300]}"))
        md.append(f"**Q: {q}**\n\n```sql\n{sql}\n```\n")
        md.append(md_table(r["rows"]) if r["ok"] else f"Error: `{r['error']}`\n")
        report["usl"].append({"question": q, "sql": sql, **{k: r[k] for k in ("ok", "n", "ms")}, "rows": r["rows"][:20],
                              "error": r.get("error")})

    # 4. data quality rules
    print("\n4. Data quality rules")
    rules, notes = [], []
    for view, stmt in dq_statements():
        create = (re.sub(r"CREATE SCHEMASTORE VIEW (\w+) WITH CONTAINER rind AS", r"CREATE OR REPLACE VIEW rind.\1 AS", stmt)
                  if sample else stmt)
        c = run_sql(engine, create)
        created = c["ok"] or "exist" in (c.get("error") or "").lower()
        if created:
            r = run_sql(engine, f"SELECT rule_name, rule_text, checked, violations FROM rind.{view}")
            how = f"view `rind.{view}`"
        else:  # the view could not be created: run the same rule SQL directly so it still executes
            r = run_sql(engine, re.sub(r"^.*?\bAS\s*\n", "", stmt, count=1, flags=re.S))
            how = "rule SQL run directly (view not created)"
            notes.append(f"{view}: {str(c.get('error'))[:200]}")
        if r["ok"] and r["rows"]:
            row = r["rows"][0]
            v = int(float(row.get("violations") or 0))
            rules.append({**row, "result": "PASS" if v == 0 else "FAIL", "ran_as": how})
            print(f"  {'PASS' if v == 0 else 'FAIL'} {row.get('rule_name')}: {v} violations in {row.get('checked')} rows ({how})")
        else:
            rules.append({"rule_name": view, "rule_text": "", "checked": None, "violations": None, "result": "ERROR", "ran_as": how})
            print(f"  ERROR {view}: {str(r.get('error'))[:300]}")
    md.append("## 4. Data quality rules\n\nDefined in the USL as one SchemaStore view per rule, `rind.dq_*` "
              "(`scripts/zetaris_quality_rules.sql`). Each view was created and then queried in this run.\n")
    md.append(md_table(rules, limit=20))
    if notes:
        md.append("Notes: " + "; ".join(f"`{n}`" for n in notes) + "\n")
    report["data_quality"] = {"rules": rules, "notes": notes}

    # 5. hand-coded comparison
    print("\n5. Hand-coded vs USL")
    hand = handcoded_account_360(ROOT / "data" / "sources")
    u = run_sql(engine, "SELECT account_id, overdue_invoices, open_high_tickets FROM rind.account_360")
    norm = lambda v: None if v in (None, "", "null") else int(float(v))  # noqa: E731
    match = sum(1 for row in u["rows"] if row["account_id"] in hand
                and norm(row["overdue_invoices"]) == hand[row["account_id"]]["overdue_invoices"]
                and norm(row["open_high_tickets"]) == hand[row["account_id"]]["open_high_tickets"])
    src_lines = len([l for l in inspect.getsource(handcoded_account_360).splitlines() if l.strip() and not l.strip().startswith(('"""', "#"))])
    view_sql = re.search(r"CREATE SCHEMASTORE VIEW account_360.*?;", (ROOT / "scripts" / "register_zetaris_sources.sql").read_text(), re.S)
    view_lines = len([l for l in (view_sql.group(0) if view_sql else "").splitlines() if l.strip()])
    print(f"  {match}/{len(hand)} accounts identical; hand-coded {src_lines} lines of Python vs {view_lines} lines of USL SQL")
    md.append("## 5. Bonus: hand-coded approach vs the USL\n\n"
              "`handcoded_account_360()` in this script rebuilds `rind.account_360` without a semantic layer: it loads three "
              "exports, maps three different customer keys by hand, aggregates and left-joins in Python.\n\n"
              f"| | Hand-coded Python | Zetaris USL view |\n|---|---|---|\n"
              f"| Lines of code | {src_lines} | {view_lines} (SQL) |\n"
              f"| Accounts with identical results | {match} of {len(hand)} | (reference) |\n"
              "| Reusable by other queries and tools | No: only this script can use it | Yes: any SQL client, the agent, the data quality rules |\n"
              "| Picks up new rows | Re-download and re-run | Next query reads the source |\n")
    report["handcoded_vs_usl"] = {"identical_accounts": match, "accounts": len(hand), "python_lines": src_lines, "usl_sql_lines": view_lines}

    ok_explore = sum(1 for e in report["exploratory"] if e["ok"])
    ok_usl = sum(1 for e in report["usl"] if e["ok"])
    passed = sum(1 for x in rules if x["result"] == "PASS")
    summary = (f"Sources: {sum(1 for s in report['sources'] if s['status'] == 'PASS')}/5 · exploratory queries: {ok_explore}/15 · "
               f"USL queries: {ok_usl}/3 · data quality rules passed: {passed}/{len(rules)} · hand-coded match: {match}/{len(hand)}")
    md.insert(3, f"**Summary:** {summary}\n")
    report["summary"] = summary
    (ROOT / "evidence").mkdir(exist_ok=True)
    name = "zetaris_usl_report_sample" if sample else "zetaris_usl_report"
    (ROOT / "evidence" / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (ROOT / "evidence" / f"{name}.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"\n{summary}\nSaved evidence/{name}.md")
    return 0 if ok_explore == 15 and ok_usl == 3 and rules and passed == len(rules) else 1  # noqa: E501


if __name__ == "__main__":
    raise SystemExit(main())
