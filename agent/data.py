"""Data layer. LIVE: every fact comes from Zetaris over its REST API.
SAMPLE_MODE: the same SQL runs on a local DuckDB copy of the same five exports,
so judges can run Rind without Zetaris, Nightshift or credentials."""
from __future__ import annotations

import json
import re
import tempfile
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import ROOT, SETTINGS
from .trace import Trace

SOURCES = {
    # view name            (system label,        local export file)
    "rind.crm_accounts":     ("CRM", "crm_accounts.json"),
    "rind.product_usage":    ("Product analytics", "product_usage.json"),
    "rind.support_tickets":  ("Support desk", "support_tickets.json"),
    "rind.billing_invoices": ("Billing", "billing_invoices.json"),
    "rind.contacts":         ("Contact directory", "contacts.json"),
}
NUMERIC = {"arr_usd", "seats_purchased", "health_score", "nps_last", "active_users", "sessions",
           "workflows_run", "amount_usd", "sentiment", "first_response_hours", "overdue_invoices",
           "open_high_tickets"}
SAFE = re.compile(r"^[A-Za-z0-9._@-]+$")


class DataError(RuntimeError):
    pass


def _q(value: str) -> str:
    if not SAFE.match(value):
        raise DataError(f"unsafe identifier value: {value!r}")
    return f"'{value}'"


def _norm(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, (date,)):
            v = v.isoformat()
        if k in NUMERIC and isinstance(v, str) and v not in ("", "null"):
            try:
                v = float(v) if "." in v else int(v)
            except ValueError:
                pass
        if v == "null":
            v = None
        out[k] = v
    return out


# ----------------------------------------------------------------------------- engines

class ZetarisEngine:
    name = "zetaris"

    def __init__(self) -> None:
        import requests  # imported here so sample mode needs no extra deps
        if not SETTINGS.zetaris_api_key:
            raise DataError("ZETARIS_API_KEY is not set. Create one in the Zetaris web UI and put it in .env")
        self._s = requests.Session()

    def query(self, sql: str, columns: list[str], limit: int = 5000) -> list[dict[str, Any]]:
        import requests
        body = {"queryId": str(uuid.uuid4()), "sql": sql, "source": "SqlEditor", "limit": limit}
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self._s.post(
                    f"{SETTINGS.zetaris_rest_url}/sql-editor/sqls/run",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {SETTINGS.zetaris_api_key}",
                        "X-Org-ID": SETTINGS.zetaris_org_id,
                        "X-Request-ID": str(uuid.uuid4()),
                        "Content-Type": "application/json",
                    },
                    timeout=SETTINGS.zetaris_timeout_s,
                )
                if r.status_code in (401, 403):
                    raise DataError(f"Zetaris rejected the API key (HTTP {r.status_code}). Create a new key in the web UI.")
                if r.status_code == 400:
                    raise DataError(f"Zetaris returned 400: {r.text[:300]}")
                r.raise_for_status()
                return self._rows(r.json(), columns)
            except DataError:
                raise
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(1.5 * (attempt + 1))
        raise DataError(f"Zetaris unreachable after 3 tries: {last}")

    @staticmethod
    def _rows(payload: Any, columns: list[str]) -> list[dict[str, Any]]:
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, dict):  # some versions nest rows/columns
            cols = data.get("columns") or data.get("schema") or columns
            data = data.get("rows") or data.get("data") or []
            if cols and isinstance(cols[0], dict):
                cols = [c.get("name") for c in cols]
            columns = list(cols)
        rows = []
        for row in data or []:
            if isinstance(row, dict):
                rows.append(_norm(row))
            else:
                rows.append(_norm(dict(zip(columns, row))))
        return rows


