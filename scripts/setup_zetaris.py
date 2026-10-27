"""Register Rind's five data sources in your local Zetaris and verify them.

  python scripts/setup_zetaris.py --dry-run   # show the statements first
  python scripts/setup_zetaris.py             # run them, one at a time
  python scripts/setup_zetaris.py --verify    # only count rows in each view

Needs ZETARIS_REST_URL, ZETARIS_ORG_ID, ZETARIS_API_KEY and RIND_DATA_BASE_URL in .env.
Re-runnable: "already exists" errors are skipped.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SAMPLE_MODE"] = "false"

from agent.data import ZetarisEngine  # noqa: E402

EXPECTED = {"rind.crm_accounts": 60, "rind.product_usage": 1920, "rind.support_tickets": None,
            "rind.billing_invoices": 240, "rind.contacts": None, "rind.account_360": 60}


def statements(base_url: str) -> list[str]:
    sql = (ROOT / "scripts" / "register_zetaris_sources.sql").read_text()
    sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    sql = sql.replace("{{DATA_BASE_URL}}", base_url.rstrip("/"))
    return [s.strip() for s in re.split(r";\s*\n", sql) if s.strip()]


def verify(z: ZetarisEngine) -> bool:
    ok = True
    for view, want in EXPECTED.items():
        try:
            rows = z.query(f"SELECT COUNT(*) AS n FROM {view}", ["n"], 1)
            n = int(list(rows[0].values())[0])
            good = n > 0 and (want is None or n == want)
            print(f"{'PASS' if good else 'FAIL'} {view}: {n} rows" + ("" if want is None else f" (expected {want})"))
            ok &= good
        except Exception as e:
            print(f"FAIL {view}: {e}")
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    base = os.getenv("RIND_DATA_BASE_URL", "")
    if not base and not a.verify:
        print("Set RIND_DATA_BASE_URL in .env, e.g. https://raw.githubusercontent.com/<you>/rind/main/data/sources")
        return 1
    if a.dry_run:
        for i, s in enumerate(statements(base), 1):
            print(f"-- [{i}]\n{s}\n")
        return 0
    z = ZetarisEngine()
    if not a.verify:
        for i, s in enumerate(statements(base), 1):
            first = s.splitlines()[0][:90]
            try:
                z.query(s, [], 10)
                print(f"OK   [{i}] {first}")
            except Exception as e:
                msg = str(e)
                if "exist" in msg.lower():
                    print(f"SKIP [{i}] {first}  (already exists)")
                elif s.upper().startswith("CACHE"):
                    print(f"WARN [{i}] {first}  (cache is optional: {msg[:120]})")
                else:
                    print(f"FAIL [{i}] {first}\n     {msg[:400]}")
                    return 1
    return 0 if verify(z) else 1


if __name__ == "__main__":
    raise SystemExit(main())
