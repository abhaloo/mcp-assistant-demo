# SQL agent golden set (`cases.jsonl`)

This is the **input** to the SQL agent diagnostic + regression harness (being built — see
the design spec under `docs/superpowers/specs/`). Each line is one evaluation case. The
harness runs the real SQL agent for each case, executes the agent's SQL **and** your
`gold_sql` against a frozen snapshot DB, and scores whether the result sets match.

> **Add your "poor answers so far" here.** Each one becomes a regression case: once it's
> in this file, every future change (prompt, `top_k`, model, schema) is re-scored against it.

## File format

One JSON object per line (JSONL — no comments, no trailing commas). Keep `gold_sql` on a
single line; whitespace doesn't matter to MySQL.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `id` | string | yes | Stable unique id, e.g. `sql-001`. Never reuse/renumber — run history keys off it. |
| `question` | string | yes | The natural-language question, worded exactly as a user would ask. |
| `role` | string | yes | `admin` \| `finance` \| `sales` \| `default` \| … — drives access tiers **and** the redaction path. |
| `permissions` | list[str] | no (default `[]`) | Extra Spatie permissions; combined with `role` to derive access tiers (mirrors production). |
| `gold_sql` | string | yes | The **correct** SQL. Must run against the snapshot and return the intended rows. |
| `ordered` | bool | no (default `false`) | `true` only when row order is part of correctness (ranking / top-N / "most recent"). |
| `tags` | list[str] | no | Free labels for stratified reporting. Use a complexity tag (`simple`/`medium`/`hard`) + feature tags (`join`, `aggregate`, `subquery`, `time-range`, `filter`, `ambiguous`). Special: `clarify-ok` — no SQL + clarifying answer is scored as success (bare “revenue” etc.), not `no_query` miss. Prefer disambiguating case wording when gold expects a specific metric. |
| `clarify_answer` | string | no | Eval-only oracle for the shared clarify loop: when the agent emits `CLARIFY: …`, the harness replies with this business phrase (e.g. `invoiced sales`) and continues — same `invoke_with_clarifications` path as prod. Never put gold SQL here. |

## Roles & the PII redaction path

`role` is not cosmetic — it changes what the agent sees:

- **`sales`, `default`, and other non-privileged roles** → redaction is **ON**. Result rows
  reach the agent (and the harness) as tokens like `<PERSON_1>`, never real values.
- **`admin` and `finance`** → redaction is **OFF** by design (they may see raw data). Real
  values flow through the agent in memory.

Either way, **the harness never writes raw rows to disk** — it compares result sets in memory
and persists only `{match, row_count, valid_sql, latency, tool_calls, error}` plus
token-scrubbed SQL. So `cases.jsonl` and the run files stay PII-free. The only raw data lives
in the local (gitignored) snapshot DB. **Cover all four+ roles** so we diagnose every path.

## Authoring checklist (per case)

1. **One unambiguous question.** If a human couldn't write the SQL without guessing a time
   range / status / entity, the question is ambiguous — either pin it down, or tag it
   `ambiguous` and expect the agent to ask a clarifying question (a separate, future scoring
   path; for now keep cases answerable).
2. **Verify your `gold_sql` before trusting it.** Run it against the snapshot yourself and
   eyeball the rows. A wrong gold query silently corrupts every score — broken gold is the
   #1 way these harnesses lie.
3. **Set `ordered` deliberately.** Default `false` (two correct SQLs can return rows in
   different orders). Set `true` for "top 5", "most recent", "highest", "ranked".
4. **Stratify by complexity.** Aim for a spread: `simple` (single table, no join),
   `medium` (1–2 joins / aggregation), `hard` (subqueries, window functions, multi-join).
   Don't let the set be all easy counts — that inflates the score.
5. **Include the messy ones.** Questions over columns with NULLs / dirty values / odd
   enums are where accuracy actually breaks; a too-clean set reads optimistically.
6. **Seed real failures.** Every query that's been "unsatisfactory" so far belongs here.

## Multi-tenancy note (`entity_id`)

Most tables carry an `entity_id` (multi-tenant). Decide once whether your questions are
**entity-scoped** ("how many customers does entity 3 have?" → `WHERE entity_id = 3`) or
**global** ("how many customers in total?"). Encode the same choice in **both** the
`question` and the `gold_sql`, or the agent and gold will disagree for the wrong reason.

## Target size

Start with your real failure cases (however many you have). For a trustworthy signal when we
later compare two models, plan toward **~50–100+ cases** stratified across complexity and
roles. (Detecting a small accuracy delta with statistical confidence wants 200+ — that's a
Step-3 concern, not a blocker for diagnosis now.)

## Worked examples

The four rows currently in `cases.jsonl` are valid, runnable templates against your schema —
edit or replace them:

```json
{"id": "example-count", "question": "How many customers are registered?", "role": "sales", "gold_sql": "SELECT COUNT(*) FROM customers", "ordered": false, "tags": ["simple", "aggregate"]}
{"id": "example-ordered", "question": "What are the 5 most recent bills by creation date?", "role": "finance", "gold_sql": "SELECT invoice_number, created_at FROM bills ORDER BY created_at DESC LIMIT 5", "ordered": true, "tags": ["medium", "time-range", "ordered"]}
```

## Removed cases (2026-08-09)

Two cases were removed. The **questions** are legitimate product feedback; the **cases**
were not measurements.

| Removed | Question | Why |
|---|---|---|
| `prod-jobs-delayed` | "which jobs have been delayed the most this week" | Needs `delivery_date` **and** `actual_delivery_date`; both are 100% NULL |
| `prod-dept-slowest` | "which department has been the slowest this week" | Needs `actual_delivery_date`; 100% NULL |

Both golds returned **zero rows**, and `compare([], [], …)` scores empty-vs-empty as a
match — so **any** query returning nothing passed, including the wrong table or the wrong
year. That is ~4.5 pp of free credit per arm, and it rewarded probing the NULL decoy
columns while an agent that correctly said "we don't track completion" scored a miss.

They were **removed, not scored `match=None`**: a non-bool `match` classifies as
`no_execution_score`, which is not exempt from the 2% opportunity-failure gate
(`sql_freeze.py`), so 2 cases × 5 repeats × 2 arms = 4.5% would abort every campaign
before scoring anything.

`tests/eval/test_sql_cases_dataset.py` pins the count at 42 and fails if either id
returns. Billing tracking a real completion or delivery fact is the prerequisite for
re-adding either question.

## Clarification scoring

A clarification only earns credit when a case carries the `clarify-ok` tag, and an
oracle case requires post-reply SQL. Before 2026-08-09 **no case carried either**, so
every clarifying question scored `no_query` — a miss — and each rule that taught the
agent to ask instead of guess mechanically lowered the measured pass rate.

Three genuinely ambiguous cases now carry one or the other, each with a reason in
`notes`. Do not add an oracle to an unambiguous case to lift the score; the lint test
requires the reason, and the diff shows the addition.