class LocalEngine:
    """DuckDB over the same JSON exports Zetaris serves. Same view names, same SQL."""
    name = "sample"

    def __init__(self) -> None:
        import duckdb
        import threading
        self._lock = threading.Lock()
        self.con = duckdb.connect()
        self.con.execute("CREATE SCHEMA rind")
        src = ROOT / "data" / "sources"
        tmp = Path(tempfile.mkdtemp())
        for view, (_, fname) in SOURCES.items():
            rows = json.loads((src / fname).read_text())["data"]
            nd = tmp / (fname + "l")
            nd.write_text("\n".join(json.dumps(r) for r in rows))
            self.con.execute(
                f"CREATE TABLE {view} AS SELECT * FROM read_json_auto('{nd.as_posix()}', format='newline_delimited', dateformat='%Y-%m-%d', timestampformat='%Y-%m-%d')"
            )
        sql = (ROOT / "scripts" / "register_zetaris_sources.sql").read_text()
        m = re.search(r"CREATE SCHEMASTORE VIEW account_360 WITH CONTAINER rind AS(.*?);", sql, re.S)
        if m:
            self.con.execute("CREATE VIEW rind.account_360 AS " + m.group(1))

    def query(self, sql: str, columns: list[str], limit: int = 5000) -> list[dict[str, Any]]:
        with self._lock:  # one DuckDB connection is not safe across threads
            cur = self.con.execute(sql)
            names = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit)
        return [_norm(dict(zip(names, r))) for r in rows]


# ----------------------------------------------------------------------------- facade

