# Rind

**Rind finds the customers who look healthy in your CRM but are about to churn, and shows exactly why.**

Open Agent Hackathon 2026 · Track 02: The Agent That Can Explain Why

## The user and the problem

A customer success manager (CSM) at a B2B software company looks after about 60 accounts.
Every Monday they decide which customers need saving this week. The CRM gives each account
a health score, but the score is recalculated monthly from logins and old survey answers,
so it lags reality. Customer success teams call the misses **watermelon accounts**: green
outside, red inside. The real warning signs are spread across five systems (product usage,
support desk, billing, contacts, CRM), and nobody has time to check all five for 60 accounts
every week.

## What Rind does

One click runs the weekly triage. Every step is logged, and every claim cites the fact it came from.

1. **Data (Zetaris).** Seven SQL queries per run pull the roster and all five systems through
   Zetaris. Rind turns them into per-account facts, including the facts that explain away false alarms:
   *was there the same dip last year?*, *does this customer always pay late?*, *was the champion
   already replaced?*, *is the urgent ticket still open?*
2. **Planner (Nemotron)** picks the accounts worth a deep look, using the snapshot and what
   memory says about earlier weeks.
3. **Investigator (Nemotron)** writes claims about each account. Each claim names one of four churn
   signals (usage decline, champion left, invoice overdue, escalating tickets) and cites the exact fact and value.
4. **Verifier (deterministic rules)** checks every claim against the Zetaris data. A claim is
   rejected if a cited number doesn't match, a fact belongs to another account, or the data doesn't
   show the signal. If the data shows a problem the Investigator missed, the Verifier sends the draft back;
   if it is still missed after revision, the Verifier adds it as a claim labelled "added by Verifier".
5. **Skeptic (Nemotron)** argues against every surviving claim. The Verifier checks each ruling
   against the data and overrules (and logs) any ruling the data contradicts.
   Up to two revision rounds.
6. **Risk and escalation (deterministic):** two or more independent signals = high, one = medium.
   High risk with ARR ≥ $100k or renewal ≤ 60 days escalates to the CS Director and exec sponsor.
7. **Reporter (Nemotron)** writes the brief, the recommended play and a draft email. If memory shows
   that play was already tried, Rind recommends a different one.
8. **Memory (Meterless H-MEM)** stores each verdict, recalls account notes next week, and marks
   memories that changed a decision as helpful. Accounts whose data hasn't changed reuse last week's verdict.

**Why the Verifier matters:** a language model will confidently invent a number. In a live run Nemotron
claimed one account's usage fell 48.2%; Zetaris said it rose 1.7%. The Verifier rejected the claim and
the account was correctly cleared. That rejection is visible in the UI and the trace.

## Results

**Live runs (Zetaris + Nightshift → NVIDIA Nemotron + H-MEM)** on 60 synthetic accounts with a hidden
answer key (`eval/ground_truth.json`: 13 at risk, 8 of them behind a green score, and 12 decoys built to
fool simple rules):

- **Larger run:** 11 accounts flagged, all 11 truly at risk; **0 of 12 decoys flagged.**
  Highlights: a hallucinated −48.2% usage drop caught by the Verifier (real value +1.7%); one account
  cleared because memory recorded that its accounts-payable team always pays net-45; one usage dip
  cleared as seasonal (same dip last year); one recommended play changed because memory showed it had already failed.
- **Latest run (5 accounts, demo setting):** 16 Nemotron calls, 7.8 minutes. All 5 flagged accounts
  are planted watermelons; 0 false alarms. Files: `evidence/live_run_result.json`, `evidence/live_run_trace.jsonl`
  (the trace records the model that served every call).

Baselines on the same data:

| Method | At-risk caught | Watermelons caught | Decoys wrongly flagged |
|---|---|---|---|
| CRM health score | 5/13 | 0/8 | 0/12 |
| Naive rules, no Skeptic/Verifier | 13/13 | 8/8 | 9/12 |

Benchmark script: `python eval/run_eval.py`. Note: `evidence/eval_results.md` is a **sample-mode pipeline check** (offline heuristic, no AI model; it says so in the file). It shows the plumbing and scoring work end to end; the live results above are the ones produced by Nemotron.

## Architecture

```
          Zetaris (one SQL access point, 5 Lightning databases = 5 systems)
   CRM ─┐  Product analytics ─┐  Support desk ─┐  Billing ─┐  Contacts ─┐
        └──────── rind.* SchemaStore views + rind.account_360 (federated join) ──────┐
                                                                                     │ 7 queries / run
 Planner ─▶ Investigator ─▶ Verifier ─▶ Skeptic ─▶ Verifier ─▶ Risk/Escalation ─▶ Reporter ─▶ CSM
               ▲               │  missed signal / bad claim        │
               └───────────────┴──── send back (max 2 rounds) ◀────┘
    ▲                                                                         │
    └──────────────────────── Meterless H-MEM (account memory) ◀──────────────┘
 AI agents (Planner, Investigator, Skeptic, Reporter): Meterless Nightshift ─▶ NVIDIA Nemotron
 Rule agents (Verifier, Risk/Escalation): deterministic Python, so the final label is reproducible
```

