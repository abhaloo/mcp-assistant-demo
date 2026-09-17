# Business Query sealed holdout — read this, not the cases

**This file is safe to read while you are tuning. The other three are not.**

| file | what it holds | safe to open while tuning? |
|---|---|---|
| `cases.holdout.jsonl` | 12 questions + expected outcomes | **no** |
| `oracle/holdout/*.sql` | hand-written SQL, one per data-backed case | **no** |
| `oracle-results.holdout.jsonl` | the executed answer key | **no** |
| `HOLDOUT-README.md` (this) | shape only: mechanisms, counts, discipline | yes |

## The sealing rule

The holdout exists to answer one question: **did the fixes driven by `cases.dev.jsonl`
generalise, or were they fitted to those twenty questions?**

It can only answer that while nobody tuning the system has seen it. So:

- Do not read the questions, the oracle SQL, or the key while you are changing
  `app/business_query/`, the planner prompt, or the definition bundle.
- Do not quote holdout text in a plan, a commit message, an insights-log entry, or a
  prompt. If a phrase from it reaches production code, that case is dead.
- Run it **once**, at the end, when the dev suite is finished and frozen. A holdout you
  re-run after every change is just a second dev suite.
- If you do read it, say so in the run report. A burned holdout that is declared is
  recoverable; one that is quietly reused is not.

## What it covers

Twelve cases, `hq-01` … `hq-12`, one per mechanism. Each mechanism is one the dev suite
already drove a fix for, re-tested with **different wording, different periods, different
records and different names** than the dev suite uses.

| case | mechanism | oracle |
|---|---|---|
| hq-01 | partial-name matching on a stored supplier name | data-backed |
| hq-02 | human-facing record number quoted by a user, vs the database id | data-backed |
| hq-03 | bare month name with no year → current business year | data-backed |
| hq-04 | explicit month + year in the past | data-backed |
| hq-05 | relative period resolved against the business date | data-backed |
| hq-06 | a list question that genuinely matches nothing → honest empty | data-backed |
| hq-07 | a count that is genuinely zero (zero is data, absence is not) | data-backed |
| hq-08 | grouped "per X" breakdown | data-backed |
| hq-09 | money total over a period | data-backed |
| hq-10 | a question that must be refused as unsupported | behaviour only |
| hq-11 | a genuinely ambiguous question that must ask for clarification | behaviour only |
| hq-12 | a permission-restricted question (`principal_override`) | behaviour only |

**9 data-backed, 3 behaviour-only.** Behaviour-only cases have no answer key by design:
refusing honestly, asking, and denying have no number to check, so `score_case` judges
them on outcome alone.

Language mix: **4 natural Kiswahili, 3 code-switched, 5 English** (2 clear, 3 noisy —
missing punctuation, typos, abbreviations). The Kiswahili deliberately avoids the
vocabulary the planner prompt's own glossary teaches; where the glossary lists a word,
the holdout uses a synonym or a different construction. A holdout that reused the
glossary's words would prove only that the glossary works.

Every case is its own intent family, so `holdout_resampling_unit: intent_family` and
case-level resampling are the same thing here — 12 strata, one case each. There is no
`paraphrase-families.holdout.json`; nothing would be in it.

## How the answer key was built

- Every answer is derived **independently, from the base tables**, by hand-written SQL —
  never from the module's compiler, its generated SQL, the semantic views' business
  logic, or by running the module. The key is able to disagree with the module, which is
  the only reason it is worth having.
- Business definitions were reimplemented from the approved registry in the billing repo
  (`app/Authorization/BusinessQuery/BusinessDefinitionRegistry.php`), term by term.
- Before any question was committed, its SQL was run and checked for the three failure
  modes the dev suite hit on 2026-08-11: no ties that make a "top N" arbitrary, no result
  larger than the 50-row plan cap, exactly one unambiguous answer. Where a period could
  be read two ways (month-to-date vs whole month, year-to-date vs whole year), the
  question was chosen so the snapshot makes both readings identical — the answer cannot
  turn on that choice.
- **No bulk customer data in the key.** The largest committed result is a handful of rows;
  no customer names, no balance lists.

Re-derive it (read-only; the runner refuses anything that is not a single `SELECT`/`WITH`):

```bash
./.venv/Scripts/python.exe scripts/business_query_oracle.py --holdout
```

Re-running produces a byte-identical file. Current key:

```
oracle_hash sha256:942e2f262c98062e7e78158b4c384564dffffe1021af394a91643ffdfd49969d
```

Fill that into `frozen_inputs.holdout_oracle_hash` in the success contract when the
holdout is frozen, together with the hash of `cases.holdout.jsonl`.

## Two judgement calls worth knowing about

**1. `hq-12` expects `denied`, matching the dev suite's convention.** On the current
`LlmPlanner` path a restricted principal is most likely to surface as `unsupported`
instead: the capability card omits members the principal cannot see, and the planner's
own card gate returns `Unsupported(member_not_found)` *before* `apply_role_scope` can
raise `ScopeDenied`. The label follows dev deliberately — a holdout must encode the same
contract as the suite it generalises from, and a case both arms fail the same way costs a
little denominator without biasing the delta. **If you relabel dev's permission case,
relabel this one to match.** What the case really catches either way is the outcome that
must never happen: `answered`.

**2. The holdout is deliberately NOT wired into the phrase-contamination guard** in
`tests/business_query/test_production_contamination.py`. That guard prints the matched
phrases when it fails, which would spill holdout wording into the terminal of whoever is
tuning — the exact leak the seal exists to prevent. The questions were instead scanned
by hand against `app/`, `billing:app/Authorization/BusinessQuery` and
`billing:database/ai-business-definitions` at build time: zero three-word phrases in
common. If you want that check automated, make the assertion report **file and count
only**, never the phrase.

## Data notes

Built against the frozen snapshot `mcp_eval` (read-only; ends **2026-07-15**; TZS only;
`entity_id = 1`), business date **2026-07-15**, via a disposable passthrough database
that has since been dropped. Recreate one with:

```bash
./.venv/Scripts/python.exe scripts/business_query_eval_db.py create --target mcp_bq_eval_holdout
```

`mcp_local`, `mcp_eval` and `multicolor-db` are protected and must never be written to.
