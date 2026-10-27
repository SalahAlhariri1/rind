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

**Full live run** (Zetaris + Nightshift → NVIDIA Nemotron + H-MEM, run `20261027T054113Z-70c31e`) on 60 synthetic
accounts with a hidden answer key (`eval/ground_truth.json`: 13 at risk, 8 of them behind a green CRM score, 12 decoys
built to fool simple rules, 35 healthy). Scored by `python eval/score_run.py`:

| Measure | Rind (live) |
|---|---|
| At-risk accounts caught | **13/13** |
| Watermelons caught (green in the CRM, actually at risk) | **8/8** |
| Decoys wrongly flagged | **0/12** (all 12 investigated and ruled out) |
| Healthy accounts wrongly flagged | 0/35 |
| Precision | 1.00 |
| Model calls | 49, all served by `nvidia/nemotron-3.5-lightning-30b-a3b` (as reported by Nightshift); no fallbacks |
| Zetaris queries | 7 |

25 of the 60 accounts were investigated (the other 35 showed no warning sign). 8 of the 25 had not changed since an
earlier live run, so their earlier Nemotron verdicts were reused; 17 were investigated fresh.
Files: `evidence/live_full_run_score.md`, `evidence/live_full_run_result.json`, `evidence/live_full_run_trace.jsonl`
(every step, every SQL query, and the model that served every call).

Highlights from live runs: in one run Nemotron claimed an account's usage fell 48.2% when Zetaris said it rose 1.7%;
the Verifier rejected the claim and the account was correctly cleared (`evidence/live_run_verifier_catch.jsonl`). Silverlake Insurance is cleared because memory
records that its accounts-payable team always pays about 40 days late; seasonal dips are cleared by comparing with the
same weeks last year; Highmoor Components gets a different play because memory shows admin training was already tried.
`evidence/live_run_result.json` is a short repeat run in which all 5 accounts were unchanged, so every verdict was reused
(1 model call, for the Planner): this is the reuse cache working.

Baselines on the same data:

| Method | At-risk caught | Watermelons caught | Decoys wrongly flagged |
|---|---|---|---|
| CRM health score | 5/13 | 0/8 | 0/12 |
| Naive rules, no Skeptic/Verifier | 13/13 | 8/8 | 9/12 |
| **Rind (live, Nemotron)** | **13/13** | **8/8** | **0/12** |

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
  `python scripts/zetaris_usl_report.py` answers 3 exploratory questions per source, 3 cross-system questions
  on the unified layer, runs the 8 data quality rules (`rind.dq_*` views), and compares a hand-coded join with the
  USL (output: `evidence/zetaris_usl_report.md`). Write-up against the Zetaris criteria: `docs/ZETARIS_USL.md`.
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

## AI and Model Usage

Models (through Meterless Nightshift, the only AI provider the product calls):
- `nvidia/nemotron-3.5-lightning-30b-a3b` served every live call: Planner (choose accounts), Investigator (write
  cited claims), Skeptic (challenge claims) and Reporter (brief, play, email). Rind uses two Nightshift lanes
  (`METERLESS_MODEL_FAST` for the Investigator, `METERLESS_MODEL_DEEP` for the others); the model that actually
  served each request is read from Nightshift's response and logged (`llm_call` events, `model_used`).
- No embeddings model; H-MEM recall uses the engine's own hybrid ranking.

Agent framework: none. A custom Python orchestrator (`agent/orchestrator.py`) with bounded loops, the OpenAI
Python SDK as the client for Nightshift's OpenAI-compatible API, and deterministic Verifier/Escalation rules.

Sponsor technology:
- Zetaris: the data layer; every fact the agents see comes from SQL over the USL (see Sponsor technology).
- Meterless Engine: H-MEM, account memory across weekly runs; conformance 13/13; `pytest tests/test_memory.py`.
- Meterless Nightshift: primary AI provider at `METERLESS_BASE_URL`, `meterless-app: rind` header.
- NVIDIA: Nemotron through Nightshift for all four AI agents. The NVIDIA key lives only in Nightshift's Providers tab.

Development tools: built with Claude (Anthropic) as the AI coding assistant; Google Gemini helped with local
environment setup. AI assistance was used for code, debugging, tests and documentation. The code was tested by
running the test suite, the H-MEM conformance suite and live runs on Zetaris and Nightshift.

Sample mode: `SAMPLE_MODE=true` runs the same agent code on a local DuckDB copy of the five exports, with no calls to
Zetaris or Nightshift. Model steps use recorded responses where available, otherwise a labelled offline heuristic
(logged as `offline-heuristic`). If Nightshift fails during a live run, the step falls back to the same heuristic,
the run is marked `degraded`, and the app and CLI warn about it.

## Data

All data is **synthetic**: `scripts/generate_data.py` (seed 42) writes the five system exports in `data/sources/`
and the answer key `eval/ground_truth.json`. No real people, companies or personal data. Zetaris reads the exports
over HTTPS from this repository (`RIND_DATA_BASE_URL`); sample mode reads the same files from disk.

## The app

`streamlit run app.py` opens a CRM-style workspace:

- **Needs attention:** the accounts list, the CRM record next to **How Rind decided** (every agent step in order),
  and the **Rind panel**: risk badge, the reasons with their source system, what was ruled out, the recommended
  action and a draft email.
- **Ruled out:** accounts that tripped a warning sign, with the innocent explanation Rind found.
- **Run log:** the SQL sent to Zetaris, the model that served each call, every event.
- **Scorecard:** the latest live run scored against the answer key (`evidence/live_full_run_score.md`).

