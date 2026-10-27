# Rind

**Rind finds the customers who look healthy in your CRM but are about to churn, and tells you why.**

Open Agent Hackathon 2026 · Track 02: The Agent That Can Explain Why

## The user and the problem

A customer success manager (CSM) at a B2B software company looks after about 60 accounts.
Every Monday they decide which customers need saving this week. The CRM gives each account
a health score, but the score is recalculated monthly from logins and old survey answers,
so it lags reality. Customer success teams call the misses **watermelon accounts**: green
outside, red inside. The real warning signs are spread across five systems (product usage,
support desk, billing, contacts, CRM notes), and nobody has time to check all five for 60
accounts every week.

## What Rind does

One click runs the weekly check:

1. **Planner** reads a one-row-per-account snapshot built from all five systems (via Zetaris)
   plus what Rind remembers from earlier weeks, and picks the accounts worth investigating.
2. **Investigator** chooses which checks to run per account and queries Zetaris for the evidence,
   then writes claims, each citing the exact fact it came from.
3. **Skeptic** challenges every claim. When a plausible alternative hasn't been ruled out
   (a seasonal dip, a customer who always pays late, a champion who was already replaced,
   an urgent ticket that was solved) it **sends the Investigator back** to run that check.
   Unsupported claims are dropped.
4. **Escalation** (a deterministic playbook policy) escalates high-risk, high-revenue or
   near-renewal accounts.
5. **Reporter** writes the account brief: why (with sources), the recommended play, and a draft email.
   If memory shows a play was already tried and failed, it recommends something else.
6. **Memory (Meterless H-MEM)** stores each decision so next week's run builds on it.

Output: a ranked list of at-risk accounts with reasons, what the Skeptic cleared, and the full trace.

## Results (synthetic benchmark)

See `evidence/eval_results.md`. 60 accounts with planted ground truth: 13 at risk (8 hidden
behind a green score) and 12 decoys built to fool simple rules.

| Method | At-risk caught | Watermelons caught | Decoys wrongly flagged |
|---|---|---|---|
| CRM health score | 5/13 | 0/8 | 0/12 |
| Naive rules, no Skeptic | 13/13 | 8/8 | 9/12 |
| Rind | see `evidence/eval_results.md` (live runs through Nightshift) | | |

## Architecture

```
          Zetaris (one SQL access point, 5 Lightning databases = 5 systems)
   CRM ─┐  Product analytics ─┐  Support desk ─┐  Billing ─┐  Contacts ─┐
        └──────────── rind.* SchemaStore views + rind.account_360 (federated join) ───┐
                                                                                       │ REST SQL
 Planner ──▶ Investigator ──▶ Skeptic ──supported──▶ Escalation ──▶ Reporter ──▶ CSM  │
    ▲             ▲  │            │                                     │              │
    │             └──┴─needs_check┘  (bounded: max 2 send-backs)        │              │
    └──────────────────── Meterless H-MEM (account memory) ◀────────────┘
 All reasoning: Meterless Nightshift ─▶ NVIDIA Nemotron lanes (fast: Lightning, deep: Super)
```

## Sponsor technology

- **Zetaris (data layer):** every fact the agents use comes from Zetaris SQL over its REST API.
  Five Lightning databases model five separate systems with five different customer keys;
  `rind.account_360` joins three of them in one federated query. Setup is one script.
- **Meterless Engine, H-MEM:** account memory across weekly sessions. The engine lives in `hmem/`
  (TypeScript, adapted from the Meterless H-MEM reference, Apache-2.0, with customer-success domain
  classification and account/play entities) and **passes the official H-MEM conformance suite 13/13**
  (`evidence/hmem_conformance.txt`). Python reaches it through `hmem/bridge.ts`. Every recall uses the
  spec's hybrid ranking, scoped per account; memories that change a decision get the spec's `helpful`
  feedback, and every write is in the H-MEM audit ledger (`memory/hmem/ledger.jsonl`).
  Tests: `pytest tests/test_memory.py` (conformance, cross-session recall in a new process, feedback
  delta + audit, memory changes the recommendation).
- **Meterless Nightshift (primary AI provider):** all model calls go to the Nightshift Runtime
  (OpenAI-compatible, `meterless-app` / `meterless-scope` headers). `python -m agent.cli check` proves it.
- **NVIDIA Nemotron:** two Nightshift lanes pinned to Nemotron models. The trace logs the model
  that served every request.

## Run it

**Sample mode (no Zetaris, no Nightshift, no credentials):**

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cd hmem && npm install && cd ..   # H-MEM engine (needs Node.js 20+)
set SAMPLE_MODE=true            # macOS/Linux: export SAMPLE_MODE=true
python -m agent.cli seed-memory
python -m agent.cli run
streamlit run app.py
```

**Live:**

1. Zetaris running locally (see the hackathon Participant Setup Guide), API key created in its web UI.
2. Nightshift running with NVIDIA connected in Providers and Nemotron lanes configured.
3. `copy .env.example .env` and fill it in (never commit `.env`).
4. `python scripts/setup_zetaris.py` registers and verifies the five sources.
5. `python -m agent.cli check` → PASS lines for Zetaris and Nightshift.
6. `python -m agent.cli seed-memory`, then `streamlit run app.py` or `python -m agent.cli run`.
7. `python eval/run_eval.py --runs 5` benchmarks it.

## Repository

```
agent/        config, data (Zetaris + sample engine), llm (Nightshift), memory (H-MEM), agents, orchestrator, cli
app.py        Streamlit UI for the CSM
data/         synthetic sources + README, sample memory seed, recorded responses for sample mode
scripts/      generate_data.py, register_zetaris_sources.sql, setup_zetaris.py
eval/         ground_truth.json, run_eval.py
tests/        memory (engine) test, end-to-end sample-mode test
logs/         one JSONL trace + result file per run
evidence/     eval results, setup proof
```

## Limitations

Synthetic data with clean patterns; one CSM's book of 60 accounts; weekly granularity.
Sample mode uses recorded model responses where available and a clearly labelled offline
heuristic otherwise; it is a demo fallback, not the product.

## Pre-existing components

Project code was built during the build window (Oct 22-27, 2026). Declared third-party components: the H-MEM
engine in `hmem/src` and its conformance suite in `hmem/conformance` are adapted from the Meterless H-MEM
reference (Apache-2.0, `hmem/LICENSE-meterless-hmem`); open-source libraries in `requirements.txt`.
