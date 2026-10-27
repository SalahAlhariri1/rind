"""Rind: weekly churn triage for a customer success manager.   streamlit run app.py

Look: a CRM record page with Rind as a side panel. The left card is what the CSM already
sees in their CRM; the right card is what Rind adds (verdict, reasons, action, email).
"""
from __future__ import annotations

import glob
import html
import json
from pathlib import Path

import streamlit as st

from agent.config import ROOT, SETTINGS

st.set_page_config(page_title="Rind", page_icon="●", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
:root{
  --bg:#EEF1F5; --card:#FFFFFF; --line:#DDE3EA; --soft:#EEF1F5; --ink:#1F2933; --muted:#66788A; --faint:#9AA8B6;
  --red:#B42318; --red-bg:#FDECEA; --amber:#B54708; --amber-bg:#FEF0C7; --green:#2F8A57; --green-bg:#E3F4EA;
}
html,body,.stApp,p,li,label,button,input,textarea,.stMarkdown{font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
.stApp{background:var(--bg);color:var(--ink)}
.stApp *:not(code):not(pre):not(code *):not([data-testid="stIconMaterial"]):not(.material-symbols-rounded){font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif !important}
.stMarkdown p.intro,.intro{font-size:14.5px !important;line-height:1.55;margin-bottom:6px}
.stMarkdown .li,.stMarkdown .dis,.stMarkdown .mem{font-size:13.5px}
header[data-testid="stHeader"]{background:transparent;height:0}
[data-testid="stToolbar"],[data-testid="stDecoration"],#MainMenu,section[data-testid="stSidebar"],[data-testid="collapsedControl"]{display:none !important}
.block-container{padding-top:1.4rem;padding-bottom:2rem;max-width:1360px}
p{margin:0}

/* cards = bordered containers */
.st-key-accounts,.st-key-record,.st-key-panel,.st-key-plain{background:var(--card);border:1px solid var(--line) !important;border-radius:8px}
.st-key-accounts,.st-key-record,.st-key-panel,.st-key-plain{padding:0 !important;gap:0 !important}

/* top bar */
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.top .brand{font-weight:600;font-size:17px}
.top .ctx{color:var(--muted);font-size:13.5px}
.stats{display:flex;gap:6px 28px;flex-wrap:wrap;margin-top:10px}
.stats div{font-size:13px;color:var(--muted)}
.stats b{color:var(--ink);font-weight:600;font-size:15px;margin-right:5px;font-variant-numeric:tabular-nums}
.stats b.r{color:var(--red)}

/* nav */
div[role="radiogroup"]{gap:4px !important;margin:14px 0 4px}
div[role="radiogroup"] label{padding:6px 12px !important;border-radius:6px;margin:0 !important;font-size:13.5px;color:var(--muted)}
div[role="radiogroup"] label:has(input:checked){background:var(--card);color:var(--ink);box-shadow:0 0 0 1px var(--line)}
div[role="radiogroup"] label > div > div:first-child:not([data-testid]){display:none !important}
div[role="radiogroup"] label p{font-weight:500}

/* card pieces */
.hd{padding:14px 18px;border-bottom:1px solid var(--soft);display:flex;justify-content:space-between;align-items:center;gap:10px}
.hd h1{font-size:19px;font-weight:600;margin:0;padding:0;line-height:1.3;color:var(--ink)}
.hd .s{color:var(--muted);font-size:13px;margin-top:2px}
.hd b.t{font-size:14px;font-weight:600}
.body{padding:14px 18px}
.lab{font-size:12px;color:var(--muted);font-weight:500;margin:0 0 8px}
.fields{display:grid;grid-template-columns:1fr 1fr;gap:14px 24px;padding:16px 18px;border-bottom:1px solid var(--soft)}
.fields span{display:block;color:var(--muted);font-size:12px}
.fields b{font-weight:500;font-size:14px}
.c-green{color:var(--green)} .c-red{color:var(--red)} .c-amber{color:var(--amber)}

.badge{font-weight:600;font-size:12px;padding:3px 10px;border-radius:99px;white-space:nowrap}
.badge.high{background:var(--red-bg);color:var(--red)} .badge.medium{background:var(--amber-bg);color:var(--amber)}
.badge.none,.badge.low{background:var(--green-bg);color:var(--green)}

.intro{font-size:14px;line-height:1.55;margin-bottom:6px}
.li{display:flex;gap:10px;padding:10px 0;border-top:1px solid var(--soft);font-size:14px;line-height:1.5}
.li i{flex:none;width:6px;height:6px;border-radius:50%;background:var(--red);margin-top:8px}
.li.medium i{background:var(--amber)} .li.ok i{background:var(--faint)}
.li .src{color:var(--muted);font-size:12px;display:block;margin-top:2px}
.dis{font-size:13px;color:var(--muted);line-height:1.5;padding:8px 0;border-top:1px solid var(--soft)}
.dis s{text-decoration-color:var(--faint)}
.mem{font-size:13px;line-height:1.5;background:var(--soft);border-radius:6px;padding:8px 10px;margin-top:8px}
.mem b{font-weight:600}
.act{border:1px solid var(--line);border-radius:6px;padding:10px 12px;margin-top:12px}
.act .p{font-weight:600;font-size:14.5px}
.act .r{font-size:13px;color:var(--muted);margin-top:2px;line-height:1.45}
.act .e{font-size:12px;color:var(--red);margin-top:6px;font-weight:500}
.foot{color:var(--muted);font-size:12px;padding:10px 18px;border-top:1px solid var(--soft)}

/* timeline */
.tl{padding:4px 18px 10px}
.tl .ln{display:grid;grid-template-columns:52px 92px minmax(0,1fr);gap:10px;padding:8px 0;border-top:1px solid var(--soft);font-size:13px;line-height:1.45}
.tl .ln:first-child{border-top:0}
.tl .t{color:var(--faint);font-variant-numeric:tabular-nums}
.tl .a{font-weight:500}
.tl .a.ai{color:#5B4BC4} .tl .a.rule{color:var(--amber)} .tl .a.data{color:var(--green)}
.tl .d{color:var(--muted);font-size:12.5px;white-space:pre-wrap;word-break:break-word;margin-top:2px}
.key{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);padding:16px 18px 6px}
.key .ai{color:#5B4BC4} .key .rule{color:var(--amber)} .key .data{color:var(--green)}
@media (max-width:640px){.tl .ln{grid-template-columns:44px minmax(0,1fr)}.tl .a,.tl .w{grid-column:2}.fields{grid-template-columns:1fr}}

/* account list rows (buttons) */
.st-key-accounts .stButton{margin:0}
.st-key-accounts .stButton button{width:100%;justify-content:flex-start;text-align:left;border:0;border-radius:0;background:transparent;
  border-top:1px solid var(--soft);padding:10px 16px;min-height:0;color:var(--ink);box-shadow:none}
.st-key-accounts .stButton button > div,.st-key-accounts .stButton button [data-testid="stMarkdownContainer"]{justify-content:flex-start;text-align:left;width:100%}
.st-key-accounts .stButton button:hover{background:#F6F8FA;color:var(--ink)}
.st-key-accounts .stButton button p{font-size:12.5px !important;text-align:left;line-height:1.45}
.st-key-accounts .stButton button p strong{font-size:13.5px}
.st-key-accounts [class*="st-key-sel_"] button{background:#F2F5F8;box-shadow:inset 3px 0 0 var(--ink)}
.st-key-accounts .stButton button p strong::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--faint);margin-right:9px;vertical-align:2px}
.st-key-accounts [class*="_high_"] button p strong::before{background:var(--red)}
.st-key-accounts [class*="_medium_"] button p strong::before{background:var(--amber)}
.st-key-accounts .stButton button p{color:var(--muted)} .st-key-accounts .stButton button p strong{color:var(--ink);font-weight:500}
.st-key-plain{padding:0 0 16px !important}
.st-key-plainbody{padding:0 18px}
.st-key-accounts .stButton button:focus:not(:active){border-color:transparent;color:var(--ink)}

/* panel buttons */
.st-key-panel .stButton,.st-key-panel [data-testid="stPopover"]{padding:0}
.st-key-panel [data-testid="stHorizontalBlock"]{padding:12px 18px 12px}
.st-key-panel,.st-key-record{padding-bottom:2px !important}
.stButton button,[data-testid="stPopover"] button{border-radius:6px;font-weight:500;font-size:13.5px}
.stButton button[kind="primary"]{background:var(--ink);border-color:var(--ink)}
.stButton button[kind="primary"]:hover{background:#000;border-color:#000}
textarea{font-size:13.5px !important;line-height:1.5 !important}
[data-testid="stExpander"] details{border:0;border-top:1px solid var(--soft);border-radius:0}
[data-testid="stExpander"] summary{padding:10px 18px}
[data-testid="stExpander"] summary p{font-size:13px;color:var(--muted)}
.lede,.stMarkdown p.lede{color:var(--muted);font-size:13.5px !important;margin:6px 0 12px;line-height:1.5}
</style>
""", unsafe_allow_html=True)

E = html.escape
PLAY = {"exec_sponsor_meeting": "Book an exec sponsor meeting", "new_champion_onboarding": "Onboard a new champion",
        "adoption_workshop": "Run an adoption workshop", "support_escalation": "Escalate the open support issues",
        "billing_outreach": "Reach out about billing", "renewal_risk_review": "Hold a renewal risk review"}
PLAY_SHORT = {"exec_sponsor_meeting": "Book exec meeting", "new_champion_onboarding": "Plan onboarding",
              "adoption_workshop": "Plan workshop", "support_escalation": "Escalate tickets",
              "billing_outreach": "Contact billing", "renewal_risk_review": "Schedule review"}
SIGNAL = {"usage_decline": "usage down", "champion_left": "champion left", "invoice_overdue": "invoice late",
          "escalating_tickets": "urgent tickets", "other": "other"}
SOURCE = {"rind.product_usage": "Product usage", "rind.support_tickets": "Support desk",
          "rind.billing_invoices": "Billing", "rind.contacts": "Contacts", "rind.crm_accounts": "CRM"}
RISK_LABEL = {"high": "High churn risk", "medium": "Medium churn risk", "none": "No action needed", "low": "No action needed"}


def money(x) -> str:
    try:
        return f"${int(x):,}"
    except (TypeError, ValueError):
        return str(x)


@st.cache_data(show_spinner=False)
def _read_json(path: str, mtime: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@st.cache_data(show_spinner=False)
def _read_trace(path: str, mtime: float) -> list[dict]:
    p = Path(path)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def latest() -> dict | None:
    files = sorted(glob.glob(str(ROOT / "logs" / "result_*.json")))
    if not files and (ROOT / "evidence" / "live_run_result.json").exists():
        files = [str(ROOT / "evidence" / "live_run_result.json")]
    return _read_json(files[-1], Path(files[-1]).stat().st_mtime) if files else None


def trace_for(result: dict) -> list[dict]:
    p = ROOT / str(result.get("trace_file", ""))
    if not p.exists() or p.is_dir():
        p = ROOT / "evidence" / "live_run_trace.jsonl"
    return _read_trace(str(p), p.stat().st_mtime) if p.exists() else []


# ----------------------------------------------------------------------------- top bar
top_l, top_r = st.columns([5, 1], vertical_alignment="center")
with top_l:
    mode = "sample data" if SETTINGS.sample_mode else "live data"
    st.markdown(f'<div class="top"><span class="brand">Rind</span>'
                f'<span class="ctx">Churn signals for Dana Reyes · 60 accounts · week of {E(SETTINGS.as_of)} · {mode}</span></div>',
                unsafe_allow_html=True)
with top_r:
    run = st.button("Run triage", type="primary", width="stretch")

if run:
    from agent.orchestrator import run_weekly
    with st.status("Running this week's triage…", expanded=True) as status:
        st.write("Reading CRM, product usage, support, billing and contacts through Zetaris")
        out = run_weekly(progress=lambda s: st.write(s))
        status.update(label=f"Done. {out['summary']['at_risk']} accounts need attention.", state="complete")
    st.cache_data.clear()
    st.session_state.pop("sel", None)

result = latest()
if not result:
    st.markdown('<p class="lede">No triage yet. Press <b>Run triage</b>. Rind reads your five systems through Zetaris, '
                'investigates accounts that look off, rules out false alarms, and lists the ones that need you.</p>',
                unsafe_allow_html=True)
    st.stop()

S = result["summary"]
accounts = result["accounts"]
order = {"high": 0, "medium": 1}
queue = sorted([a for a in accounts if a["risk"] in ("high", "medium")], key=lambda a: (order[a["risk"]], -a["arr_usd"]))
cleared = [a for a in accounts if a["risk"] not in ("high", "medium")]
trace = trace_for(result)

st.markdown(
    '<div class="stats">'
    f'<div><b class="r">{len(queue)}</b>need attention</div>'
    f'<div><b>{S.get("watermelons", 0)}</b>of those look green in the CRM</div>'
    f'<div><b>{money(S["arr_at_risk_usd"])}</b>yearly revenue at risk</div>'
    f'<div><b>{S["false_alarms_stopped"]}</b>false alarms ruled out</div>'
    '</div>', unsafe_allow_html=True)
if S.get("degraded"):
    st.warning("Some AI steps failed in this run and used backup rules. Re-run before acting on it.")

tab = st.radio("View", ["Needs attention", "Ruled out", "Run log", "Scorecard"], horizontal=True,
               label_visibility="collapsed", key="tab")


# ----------------------------------------------------------------------------- pieces
def timeline(aid: str) -> str:
    lines = []
    for e in [e for e in trace if e.get("account_id") == aid and e["event"] != "llm_call"]:
        ag, ev = e["agent"], e["event"]
        kind = "rule" if ag in ("Verifier", "Escalation") else "data" if (ag == "Memory" or ev == "evidence") else "ai"
        w, d = ev.replace("_", " ").capitalize(), ""
        if ev == "evidence":
            w, d = "Pulled records through Zetaris", ", ".join(e.get("checks") or [])
        elif ev == "recall":
            w, d = "Recalled notes from earlier weeks", " | ".join(e.get("texts") or [])
        elif ev == "claims":
            w = "Revised the draft" if e.get("revision") else "Drafted the case"
            d = "\n".join(f"· {c.get('statement')}" for c in e.get("claims", []))
        elif ev == "claims_rejected":
            w, d = "Rejected claims the data doesn't support", "\n".join(f"· {r['claim']}  ({r['reason']})" for r in e.get("rejected", []))
        elif ev == "missed_signals":
            w, d = "Sent the draft back: it missed a problem", ", ".join(SIGNAL.get(m["signal"], m["signal"]) for m in e.get("missed", []))
        elif ev == "send_back":
            w, d = "Sent the draft back", "\n".join(e.get("notes") or e.get("requests") or [])
        elif ev == "review":
            w, d = "Challenged each claim", "\n".join(f"· {v.get('claim_id')} {v.get('verdict')}: {v.get('reason') or ''}" for v in e.get("verdicts", []))
        elif ev == "skeptic_overridden":
            w, d = "Overruled the Skeptic", "\n".join(f"· {SIGNAL.get(o['signal'], o['signal'])}: {o['skeptic']} → {o['verifier']} ({o['reason']})" for o in e.get("overrides", []))
        elif ev == "claim_added":
            w, d = "Added a problem the draft missed", e.get("claim", {}).get("statement", "")
        elif ev == "decision":
            w, d = f"Set risk to {e.get('risk')}{' and escalated' if e.get('escalate') else ''}", e.get("rule", "")
        elif ev == "brief":
            w, d = "Wrote the summary and email", PLAY.get(e.get("play"), e.get("play") or "")
        elif ev == "play_changed":
            w, d = "Changed the plan because of memory", e.get("reason", "")
        elif ev == "remember":
            w, d = "Saved this week's verdict to memory", f"risk {e.get('risk')}"
        elif ev == "feedback_helpful":
            w, d = "Marked a memory as useful", e.get("text", "")
        elif ev == "reuse_unchanged":
            w = "Nothing changed since last week, so the verdict was reused"
        lines.append(f'<div class="ln"><span class="t">{e.get("t_s", 0):.1f}s</span><span class="a {kind}">{E(ag)}</span>'
                     f'<div><div class="w">{E(w)}</div>' + (f'<div class="d">{E(d)}</div>' if d else "") + "</div></div>")
    return "".join(lines)


def record_card(a: dict) -> None:
    brief = a.get("brief") or {}
    contact = (brief.get("draft_email") or {}).get("to") or "—"
    col = {"green": "c-green", "red": "c-red", "amber": "c-amber", "yellow": "c-amber"}.get(a["crm_color"], "")
    with st.container(key="record"):
        st.markdown(
            f'<div class="hd"><div><h1>{E(a["account_name"])}</h1><div class="s">Account · {E(a["account_id"])} · Owner: Dana Reyes</div></div></div>'
            '<div class="fields">'
            f'<div><span>Health score (CRM)</span><b class="{col}">{a["crm_health"]} · {E(a["crm_color"].capitalize())}</b></div>'
            f'<div><span>Annual revenue</span><b>{money(a["arr_usd"])}</b></div>'
            f'<div><span>Renewal date</span><b>{E(str(a["renewal_date"]))} ({a["renewal_in_days"]} days)</b></div>'
            f'<div><span>Main contact</span><b>{E(contact)}</b></div></div>', unsafe_allow_html=True)
        tl = timeline(a["account_id"])
        if tl:
            calls = [e for e in trace if e.get("account_id") == a["account_id"] and e["event"] == "llm_call"]
            models = ", ".join(sorted({str(c.get("model_used")) for c in calls})) or "none"
            st.markdown('<div class="key"><span style="color:var(--ink);font-weight:500">How Rind decided</span>'
                        '<span class="ai">● AI agent</span><span class="rule">● rule check</span><span class="data">● data / memory</span></div>'
                        f'<div class="tl">{tl}</div>'
                        f'<div class="foot">{len(calls)} model calls · served by {E(models)}</div>', unsafe_allow_html=True)
        with st.expander("Raw data from Zetaris"):
            for k, ev in (a.get("evidence") or {}).items():
                st.markdown(f"**{SOURCE.get(ev.get('view'), ev.get('view'))}** · `{k}`")
                st.json(ev.get("facts", {}), expanded=False)


def panel_card(a: dict) -> None:
    risk, brief = a["risk"], a.get("brief") or {}
    supported, dropped = a.get("supported_claims", []), a.get("dropped_claims", [])
    flagged = risk in ("high", "medium")
    with st.container(key="panel"):
        parts = [f'<div class="hd"><b class="t">Rind</b><span class="badge {E(risk)}">{RISK_LABEL.get(risk, risk)}</span></div><div class="body">']
        n = len(supported)
        if flagged and a["crm_color"] == "green":
            parts.append(f'<p class="intro">The health score looks fine, but {"one thing" if n == 1 else f"{n} things"} changed:</p>')
        elif flagged:
            parts.append(f'<p class="intro">The CRM already shows concern. Rind confirmed {"one problem" if n == 1 else f"{n} problems"}:</p>')
        else:
            parts.append('<p class="intro">Something here looked like a warning sign. Rind checked it and found an innocent explanation.</p>')
        for c in supported:
            src = SOURCE.get((c.get("evidence") or [{}])[0].get("view", ""), "Zetaris")
            extra = " · added by the Verifier" if c.get("added_by") == "Verifier" else ""
            parts.append(f'<div class="li {E(risk)}"><i></i><div>{E(c["statement"])}<span class="src">{E(src)}{extra}</span></div></div>')
        for d in dropped:
            reason = (d.get("reason") or "").replace("Verifier: ", "")
            if flagged:
                parts.append(f'<div class="dis">Checked and ruled out: <s>{E(d["claim"])}</s> {E(reason)}</div>')
            else:
                parts.append(f'<div class="li ok"><i></i><div><s>{E(d["claim"])}</s><span class="src">{E(reason)}</span></div></div>')
        for m in a.get("memory_recalled", []):
            parts.append(f'<div class="mem"><b>From memory:</b> {E(m)}</div>')
        if brief.get("memory_used"):
            parts.append(f'<div class="mem"><b>Plan changed because of memory:</b> {E(brief["memory_used"])}</div>')
        if flagged and brief:
            esc = a.get("escalation", {})
            parts.append(f'<div class="act"><div class="p">{E(PLAY.get(brief.get("recommended_play"), brief.get("recommended_play") or ""))}</div>'
                         f'<div class="r">{E(brief.get("play_rationale") or "")}</div>'
                         + (f'<div class="e">Escalate to {E(esc.get("to", ""))}</div>' if esc.get("escalate") else "") + "</div>")
        parts.append("</div>")
        st.markdown("".join(parts), unsafe_allow_html=True)

        em = brief.get("draft_email") or {}
        if flagged and (em or brief):
            b1, b2 = st.columns(2)
            done = st.session_state.get("done", set())
            with b1:
                label = "✓ Marked as handled" if a["account_id"] in done else PLAY_SHORT.get(brief.get("recommended_play"), "Mark as handled")
                if st.button(label, type="primary", width="stretch", key=f"do_{a['account_id']}"):
                    done = set(done) ^ {a["account_id"]}
                    st.session_state.done = done
                    st.rerun()
            with b2:
                if em:
                    with st.popover("Draft email", width="stretch"):
                        st.text_area("Edit before sending", f"To: {em.get('to')}\nSubject: {em.get('subject')}\n\n{em.get('body')}",
                                     height=280, key=f"email_{a['account_id']}")
        sources = sorted({SOURCE.get(e.get("view"), "") for c in supported for e in (c.get("evidence") or [])} - {""})
        when = f"run {E(str(result.get('run_id', ''))[:8])}"
        st.markdown(f'<div class="foot">{"Verified against " + E(" & ".join(sources).lower()) if sources else "Checked against all five systems"} · {when}</div>',
                    unsafe_allow_html=True)


def account_list(items: list[dict], title: str, prefix: str) -> None:
    with st.container(key="accounts"):
        st.markdown(f'<div class="hd"><b class="t">{E(title)}</b><span class="s" style="color:var(--muted);font-size:12px">{len(items)}</span></div>',
                    unsafe_allow_html=True)
        for a in items:
            sig = ", ".join(SIGNAL.get(c["signal"], c["signal"]) for c in a.get("supported_claims", []))
            if not sig:
                gone = [SIGNAL.get(d.get("signal"), "") for d in a.get("dropped_claims", []) if d.get("signal")]
                sig = (", ".join(dict.fromkeys(g for g in gone if g)) + " · explained") if any(gone) else "explained"
            label = f"**{a['account_name']}**  \n{money(a['arr_usd'])} · {sig}"
            key = f"{'sel' if a['account_id'] == st.session_state.sel else 'row'}_{a['risk']}_{prefix}_{a['account_id']}"
            if st.button(label, key=key, width="stretch"):
                st.session_state.sel = a["account_id"]
                st.rerun()


def workspace(items: list[dict], title: str, empty: str, prefix: str) -> None:
    if not items:
        st.info(empty)
        return
    ids = [a["account_id"] for a in items]
    if st.session_state.get("sel") not in ids:
        st.session_state.sel = ids[0]
    a = next(x for x in items if x["account_id"] == st.session_state.sel)
    c1, c2, c3 = st.columns([1.05, 2.1, 1.55], gap="small")
    with c1:
        account_list(items, title, prefix)
    with c2:
        record_card(a)
    with c3:
        panel_card(a)


if tab == "Needs attention":
    workspace(queue, "Needs attention", "Nothing needs you this week.", "q")
elif tab == "Ruled out":
    st.markdown('<p class="lede">These accounts tripped a warning sign. Rind looked into each one and found the innocent explanation, '
                "so you don't have to chase them.</p>", unsafe_allow_html=True)
    workspace(cleared, "Ruled out", "No false alarms this week.", "c")
elif tab == "Run log":
    with st.container(key="plain"):
        st.markdown('<div class="hd"><b class="t">Run log</b></div>', unsafe_allow_html=True)
        with st.container(key="plainbody"):
            st.markdown('<p class="lede">Everything this run did: the SQL sent to Zetaris, which model answered each AI call, '
                        "and every step.</p>", unsafe_allow_html=True)
            st.markdown("**Models that served this run** (reported by Nightshift): "
                        + (", ".join(f"`{m}` × {n}" for m, n in result.get("model_usage", {}).items()) or "none"))
            for e in [e for e in trace if e["event"] == "sql"]:
                st.markdown(f"**{E(e.get('system', ''))}** · {e.get('rows')} rows · {e.get('ms')} ms · {E(e.get('purpose', ''))}")
                st.code(e.get("sql", ""), language="sql")
            who = st.multiselect("Filter by agent", sorted({e["agent"] for e in trace}), placeholder="All agents")
            st.dataframe([{"t (s)": e.get("t_s"), "agent": e["agent"], "event": e["event"], "account": e.get("account_id", ""),
                           "model / detail": str(e.get("model_used") or e.get("rule") or e.get("risk") or e.get("purpose") or "")[:120],
                           "ms": e.get("latency_ms") or e.get("ms")} for e in trace if not who or e["agent"] in who],
                         hide_index=True, height=460, width="stretch")
else:
    with st.container(key="plain"):
        st.markdown('<div class="hd"><b class="t">Scorecard</b></div><p class="lede" style="padding:12px 18px 0">The test data hides an answer key: '
                    "13 accounts truly at risk (8 behind a green CRM score) and 12 traps built to fool simple rules.</p>",
                    unsafe_allow_html=True)
        md = ROOT / "evidence" / "eval_results.md"
        with st.container(key="plainbody"):
            st.markdown(md.read_text(encoding="utf-8") if md.exists() else "Run `python eval/run_eval.py` to score Rind against the answer key.")
