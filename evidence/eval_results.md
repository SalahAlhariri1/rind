# Rind evaluation (sample mode, 1 run(s))

Generated 2026-10-25T22:42:21+00:00. 60 synthetic accounts: 13 truly at risk (8 hidden behind a green CRM score), 12 decoys built to fool simple rules (seasonal dips, habitual late payers, solved urgent tickets, replaced champions), 35 healthy.

| Method | At-risk caught | Watermelons caught | Decoys wrongly flagged | Healthy wrongly flagged | Precision |
|---|---|---|---|---|---|
| CRM health score (red only) | 5/13 | 0/8 | 0/12 | 0/35 | 1.0 |
| Naive rules, no Skeptic | 13/13 | 8/8 | 9/12 | 0/35 | 0.591 |
| **Rind** (mean of 1) | 13/13 | 8/8 | 0/12 | 0/35 | 1.0 |

Evidence check: 100% of Rind's claims cite a fact that matches the Zetaris query result exactly.

Models that served Rind's requests (from Nightshift's responses): `offline-heuristic` x119

- run `20261025T224221Z-8ae5f5`: caught 13/13, missed none, false alarms none