## Use it without the UI

`run.py` takes JSON and returns JSON. Progress lines go to stdout as the run happens; the full trace is
written to `logs/run_<run_id>.jsonl`.

```bash
python run.py --sample --input input_examples/example_1.json            # sample mode, no credentials
python run.py --input input_examples/example_1.json --output out.json   # live (Zetaris + Nightshift)
python run.py                                                            # weekly triage, the Planner picks accounts
python run.py --serve --port 8000                                        # HTTP API
curl -X POST http://localhost:8000/run -H "Content-Type: application/json" -d @input_examples/example_1.json
```

Request (every field optional): `{"run_id": "...", "input": {"accounts": ["ACC-1047"], "max_deep_dives": 5},
"options": {"sample_mode": true, "reuse_unchanged": false}}`. The response has `status`, a `summary`, and for each
account what the CRM says vs. what Rind says, the reasons with their source and cited facts, what was ruled out and why,
memory used, the recommended play, escalation and a draft email, plus the agents, the models that served the calls,
and the trace file. Errors come back as `{"status": "error", "error": {"type", "message", "recoverable"}}`.

| Example | Input | What it shows |
|---|---|---|
| 1 | `input_examples/example_1.json` | 3 accounts the CRM scores green (72–89): Rind finds the hidden problems |
| 2 | `input_examples/example_2.json` | 4 false alarms (always pays late, seasonal dip, solved ticket, replaced champion): Rind rules each out |
| 3 | `input_examples/example_3.json` | Memory: earlier notes are recalled, and a play already tried is not recommended again |

Outputs: `output_examples/example_N.json` (live run through Nightshift) and `output_examples/sample_mode/example_N.json`
(sample mode, offline heuristic), each with the trace of the run that produced it (`*.trace.jsonl`).
Each output is produced by running `run.py` on that input; none are written by hand.

## Run it

**Sample mode (no Zetaris, no Nightshift, no credentials).** Uses the bundled synthetic data and labelled
offline answers instead of the model, so anyone can see the full pipeline and UI in a few minutes.
Needs Python 3.11+ and Node.js 20+.

Windows (PowerShell):

```powershell
git clone https://github.com/SalahAlhariri1/rind.git
cd rind
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd hmem; npm install; cd ..
$env:SAMPLE_MODE="true"
$env:RIND_MAX_DEEP_DIVES="26"
python -m agent.cli seed-memory
python -m agent.cli run
python run.py --sample --input input_examples/example_2.json
streamlit run app.py
```

macOS / Linux:

```bash
git clone https://github.com/SalahAlhariri1/rind.git
cd rind
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
(cd hmem && npm install)
export SAMPLE_MODE=true RIND_MAX_DEEP_DIVES=26
python -m agent.cli seed-memory
python -m agent.cli run
python run.py --sample --input input_examples/example_2.json
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
8. `python scripts/zetaris_usl_report.py` produces the Zetaris USL evidence report.

**Tests:** `pytest -q` (11 tests: H-MEM conformance and cross-session memory, the Verifier, the Reporter safety net, the `run.py`
examples and error handling, and an end-to-end sample-mode run, which needs `RIND_MAX_DEEP_DIVES=26`).

## Repository

```
agent/        config, data (Zetaris + sample engine), llm (Nightshift), memory (H-MEM),
              agents, verifier, orchestrator, cli, trace
app.py        Streamlit UI for the CSM
run.py        programmatic entry point (JSON in/out, optional HTTP API)
input_examples/, output_examples/   3 sample requests and the outputs they produced
data/         synthetic sources (served to Zetaris), sample memory seed
scripts/      generate_data.py, register_zetaris_sources.sql, setup_zetaris.py,
              zetaris_quality_rules.sql, zetaris_usl_report.py
docs/         ZETARIS_USL.md (Zetaris USL write-up)
eval/         ground_truth.json, run_eval.py
hmem/         H-MEM engine, bridge, conformance suite
tests/        memory, verifier, end-to-end sample-mode test
evidence/     live run results + traces, H-MEM conformance output, Zetaris USL report, eval results
```

## Limitations and next steps

- Synthetic data with clean patterns; one CSM's book of 60 accounts; weekly granularity.
- The free Nemotron endpoint averages roughly 40–60 s per call, so a full run that investigates 25 accounts
  can take close to an hour; the demo setting uses 5 deep investigations (a few minutes). Bulk evidence (7 queries per run) and reuse of unchanged accounts keep it practical.
- Next: write verdicts back to the CRM (e.g. a Salesforce account field and side panel), Slack/email digest
  on Monday morning, and learning from which plays actually saved accounts.
- Sample mode uses recorded model responses where available and a labelled offline heuristic otherwise;
  it is a demo fallback, not the product.

## Third-party code and licences

- Meterless H-MEM reference engine and conformance suite (adapted in `hmem/`): Apache-2.0
- openai (Python SDK): Apache-2.0 · requests: Apache-2.0 · python-dotenv: BSD-3-Clause
- duckdb: MIT · streamlit: Apache-2.0 · pytest: MIT · tsx: MIT

## Pre-existing components

Project code was built during the build window (Oct 22–27, 2026), with AI coding assistance.
Declared third-party components: the H-MEM engine in `hmem/src` and its conformance suite in
`hmem/conformance` are adapted from the Meterless H-MEM reference (Apache-2.0, `hmem/LICENSE-meterless-hmem`);
open-source libraries in `requirements.txt` and `hmem/package.json`.