class DataLayer:
    def __init__(self, trace: Trace) -> None:
        self.trace = trace
        self.engine = LocalEngine() if SETTINGS.sample_mode else ZetarisEngine()
        self.as_of = date.fromisoformat(SETTINGS.as_of)
        self.queries = 0

    def sql(self, agent: str, view: str, sql: str, columns: list[str], purpose: str) -> list[dict[str, Any]]:
        t = time.time()
        try:
            rows = self.engine.query(sql, columns)
        except Exception as e:
            self.trace.log(agent, "sql_error", engine=self.engine.name, view=view, sql=sql, error=str(e)[:300])
            raise
        self.queries += 1
        self.trace.log(agent, "sql", engine=self.engine.name, system=SOURCES.get(view, ("Zetaris view",))[0],
                       view=view, sql=sql, rows=len(rows), ms=int((time.time() - t) * 1000), purpose=purpose)
        return rows

    def ping(self) -> bool:
        rows = self.engine.query("SELECT 1 AS one", ["one"], 1)
        return bool(rows) and str(list(rows[0].values())[0]) == "1"

    # ---- windows
    def window(self, weeks_back: int, year_offset: int = 0) -> str:
        d = self.as_of - timedelta(weeks=weeks_back) - timedelta(weeks=52 * year_offset)
        return d.isoformat()

    # ---- bulk pulls for the weekly screen (one query per system)
    def roster(self, agent: str) -> list[dict[str, Any]]:
        cols = ["account_id", "account_name", "domain", "billing_customer_no", "industry", "segment", "arr_usd",
                "seats_purchased", "renewal_date", "health_score", "health_color", "health_updated_on", "nps_last",
                "last_csm_touch", "csm_note"]
        return self.sql(agent, "rind.crm_accounts", f"SELECT {', '.join(cols)} FROM rind.crm_accounts", cols,
                        "Account roster with the CRM health score")

    def bulk(self, agent: str) -> dict[str, list[dict[str, Any]]]:
        start = self.window(16)
        out = {
            "usage": self.sql(agent, "rind.product_usage",
                              f"SELECT account_ref, week_start, active_users, seats_purchased, workflows_run FROM rind.product_usage WHERE week_start >= '{start}'",
                              ["account_ref", "week_start", "active_users", "seats_purchased", "workflows_run"],
                              "Weekly active users, last 16 weeks, all accounts"),
            "tickets": self.sql(agent, "rind.support_tickets",
                                "SELECT ticket_id, org_domain, opened_at, priority, status, subject, sentiment, solved_at FROM rind.support_tickets",
                                ["ticket_id", "org_domain", "opened_at", "priority", "status", "subject", "sentiment", "solved_at"],
                                "All support tickets"),
            "invoices": self.sql(agent, "rind.billing_invoices",
                                 "SELECT invoice_id, customer_no, issued_on, due_on, amount_usd, paid_on, status FROM rind.billing_invoices",
                                 ["invoice_id", "customer_no", "issued_on", "due_on", "amount_usd", "paid_on", "status"],
                                 "All invoices and payments"),
            "usage_last_year": self.sql(agent, "rind.product_usage",
                              f"SELECT account_ref, week_start, active_users, seats_purchased, workflows_run FROM rind.product_usage WHERE week_start >= '{self.window(16, 1)}' AND week_start < '{self.window(0, 1)}'",
                              ["account_ref", "week_start", "active_users", "seats_purchased", "workflows_run"],
                              "Same 16 weeks last year, all accounts (seasonality baseline)"),
            "contacts": self.sql(agent, "rind.contacts",
                                 "SELECT contact_id, company_domain, full_name, title, role, status, changed_on, new_company, last_engaged_on FROM rind.contacts",
                                 ["contact_id", "company_domain", "full_name", "title", "role", "status", "changed_on", "new_company", "last_engaged_on"],
                                 "All key contacts with job-change status"),
        }
        return out

    def account_360(self, agent: str) -> list[dict[str, Any]]:
        cols = ["account_id", "account_name", "arr_usd", "renewal_date", "health_score", "health_color",
                "overdue_invoices", "open_high_tickets"]
        return self.sql(agent, "rind.account_360", f"SELECT {', '.join(cols)} FROM rind.account_360", cols,
                        "Federated join across CRM, billing and help desk")

    def evidence_from_bulk(self, acct: dict[str, Any], bulk: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
        """Every check for one account, cut from the per-system Zetaris pulls (one query per system
        per run instead of five per account). Same shape and facts as check()."""
        ref, dom, cno = "acc" + acct["account_id"].split("-")[1], acct["domain"], acct["billing_customer_no"]
        u = sorted([r for r in bulk["usage"] if r["account_ref"] == ref], key=lambda r: str(r["week_start"]))
        ly = sorted([r for r in bulk.get("usage_last_year", []) if r["account_ref"] == ref], key=lambda r: str(r["week_start"]))
        t = sorted([r for r in bulk["tickets"] if r["org_domain"] == dom], key=lambda r: str(r["opened_at"]))
        i = sorted([r for r in bulk["invoices"] if r["customer_no"] == cno], key=lambda r: str(r["due_on"]))
        c = [r for r in bulk["contacts"] if r["company_domain"] == dom]
        ev = {
            "usage_trend": {"check": "usage_trend", "view": "rind.product_usage", "facts": usage_facts(u), "rows": u},
            "tickets": {"check": "tickets", "view": "rind.support_tickets", "facts": ticket_facts(t, self.as_of), "rows": t},
            "invoices": {"check": "invoices", "view": "rind.billing_invoices", "facts": invoice_facts(i, self.as_of), "rows": i},
            "contacts": {"check": "contacts", "view": "rind.contacts", "facts": contact_facts(c, self.as_of), "rows": c},
        }
        if ly:
            ev["usage_last_year"] = {"check": "usage_last_year", "view": "rind.product_usage", "facts": last_year_facts(ly), "rows": ly}
        return ev

    # ---- targeted checks the Investigator and Skeptic ask for
    def check(self, agent: str, check: str, acct: dict[str, Any]) -> dict[str, Any]:
        aid, ref = acct["account_id"], "acc" + acct["account_id"].split("-")[1]
        dom, cno = acct["domain"], acct["billing_customer_no"]
        if check == "usage_trend":
            cols = ["week_start", "active_users", "seats_purchased", "workflows_run"]
            rows = self.sql(agent, "rind.product_usage",
                            f"SELECT {', '.join(cols)} FROM rind.product_usage WHERE account_ref = {_q(ref)} AND week_start >= '{self.window(16)}' ORDER BY week_start",
                            cols, f"{aid}: usage trend, last 16 weeks")
            return {"check": check, "view": "rind.product_usage", "facts": usage_facts(rows), "rows": rows}
        if check == "usage_last_year":
            cols = ["week_start", "active_users", "seats_purchased", "workflows_run"]
            rows = self.sql(agent, "rind.product_usage",
                            f"SELECT {', '.join(cols)} FROM rind.product_usage WHERE account_ref = {_q(ref)} AND week_start >= '{self.window(16, 1)}' AND week_start < '{self.window(0, 1)}' ORDER BY week_start",
                            cols, f"{aid}: same 16 weeks last year (seasonality check)")
            return {"check": check, "view": "rind.product_usage", "facts": last_year_facts(rows), "rows": rows}
        if check == "tickets":
            cols = ["ticket_id", "opened_at", "priority", "status", "subject", "sentiment", "solved_at"]
            rows = self.sql(agent, "rind.support_tickets",
                            f"SELECT {', '.join(cols)} FROM rind.support_tickets WHERE org_domain = {_q(dom)} ORDER BY opened_at",
                            cols, f"{aid}: support tickets")
            return {"check": check, "view": "rind.support_tickets", "facts": ticket_facts(rows, self.as_of), "rows": rows}
        if check == "invoices":
            cols = ["invoice_id", "issued_on", "due_on", "amount_usd", "paid_on", "status"]
            rows = self.sql(agent, "rind.billing_invoices",
                            f"SELECT {', '.join(cols)} FROM rind.billing_invoices WHERE customer_no = {_q(cno)} ORDER BY due_on",
                            cols, f"{aid}: invoices and payment history")
            return {"check": check, "view": "rind.billing_invoices", "facts": invoice_facts(rows, self.as_of), "rows": rows}
        if check == "contacts":
            cols = ["full_name", "title", "role", "status", "changed_on", "new_company", "last_engaged_on"]
            rows = self.sql(agent, "rind.contacts",
                            f"SELECT {', '.join(cols)} FROM rind.contacts WHERE company_domain = {_q(dom)}",
                            cols, f"{aid}: key contacts and job changes")
            return {"check": check, "view": "rind.contacts", "facts": contact_facts(rows, self.as_of), "rows": rows}
        raise DataError(f"unknown check {check}")


CHECKS = {
    "usage_trend": "weekly active users for the last 16 weeks (detects a usage slide)",
    "usage_last_year": "the same 16 weeks last year (tells a real decline from a seasonal dip)",
    "tickets": "support tickets: open high/urgent issues, sentiment, whether they were solved",
    "invoices": "invoices: is the latest one overdue, and has this customer always paid late?",
    "contacts": "champion and exec sponsor: did anyone leave, was a replacement named?",
}


# ----------------------------------------------------------------------------- fact computations

def _avg(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def usage_facts(rows: list[dict[str, Any]], prefix: str = "") -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: str(r["week_start"]))
    au = [float(r["active_users"]) for r in rows]
    if len(au) < 8:
        return {prefix + "weeks": len(au)}
    recent, prior = _avg(au[-4:]), _avg(au[-12:-4])
    seats = float(rows[-1].get("seats_purchased") or 0) or 1.0
    return {
        prefix + "weeks": len(au),
        prefix + "avg_weekly_active_users_last4": round(recent, 1),
        prefix + "avg_weekly_active_users_prior8": round(prior, 1),
        prefix + "change_pct": round(100 * (recent - prior) / prior, 1) if prior else 0.0,
        prefix + "seat_utilization_pct": round(100 * recent / seats, 1),
    }


def last_year_facts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    f = usage_facts(rows, prefix="last_year_")
    if f.get("last_year_change_pct") is not None:
        f["seasonal_dip_last_year"] = f["last_year_change_pct"] <= -25  # a similar dip happened last year
    return f


def ticket_facts(rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    cutoff = (as_of - timedelta(days=30)).isoformat()
    recent = [r for r in rows if str(r["opened_at"]) >= cutoff]
    open_high = [r for r in rows if r["status"] != "solved" and r["priority"] in ("high", "urgent")]
    solved_urgent_recent = [r for r in recent if r["status"] == "solved" and r["priority"] in ("high", "urgent")]
    sents = [float(r["sentiment"]) for r in recent if r.get("sentiment") is not None]
    latest = max(rows, key=lambda r: str(r["opened_at"])) if rows else None
    return {
        "tickets_total": len(rows),
        "tickets_last30": len(recent),
        "open_high_or_urgent": len(open_high),
        "open_high_subjects": [r["subject"] for r in open_high][:4],
        "solved_urgent_last30": len(solved_urgent_recent),
        "avg_sentiment_last30": round(_avg(sents), 2) if sents else None,
        "latest_ticket_sentiment": latest.get("sentiment") if latest else None,
        "latest_ticket_subject": latest.get("subject") if latest else None,
        "urgent_issue_still_open": len(open_high) >= 1,
    }


def invoice_facts(rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: str(r["due_on"]))
    if not rows:
        return {"invoices": 0}
    latest = rows[-1]
    due = date.fromisoformat(str(latest["due_on"]))
    days_overdue = (as_of - due).days if not latest.get("paid_on") and as_of > due else 0
    history = []
    for r in rows[:-1]:
        if r.get("paid_on"):
            history.append((date.fromisoformat(str(r["paid_on"])) - date.fromisoformat(str(r["due_on"]))).days)
    return {
        "invoices": len(rows),
        "latest_invoice": latest["invoice_id"],
        "latest_status": latest["status"],
        "latest_amount_usd": latest["amount_usd"],
        "latest_days_overdue": days_overdue,
        "past_avg_days_late": round(_avg(history), 1) if history else None,
        "past_invoices_paid_late": sum(1 for d in history if d > 5),
        "past_invoices": len(history),
        # Derived so nobody has to do arithmetic: lateness is only a signal if it is new behaviour.
        "habitual_late_payer": bool(history) and _avg(history) >= 25 and sum(1 for d in history if d > 5) >= 2,
        "overdue_is_new_behaviour": days_overdue > 14 and not (bool(history) and _avg(history) >= 25),
    }


def contact_facts(rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    champs = [r for r in rows if r["role"] == "champion"]
    left = [r for r in champs if r["status"] == "left_company"]
    active = [r for r in champs if r["status"] == "active"]
    sponsor = next((r for r in rows if r["role"] == "exec_sponsor"), None)
    out: dict[str, Any] = {
        "champion_left": bool(left),
        "active_champion": active[0]["full_name"] if active else None,
        "active_champion_title": active[0]["title"] if active else None,
        "replacement_champion_named": bool(left and active),
    }
    if left:
        out.update({"departed_champion": left[0]["full_name"], "departed_title": left[0]["title"],
                    "departed_on": left[0]["changed_on"], "departed_to": left[0]["new_company"],
                    "days_since_departure": (as_of - date.fromisoformat(str(left[0]["changed_on"]))).days})
    if active:
        out["active_champion_last_engaged"] = active[0]["last_engaged_on"]
    if sponsor:
        out["exec_sponsor"] = sponsor["full_name"]
        out["exec_sponsor_title"] = sponsor["title"]
        out["exec_sponsor_days_since_engaged"] = (as_of - date.fromisoformat(str(sponsor["last_engaged_on"]))).days
    admin = next((r for r in rows if r["role"] == "admin"), None)
    if admin:
        out["admin_contact"] = admin["full_name"]
    return out


def snapshot(roster: list[dict[str, Any]], bulk: dict[str, list[dict[str, Any]]], as_of: date) -> list[dict[str, Any]]:
    """Compact one-row-per-account table the Planner reasons over."""
    by_ref: dict[str, list] = {}
    for r in bulk["usage"]:
        by_ref.setdefault(r["account_ref"], []).append(r)
    by_dom_t: dict[str, list] = {}
    for r in bulk["tickets"]:
        by_dom_t.setdefault(r["org_domain"], []).append(r)
    by_cno: dict[str, list] = {}
    for r in bulk["invoices"]:
        by_cno.setdefault(r["customer_no"], []).append(r)
    by_dom_c: dict[str, list] = {}
    for r in bulk["contacts"]:
        by_dom_c.setdefault(r["company_domain"], []).append(r)
    out = []
    for a in roster:
        ref = "acc" + a["account_id"].split("-")[1]
        u = usage_facts(by_ref.get(ref, []))
        t = ticket_facts(by_dom_t.get(a["domain"], []), as_of)
        i = invoice_facts(by_cno.get(a["billing_customer_no"], []), as_of)
        c = contact_facts(by_dom_c.get(a["domain"], []), as_of)
        out.append({
            "account_id": a["account_id"], "account_name": a["account_name"], "industry": a["industry"],
            "arr_usd": a["arr_usd"], "renewal_in_days": (date.fromisoformat(str(a["renewal_date"])) - as_of).days,
            "crm_health": a["health_score"], "crm_color": a["health_color"],
            "usage_change_pct": u.get("change_pct"), "seat_util_pct": u.get("seat_utilization_pct"),
            "open_high_tickets": t["open_high_or_urgent"], "sentiment_30d": t["avg_sentiment_last30"],
            "invoice_days_overdue": i.get("latest_days_overdue"), "champion_left": c["champion_left"],
        })
        ws = []
        if (u.get("change_pct") or 0) <= -20:
            ws.append("usage_decline")
        if c["champion_left"]:
            ws.append("champion_left")
        if (i.get("latest_days_overdue") or 0) > 14:
            ws.append("invoice_overdue")
        if t["open_high_or_urgent"] >= 1 or t["solved_urgent_last30"] >= 1:
            ws.append("escalating_tickets")
        out[-1]["warning_signs"] = "|".join(ws) or "none"
    return out
