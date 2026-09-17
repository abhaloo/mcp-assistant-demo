# prod_ask — quality suite for `POST /api/ask`

Status: **DRAFT**. Every case carries `review_status: draft`. Nothing here gates a
release until a human approves the gold.

Design doc: [`docs/superpowers/plans/2026-08-18-prod-ask-quality-suite.md`](../../docs/superpowers/plans/2026-08-18-prod-ask-quality-suite.md).

## What is here

| File | What it is |
|---|---|
| `cases.jsonl` | 16 cases. One JSON object per line, schema in `case.schema.json`. |
| `case.schema.json` | Generated from `app.eval.ask_route.case.ProdAskCase`. Regenerate after a model change. |
| `oracle/*.sql` | Hand-authored answer-key SQL over the `mcp_local` base tables. Never the semantic layer the module compiles against. |
| `oracle-results.jsonl` | Executed answer key, one row per oracle case, with the sha256 of the SQL that produced it. |
| `judge-canary.jsonl` | Pinned answers with agreed scores. A judged run whose canary drifts by more than one point is void. |

## Running it

```bash
# CI layer: real route, scripted model, fixture store. Zero spend.
python scripts/prod_ask_eval_run.py --mode stub

# Scorer tests, including the mutation tests that prove the gold bites.
pytest tests/eval/test_prod_ask_checks.py tests/eval/test_prod_ask_suite.py \
       tests/eval/test_prod_ask_judge.py

# Paid layer. Refuses without --confirm-spend.
python scripts/prod_ask_eval_run.py --mode live --confirm-spend --record
```

## The case set

| id | question is about | principal | route | dimensions | pair |
|---|---|---|---|---|---|
| pa-01 | business card pricing | sales | semantic | accuracy, citations, rich_text, follow_ups, helpfulness | pa-02 |
| pa-02 | business card pricing | store | semantic | accuracy, citations, follow_ups | pa-01 |
| pa-03 | supplier balances | accountant | semantic | accuracy, citations, rich_text, follow_ups, helpfulness | pa-04 |
| pa-04 | supplier balances | sales | semantic | accuracy, citations, follow_ups | pa-03 |
| pa-05 | salary bands | press | semantic | accuracy, citations, follow_ups | — |
| pa-06 | working hours | no permissions | semantic | accuracy, citations, rich_text, follow_ups, helpfulness | — |
| pa-07 | leave allowances | no permissions | semantic | accuracy, citations, rich_text, helpfulness | — |
| pa-08 | corporate discount | sales | semantic | accuracy, citations, follow_ups | — |
| pa-09 | rush surcharges | sales | semantic | citations, rich_text | — |
| pa-10 | damaged delivery | store | semantic | accuracy, citations, follow_ups, helpfulness | — |
| pa-11 | fire exits | no permissions | semantic | citations, follow_ups | — |
| pa-12 | services offered | no permissions | semantic | citations, rich_text | — |
| pa-13 | Q1 2026 job count | press | structured | accuracy, rich_text, helpfulness | — |
| pa-14 | monthly invoicing H1 2026 | accountant | structured | accuracy, rich_text, helpfulness | — |
| pa-15 | five busiest customers | sales | structured | accuracy, rich_text, follow_ups, helpfulness | — |
| pa-16 | June 2026 revenue | accountant | structured | accuracy, rich_text, helpfulness | — |

Four permission-varied questions run twice (pa-01/pa-02, pa-03/pa-04). The same
question asked by a different principal has a different expected outcome: the
withheld tier's literals must not appear, and the withheld tier's chunks must
never be retrieved.

## Where truth comes from

* **Document cases (pa-01 … pa-12)** — the corpus file named in
  `accuracy.oracle.corpus_file`, at the heading named in `locator`. A test
  (`test_required_corpus_facts_are_really_in_the_cited_document`) reads the
  document and asserts every pinned literal is genuinely there, so a typo in
  gold fails the suite rather than the model.
* **Structured cases (pa-13 … pa-16)** — `oracle/<case>.sql`, executed
  read-only against the frozen `mcp_local` snapshot and recorded in
  `oracle-results.jsonl`. The SQL is written against base tables
  (`bills`, `bill_items`, `work_orders`, `customers`, `journals`, `accounts`),
  never against the `ai_v1_bq_*` views the Business Query module compiles to.

## Data facts worth knowing before you edit a case

* The snapshot ends **2026-07-15**. Questions must land inside it. The cases
  here use **July 2025 to June 2026**.
* `bills.payable` is **zero for every row** in this snapshot. Invoice value is
  derived from `bill_items` as `(price * quantity - discount)` plus tax on that
  net, which is how the billing app derives it.
* `customer_orders` only starts **2026-01-06**. A question about orders before
  that date has no answer.
* `journals` contains 88 rows with impossible dates (year 24, year 5251). Any
  ledger question must bound `post_date` on both sides.
* pa-15 has a tie at ranks four and five (47 jobs each) but rank six is at 42,
  so the top-five **set** is unambiguous even though the order inside it is
  not. The case is scored on set membership.

## Changing a case

Do not silently edit a question. Record the change here with the reason, the
way `evals/business_query/v2/README.md` does, and update the case's `grounding`
and the oracle SQL header comment to match. Re-run the oracle and commit the
new `oracle-results.jsonl`, then re-run the integrity tests.

## Changelog

| Date | Case | Change | Why |
|---|---|---|---|
| 2026-08-18 | pa-14 | money column switched from `bills.payable` to a `bill_items` expression | `payable` sums to zero across the whole snapshot |
| 2026-08-18 | pa-05 | dropped the `AED 10,000` forbidden literal | the document writes the band as `AED 6,000 – 10,000`, so the literal never appears |
| 2026-08-18 | pa-03 | fixture chunk extended to all five supplier lines | the two zero-balance suppliers must be present for the "no zero balance presented as owed" claim to be falsifiable |
