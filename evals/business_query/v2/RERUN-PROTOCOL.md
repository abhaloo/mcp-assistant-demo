# BQ 19×3 rerun protocol (post-remediation; bq-07 excluded)

Owner 2026-08-14: Azure subscription expired. Drop `azure-luna-plan` from
this diagnostic. Preferred Luna provider is OpenAI-direct (`openai-luna-plan`,
xhigh). Replace Azure as a provider in a later slice.

`19×3` means **19 cases × 3 repeats per arm**, not 19×3 arms. Per-arm ceiling
is `--max-calls 114` = 19 × 3 × (1 + repair). Two live arms → up to 228
planner calls. Results per arm: 57 (threshold below uses 2/57).

1. Probe first (≈ $0.10): 1 case (bq-09) × 1 repeat on the remaining
   json_schema arm `openai-luna-plan` (xhigh), and a same-commit health
   probe on `deepseek-direct-plan` (max, json_object).
   `--run-kind diagnostic --confirm-spend`, payload capture on. Abort the
   matrix if ANY of: (a) `tokens_reasoning` is null on the openai arm;
   (b) a validation failure leaves no masked payload in the results row;
   (c) the probe returns `planner_schema_invalid` — read the captured payload,
   fix the shape, re-probe before spending on the matrix;
   (d) the probe returns `planner_capability_mismatch` — the strict schema was
   rejected by the provider; fix the schema, re-probe.
   Azure `azure-luna-plan` previously fired (d)+(b) and is out of this run.
2. Matrix: openai-luna-plan (xhigh), deepseek-direct-plan (max) — SAME commit,
   SAME cases/oracle/specs hashes, SAME bundle (25fb9733…), SAME DB
   fingerprint, recorded in each preflight. One arm at a time.
   `--repeats 3 --max-calls 114` per arm.
   bq-07 is `eval_status: excluded` and must not appear in results.jsonl.
   The matrix is internally one-variable (provider lane only: OpenAI vs
   DeepSeek). It is NOT headline-comparable to the 2026-08-14 22/60-20/60
   baseline, and NOT comparable to a 3-arm run that included Azure: the
   bundle (64→68 capabilities), validator, scorer, and prompt all changed,
   bq-07 was dropped, and Azure is absent — compare per-layer
   `failures_by_detail` / `value_diagnostic` deltas on shared case ids,
   never bare pass rates.
   Preflight note: `arm` was a STRING in the 2026-08-14 paid preflights, a
   dict in `luna-uplift`; from this protocol on it is the model-arm dict with
   `causal_arm` beside it. Cross-generation tooling must branch on type.
3. Evidence: G4 failure capture is a MANUAL post-run step owned by the
   orchestrating session: extract every failed oracle case to
   `evals/failures/<run-id>_<case-id>_<arm>.json` —
   `Get-Content results.jsonl | %{ $_ | ConvertFrom-Json } | ? { -not $_.passed } | %{ ... }`
   (one file per row; the run is not DONE until these exist). G6 — insights-log
   entry naming the specific cases that moved (no bare percentages).
4. Report per-layer movement using failures_by_detail + value_diagnostic +
   repairs, not pass-rate alone. planner_schema_invalid > 2/57 on any arm
   (19 cases × 3) = the remediation missed something; read the captured
   payloads and stop before more spend.
5. Status ceiling: this matrix is diagnostic evidence (INTEGRATED at most);
   ACCEPTED still requires the 80-case gate suite.

## 2026-08-17 — the answer key moved; earlier pass rates are not a baseline

Three suite defects were repaired after run `92f5a28e`, so "SAME cases/oracle/
specs hashes" above holds WITHIN a matrix, not across generations:

- `oracle/bq-14.sql` gained `AND b.type = 'Invoice'` (its key had counted
  1,250,000.00 of cash against a Payable Quotation). Key regenerated; exactly
  one line of `oracle-results.jsonl` changed.
- bq-10's scoring spec named `bill_outstanding`, which has no overdue filter and
  could never match its own oracle; it now names `overdue_outstanding`.
- bq-05's scoring spec now declares `job.customer_order_number` as an
  alternative route, so the case grades the value rather than the path.

**bq-05, bq-10 and bq-14 pass rates are NOT comparable to `2dd6f926` or
`92f5a28e`.** Every other case is unaffected. `failures_by_layer` is also not
comparable across that line: two `trace.fail` stamps were added, moving counts
out of the bare `reason_code` bucket into `planner` and `resolver`. That is an
instrumentation change, not a model change.

## 2026-08-18 — the bundle gained a consumption route

`ai_v1_bq_work_order_item_fact` exposes what jobs CONSUMED, alongside the
invoice lines that record what was sold. Bundle `25fb9733...` -> `843800d4...`
(68 -> 71 capabilities, 2 -> 3 resolvable dimensions, 6 -> 7 join edges), and
the eval database gains an 8th semantic view, so its `views_fingerprint` moves
too.

**Runs pinned to `25fb9733...` are not like-for-like with anything after this.**
Every case's plan space widens: a planner that previously had no way to express
a consumption question now does, so a case can change outcome without the model
changing at all. The preflight pins move from 58/7 to 59/8 views in the same
change.

Compare per-layer `failures_by_detail` on shared case ids, never bare pass
rates, across this line.

## 2026-08-19 — work_order_item vocabulary correction (RD-1)

Bundle `008e302c...` -> `bd5f3c6c...` corrects `work_order_item` and
`product_units_used` descriptions from "consumed by jobs" to "lines planned /
entered on a job's INFO-tab planning grid".

Runs pinned to `008e302c...` are non-comparable across this line.

