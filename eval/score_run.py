"""Score the newest live run (from the app or the CLI) against the hidden answer key.

    python eval/score_run.py            # newest logs/result_*.json served by a real model
Writes evidence/live_full_run_score.md and copies the run's result + trace into evidence/.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

files = sorted(glob.glob("logs/result_*.json"), key=os.path.getmtime, reverse=True)
live = [f for f in files if any(m != "offline-heuristic" for m in json.load(open(f, encoding="utf-8")).get("model_usage", {}))]
if not live:
    sys.exit("No live run found in logs/ (every run there used the offline heuristic).")
path = live[0]
r = json.load(open(path, encoding="utf-8"))
gt = {a["account_id"]: a for a in json.load(open("eval/ground_truth.json", encoding="utf-8"))["accounts"]}

flagged = {a["account_id"] for a in r["accounts"] if a["risk"] in ("high", "medium")}
at_risk = {k for k, a in gt.items() if a["at_risk"]}
melons = {k for k, a in gt.items() if a["label"] == "watermelon"}
decoys = {k for k, a in gt.items() if a["label"].startswith("decoy")}
healthy = {k for k, a in gt.items() if a["label"] == "healthy"}
investigated = {a["account_id"] for a in r["accounts"]}

name = lambda k: gt[k]["account_name"]
tp = flagged & at_risk
missed = at_risk - flagged
fp = flagged - at_risk
precision = len(tp) / len(flagged) if flagged else 0.0
S = r["summary"]

lines = [
    f"# Rind live run scored against the answer key",
    "",
    f"Run `{r['run_id']}` · mode `{r.get('mode')}` · {S.get('investigated')} of {S.get('accounts')} accounts investigated · "
    f"{S.get('zetaris_queries')} Zetaris queries · {S.get('llm_calls')} model calls · reused unchanged: {S.get('reused_unchanged', 0)}",
    "",
    "Models that served the calls (reported by Nightshift): "
    + ", ".join(f"`{m}` x{n}" for m, n in r.get("model_usage", {}).items()),
    "",
    "| Measure | Result |",
    "|---|---|",
    f"| At-risk accounts caught | {len(tp)}/{len(at_risk)} |",
    f"| Watermelons caught (green in CRM, actually at risk) | {len(flagged & melons)}/{len(melons)} |",
    f"| Decoys wrongly flagged | {len(flagged & decoys)}/{len(decoys)} |",
    f"| Healthy accounts wrongly flagged | {len(flagged & healthy)}/{len(healthy)} |",
    f"| Precision (flagged accounts that are truly at risk) | {precision:.2f} |",
    "",
    f"Missed: {', '.join(name(k) + ('' if k in investigated else ' (not investigated)') for k in sorted(missed)) or 'none'}",
    "",
    f"False alarms: {', '.join(name(k) for k in sorted(fp)) or 'none'}",
    "",
    f"Decoys investigated and correctly ruled out: {', '.join(name(k) for k in sorted((decoys & investigated) - flagged)) or 'none'}",
    "",
    "Degraded (fell back to rules on some AI step): " + ("YES, re-run before relying on this" if S.get("degraded") else "no"),
]
Path("evidence").mkdir(exist_ok=True)
Path("evidence/live_full_run_score.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
shutil.copy(path, "evidence/live_full_run_result.json")
if Path(r.get("trace_file", "")).exists():
    shutil.copy(r["trace_file"], "evidence/live_full_run_trace.jsonl")
print("\n".join(lines))
print("\nSaved evidence/live_full_run_score.md")