## Sponsor technology

- **Zetaris (data layer):** every fact comes from Zetaris SQL over its REST API. Five Lightning
  databases model five separate systems with different customer keys; `rind.account_360` joins them
  in one federated query. `scripts/setup_zetaris.py` registers and verifies everything.
- **Meterless Engine, H-MEM:** account memory across weekly runs, in `hmem/` (TypeScript, adapted from
  the Meterless H-MEM reference, Apache-2.0, with customer-success domain classification and account/play
  entities). **Passes the official H-MEM conformance suite 13/13** (`evidence/hmem_conformance.txt`,
  `evidence/hmem_conformance_windows.txt`). Python talks to it through `hmem/bridge.ts`.
  Recall is scoped per account; memories that change a decision get `helpful` feedback; every write is
  in the H-MEM audit ledger. Tests: `pytest tests/test_memory.py`.
- **Meterless Nightshift (primary AI provider):** every model call goes to the Nightshift Runtime
  (OpenAI-compatible, `meterless-app: rind` / `meterless-scope: user` headers).
  `python -m agent.cli check` proves the connection.
- **NVIDIA Nemotron:** Nightshift served every call in the live runs with
  `nvidia/nemotron-3.5-lightning-30b-a3b`, as recorded per call in the trace.

## The app

`streamlit run app.py` opens a CRM-style workspace:

- **Needs attention:** the accounts list, the CRM record next to **How Rind decided** (every agent step in order),
  and the **Rind panel**: risk badge, the reasons with their source system, what was ruled out, the recommended
  action and a draft email.
- **Ruled out:** accounts that tripped a warning sign, with the innocent explanation Rind found.
- **Run log:** the SQL sent to Zetaris, the model that served each call, every event.
- **Scorecard:** the benchmark against the answer key.

## Run it

**Sample mode (no Zetaris, no Nightshift, no credentials):**

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cd hmem && npm install && cd ..   # H-MEM engine (needs Node.js 20+)
set SAMPLE_MODE=true            # macOS/Linux: export SAMPLE_MODE=true
set RIND_MAX_DEEP_DIVES=26
python -m agent.cli seed-memory
python -m agent.cli run
streamlit run app.py
```

**Live:**

1. Zetaris running locally (hackathon Participant Setup Guide), API key created in its web UI.
2. Nightshift running with NVIDIA connected in Providers.
3. `copy .env.example .env` and fill it in (never commit `.env`).
4. `python scripts/setup_zetaris.py` registers and verifies the five sources.
5. `python -m agent.cli check` → PASS lines for Zetaris and Nightshift.
6. `python -m agent.cli seed-memory`, then `streamlit run app.py` or `python -m agent.cli run`.
7. `RIND_MAX_DEEP_DIVES` sets how many accounts get a deep investigation per run (5 for a fast demo).

**Tests:** `pytest -q` (8 tests: H-MEM conformance and cross-session memory, the Verifier, and an
end-to-end sample-mode run, which needs `RIND_MAX_DEEP_DIVES=26`).

## Repository

```
agent/        config, data (Zetaris + sample engine), llm (Nightshift), memory (H-MEM),
              agents, verifier, orchestrator, cli, trace
app.py        Streamlit UI for the CSM
data/         synthetic sources (served to Zetaris), sample memory seed
scripts/      generate_data.py, register_zetaris_sources.sql, setup_zetaris.py
eval/         ground_truth.json, run_eval.py
hmem/         H-MEM engine, bridge, conformance suite
tests/        memory, verifier, end-to-end sample-mode test
evidence/     live run result + trace, H-MEM conformance output, eval results
```

## Limitations and next steps

- Synthetic data with clean patterns; one CSM's book of 60 accounts; weekly granularity.
- The free Nemotron endpoint averages roughly 40–60 s per call, so a full 26-account run is slow;
  the demo uses 5 deep investigations. Bulk evidence (7 queries per run) and reuse of unchanged accounts keep it practical.
- Next: write verdicts back to the CRM (e.g. a Salesforce account field and side panel), Slack/email digest
  on Monday morning, and learning from which plays actually saved accounts.
- Sample mode uses recorded model responses where available and a labelled offline heuristic otherwise;
  it is a demo fallback, not the product.

## Pre-existing components

Project code was built during the build window (Oct 22–27, 2026), with AI coding assistance.
Declared third-party components: the H-MEM engine in `hmem/src` and its conformance suite in
`hmem/conformance` are adapted from the Meterless H-MEM reference (Apache-2.0, `hmem/LICENSE-meterless-hmem`);
open-source libraries in `requirements.txt` and `hmem/package.json`.
