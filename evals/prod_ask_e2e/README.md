# prod-ask-e2e — durable browser suite for the Ask AI panel

> **Run it with `/ai-e2e`** (`.claude/skills/ai-e2e/`). That skill owns the pipeline —
> preflight, adversarial review of the suite, answer-key stripping, execution, validity
> review, grading, diagnosis — plus the dispatch prompts and the model tiers.
>
> **This suite is the living one for the Ask surface. Extend it; do not replace it.**
> A new feature adds cases and a `revision_history` line. Existing oracles and checks change
> only when a reviewer proves one wrong, and the reason gets logged. Cases persisting across
> runs is the whole mechanism by which a regression shows up as a diff rather than a surprise.

Thirty-two cases across five permission personas, graded on RBAC, answer accuracy, UI/UX,
rich-text rendering, SQL-route citations, follow-up chips, page context, helpfulness, and
concurrency.

This is the browser counterpart to `evals/prod_ask/`, which exercises the same route
through a stub token. That suite proves the service behaves; this one proves the **product**
does — the panel a user actually clicks, with a real session, real permissions, and the
department scope the Laravel app mints.

## The one idea worth understanding

Every authorization case is **paired**, and each pair must land opposite ways:

| pair | proves |
|---|---|
| `rb-01` (7) / `rb-02` (6) / `rb-03` (6113) | one question, three callers, three different correct answers — the department predicate is derived from the caller, not hardcoded |
| `rb-07` allow / `rb-08` deny | the printing document tier filters by caller |
| `rb-09` allow / `rb-10` deny | the mirror — so no fixed allow-list explains both |
| `rb-11` deny / `rb-12` allow | the confidential payroll document is gated, and the gate isn't just a broken retriever |

A single-sided check is close to worthless here. "Printing cannot read the payroll document"
passes just as cleanly when the entire document route is down — which is not hypothetical:
the first time this suite was prepared, the container had zero embeddings, and the three
DENY halves would all have passed. The positive half is what gives the negative half meaning,
and the preflight is what stops you grading a dead index as a working policy.

## Layout

```
suite.json           cases, oracles, checks, release gates      <- the answer key
run-plan.json        generated; same cases with the answer key removed
executor-brief.md    contract for the browser agent
grader-brief.md      contract for the grading agent
tools/derive_oracles.sql   re-derives every SQL oracle from the views
```

`scripts/eval/make_prod_ask_e2e_run_plan.py` generates `run-plan.json` from `suite.json`.

The executor never sees `suite.json`. The generator strips the outcome-bearing keys —
including `title` and `expected_route`, because "Capability denial - sales has no view job"
*is* the answer — then derives the forbidden tokens **from the suite itself** and fails if
one survives. An earlier version hardcoded seven tokens and stripped neither field; a cold
review pulled the ALLOW/DENY answer key straight out of the generated plan. Deriving the
list is the difference between catching the leaks you thought of and catching the ones you
didn't.

## Running it

```bash
# 0. Environment up (RAG API + billing app + MySQL)
pwsh -NoProfile -File <migration-factory>/env.ps1 pair -Slug <slug>

# 1. Seed the personas (idempotent)
php multicolor-billing/scripts/seed-department-test-users.php

# 2. PREFLIGHT — every check in suite.json § preflight. Do not skip.
#    Corpus mounted, index non-empty, embeddings alive, oracles current.

# 3. Regenerate the executor's plan
python scripts/eval/make_prod_ask_e2e_run_plan.py

# 4. Dispatch the executor (cheap/fast model) with executor-brief.md + run-plan.json
#    + the test password. It writes the evidence bundle and returns NO verdict.

# 5. Dispatch the grader (strong model) with grader-brief.md + suite.json + the bundle.
```

**Step 2 is the step people will want to skip.** Two of its checks exist because the run
that preceded them would have burned ~25 live model calls to discover an environment
problem, and would have reported it as a product regression. Specifically:

- `/readyz` reports `chroma: true` from a *connectivity ping*, not a document count. An
  empty index looks healthy.
- `get_embeddings()` branches on `chat_provider`, not `embedding_provider`
  (`app/providers/factory.py:293`). With an Azure chat provider, embeddings go to Azure
  regardless of what `embedding_provider` says — so a broken Azure subscription takes out
  the document route while the SQL route keeps working perfectly.

Step 2 also re-derives the oracles, which are snapshots of live data in
`mcp_prod_wire_eval`. After any ingest or restore they go stale, and a stale oracle marks
correct answers wrong — which teaches you to ignore the suite. That is a worse outcome than
not running it.

## Evidence

Each run writes one folder under `docs/superpowers/evidence/<UTC-date>-prod-ask-e2e/`
per `.claude/skills/visual-judge/references/evidence-contract.md`: `test-results.json`,
`report.md`, `execution-notes.md`, `screenshots/`, `console.log`. The bundle must be
auditable by someone with no access to the conversation that produced it.

## Personas

| persona | user | dept | structured access | document tiers |
|---|---|---|---|---|
| `admin` | `test_admin` | *(none)* | all | all |
| `store` | `test_dept_store` | 1 STORE | invoice, job, inventory, quotation, journal, customer, product, supplier | all |
| `print` | `test_dept_printing` | 2 PRINTING PRESS | job only | all, customers, printing |
| `sales` | `test_dept_sales` | 3 SALES | invoice, inventory, customer, product, supplier — **no job** | all, customers, finance, graphic design, sales, warehouse |
| `tissue` | `test_dept_tissue` | 8 TISSUE | inventory only | all, customers, warehouse |

`test_admin` exists only for this suite. Every other seeded user carries a department, which
makes a correctly scoped answer and a never-scoped answer look identical. It is the only
caller with `department_id` NULL, so it is the only one that can tell those two apart.

`print` and `tissue` are deliberate mirrors: each can read a document tier the other cannot.
`sales` and `print` are mirrors on the structured side — `sales` has invoices but no jobs,
`print` has jobs but no invoices.

## What this suite does NOT cover

Stated rather than implied, because a suite that overstates its coverage is worse than a
smaller honest one. Full detail in `suite.json § coverage_gaps`:

1. **The entity predicate is never exercised.** Every fact view holds exactly one
   `entity_id` and all five personas belong to it. Deleting the entity `ForcedPredicate`
   would change no answer here.
2. **The follow-up chip permission gate is not browser-observable.** Chip *text* is
   question-driven, and the path where the gate fires is a denial — on which the UI
   suppresses chips anyway. Covered by `tests/services/test_follow_up_suggestions.py`.
3. **No attached page-context case.** Every case runs detached.
4. **Routing is inferred from phase labels**, which are advisory rather than a contract.

## Cost

A full run is ~26 live model calls plus five logins, against the real database.

## Known conditions

- **The document route is non-functional as of 2026-08-18**: every embedding call returns
  Azure `400 SubscriptionNotRegistered`. The seven document cases must be reported NOT RUN.
  Unblock with `az provider register --namespace Microsoft.CognitiveServices`.
- `GET /readyz` returns 503 on `business_query_resolver_coverage` — the manifest index
  accepts a legacy hash no bundle declares compatibility with. Confirmed pre-existing (no
  diff against `master`) and it does not gate the Ask panel.
