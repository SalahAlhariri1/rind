"""Measure Rind against the planted ground truth (Sam's tip: measure something, run it many times).

  python eval/run_eval.py --runs 1          # sample mode or live, whatever .env says
  python eval/run_eval.py --runs 20         # overnight benchmark through Nightshift

Ground truth lives only here (eval/ground_truth.json); it is never served through
Zetaris or shown to the agents. Writes evidence/eval_results.md and .json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.config import SETTINGS  # noqa: E402
from agent.data import DataLayer, snapshot  # noqa: E402
from agent.trace import Trace  # noqa: E402

GT = json.loads((ROOT / "eval" / "ground_truth.json").read_text())["accounts"]
LABEL = {a["account_id"]: a["label"] for a in GT}
AT_RISK = {a["account_id"] for a in GT if a["at_risk"]}
WATERMELON = {a["account_id"] for a in GT if a["label"] == "watermelon"}
DECOYS = {a["account_id"] for a in GT if a["label"].startswith("decoy")}
HEALTHY = {a["account_id"] for a in GT if a["label"] == "healthy"}


def score(flagged: set[str]) -> dict[str, float]:
    tp = len(flagged & AT_RISK)
    return {
        "at_risk_caught": tp, "at_risk_total": len(AT_RISK),
        "watermelons_caught": len(flagged & WATERMELON), "watermelons_total": len(WATERMELON),
        "decoy_false_alarms": len(flagged & DECOYS), "decoys_total": len(DECOYS),
        "healthy_false_alarms": len(flagged & HEALTHY), "healthy_total": len(HEALTHY),
        "precision": round(tp / len(flagged), 3) if flagged else 0.0,
        "recall": round(tp / len(AT_RISK), 3),
    }


def baselines() -> dict[str, dict[str, float]]:
    dl = DataLayer(Trace(mode="eval-baseline"))
    roster = dl.roster("eval")
    snap = snapshot(roster, dl.bulk("eval"), dl.as_of)
    crm_red = {r["account_id"] for r in snap if r["crm_color"] == "red"}
    naive = {r["account_id"] for r in snap if (r["usage_change_pct"] or 0) <= -20 or r["champion_left"]
             or (r["invoice_days_overdue"] or 0) > 14 or (r["open_high_tickets"] or 0) >= 1}
    return {"CRM health score (red only)": score(crm_red), "Naive rules, no Skeptic": score(naive)}


def citation_rate(result: dict) -> float:
    total = ok = 0
    for acc in result["accounts"]:
        facts = {}
        for ev in acc["evidence"].values():
            facts.update(ev["facts"])
        for c in acc["supported_claims"]:
            total += 1
            evs = c.get("evidence") or []
            if evs and all(e.get("fact") in facts and str(facts[e["fact"]]) == str(e.get("value")) for e in evs):
                ok += 1
    return round(ok / total, 3) if total else 1.0


def main() -> None:
    from agent.orchestrator import run_weekly
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)
    a = ap.parse_args()

    runs = []
    for i in range(a.runs):
        import os
        import shutil
        mem_dir = ROOT / "memory" / f"eval_hmem_{i}"  # fresh H-MEM store per run, seeded like the demo
        shutil.rmtree(mem_dir, ignore_errors=True)
        os.environ["RIND_HMEM_DIR"] = str(mem_dir)
        os.environ["RIND_REUSE_UNCHANGED"] = "false"  # every eval run investigates from scratch
        mem_path = ROOT / "memory" / f"eval_memory_{i}.json"
        os.environ["RIND_MEMORY_PATH"] = str(mem_path)
        from agent.memory import get_memory, seed_demo_memory
        seed_demo_memory(get_memory())
        out = run_weekly(progress=lambda s: print(f"[run {i+1}/{a.runs}]", s, file=sys.stderr))
        flagged = {r["account_id"] for r in out["accounts"] if r["risk"] in ("high", "medium")}
        s = score(flagged)
        s["citation_rate"] = citation_rate(out)
        s["run_id"] = out["run_id"]
        s["model_usage"] = out["model_usage"]
        s["llm_calls"] = out["summary"]["llm_calls"]
        s["zetaris_queries"] = out["summary"]["zetaris_queries"]
        s["missed"] = sorted((AT_RISK - flagged))
        s["false_alarms"] = sorted(flagged - AT_RISK)
        runs.append(s)
        mem_path.unlink(missing_ok=True)
        shutil.rmtree(mem_dir, ignore_errors=True)

    base = baselines()
    agg = {k: round(statistics.mean(r[k] for r in runs), 3) for k in
           ("at_risk_caught", "watermelons_caught", "decoy_false_alarms", "healthy_false_alarms", "precision", "recall", "citation_rate")}
    models: dict[str, int] = {}
    for r in runs:
        for m, n in r["model_usage"].items():
            models[m] = models.get(m, 0) + n

    result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "mode": "sample" if SETTINGS.sample_mode else "live", "runs": a.runs,
              "rind_mean": agg, "baselines": base, "per_run": runs, "models": models}
    (ROOT / "evidence").mkdir(exist_ok=True)
    (ROOT / "evidence" / "eval_results.json").write_text(json.dumps(result, indent=1))

    def row(name: str, s: dict) -> str:
        return (f"| {name} | {s['at_risk_caught']}/{s.get('at_risk_total', 13)} | {s['watermelons_caught']}/{s.get('watermelons_total', 8)} | "
                f"{s['decoy_false_alarms']}/{s.get('decoys_total', 12)} | {s['healthy_false_alarms']}/{s.get('healthy_total', 35)} | {s['precision']} |")

    md = [f"# Rind evaluation ({result['mode']} mode, {a.runs} run(s))", "",
          f"Generated {result['generated_at']}. 60 synthetic accounts: 13 truly at risk (8 hidden behind a green CRM score), "
          "12 decoys built to fool simple rules (seasonal dips, habitual late payers, solved urgent tickets, replaced champions), 35 healthy.", "",
          "| Method | At-risk caught | Watermelons caught | Decoys wrongly flagged | Healthy wrongly flagged | Precision |",
          "|---|---|---|---|---|---|",
          row("CRM health score (red only)", base["CRM health score (red only)"]),
          row("Naive rules, no Skeptic", base["Naive rules, no Skeptic"]),
          row(f"**Rind** (mean of {a.runs})", {**agg, "at_risk_total": 13, "watermelons_total": 8, "decoys_total": 12, "healthy_total": 35}),
          "", f"Evidence check: {agg['citation_rate']*100:.0f}% of Rind's claims cite a fact that matches the Zetaris query result exactly.",
          "", "Models that served Rind's requests (from Nightshift's responses): " + ", ".join(f"`{m}` x{n}" for m, n in models.items()), ""]
    for r in runs:
        md.append(f"- run `{r['run_id']}`: caught {r['at_risk_caught']}/13, missed {r['missed'] or 'none'}, false alarms {r['false_alarms'] or 'none'}")
    (ROOT / "evidence" / "eval_results.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
