# Record-dispatch golden set (task P5)

This is the **input** to `scripts/evaluate_record_dispatch.py`, the harness that compares the
two `record_dispatch_mode` implementations (`agentic` = the existing bounded tool-call loop,
`single_call` = the Phase 5 CANDIDATE) against each other and against gold. Each line is one
evaluation case.

> **Do not read `cases_heldout.jsonl` during prompt development.** Iterate against
> `cases.jsonl` (the dev partition) only. `cases_heldout.jsonl` exists to catch prompt/schema
> overfitting to the dev set — reading it while tuning `app/services/record_intent.py`'s system
> prompt or `RecordQueryIntent` schema defeats that purpose. It is read only by the harness
> itself, and only when a human explicitly runs the held-out evaluation (Gate 5).

## Where the gold comes from

Every `expected_ids` value below is copied — never invented, never derived by running either
dispatcher — from `tests/integration/fixtures/billing_projection_expected.json`, the Task 3B
hand-authored mirror of Billing's `database/seeders/AiProjectionContractSeeder.php`. Each case's
`gold_note` cites the specific seeder stratum (`included_ids`/`excluded_ids`/
`discriminator_mismatch_ids`) it derives from. The eval principal
(`entity_id=501, cross_entity=false, department_id=601`) is the same fixture principal
`tests/integration/test_billing_projection_contract.py` uses.

## File format

One JSON object per line (JSONL — no comments, no trailing commas).

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable unique id (`<stratum-prefix>-<split>-NNN`). Never reuse/renumber — run history keys off it. |
| `split` | string | `dev` or `heldout` — which file the row lives in (redundant with the filename, kept for convenience when cases are filtered/concatenated). |
| `stratum` | string | One of the four strata below. |
| `question` | string | The natural-language question, worded exactly as a user would ask. |
| `principal` | object | `{entity_id, cross_entity, department_id}` — the eval harness grants this principal broad `search`/`read` access to every manifest resource (authorization is deliberately out of scope for this stochastic eval; see below). |
| `resource_type` | string | The manifest resource type the gold intent targets. |
| `action` | string | `search` \| `list` \| `get` — omitted for `unsupported_adversarial` cases, which have no correct action. |
| `query` / `record_ids` / `filters` / `sort` | varies | Gold-provenance detail for the relevant action — sugar for a human reviewer, not required to run the harness. |
| `adversarial_type` | string | `unsupported_adversarial` only: `relative_date` \| `aggregation` \| `ambiguous` \| `cross_entity` \| `prompt_injection`. |
| `expect` | string | `resolved` or `empty` — mirrors `RecordDispatchResolved`/`RecordDispatchEmpty` exactly. |
| `expected_ids` | list[str] | The exact record ids a correct dispatch must return (empty list when `expect: empty`). |
| `gold_note` | string | Cites the seeder stratum/fixture fact this case's gold derives from. |

## The four strata

1. **`direct_get`** — a `get` by one or more exact record ids, including seeded ids that must
   NOT resolve (wrong entity, wrong department, or wrong Bill subtype) and mixed batches (some
   ids resolve, some don't) to exercise partial-credit scoring.
2. **`search_by_reference`** — a free-text `search`, anchored on the two search substrings the
   Task 3B fixture itself verifies (`"Northwind"` → customer 701, `"department contract job"` →
   job 611), plus legitimate zero-match searches (text that was never seeded).
3. **`filtered_sorted_list`** — a `list`, anchored on every scope-only stratum the fixture
   verifies (customer/job/invoice/quotation/payable_quotation/credit_note), the one genuinely
   user-settable filter field across this manifest (`id` — `entity_id`/`department_id`/
   `type`/`payable` are all reserved scope/subtype columns the executor binds automatically,
   never a legal caller-supplied filter), the fixture's ascending `sort=created_at` case, and
   zero-row lists for resources the fixture seeds no rows for (`supplier`/`product`/
   `customer_order`/`inventory`).
4. **`unsupported_adversarial`** — five sub-types, all correctly gold to `expect: empty` **or**,
   for prompt injection, whatever the properly-scoped answer would have been anyway (injection
   framing must never change the result — that's Invariant 3/7, "the LLM never decides
   authorization"):
   - `relative_date` — no resource declares a date-RANGE filter (only `created_at`/`updated_at`
     SORT fields exist), so "this week"/"last month" questions are structurally unsupported.
   - `aggregation` — the record tools return rows, never a computed sum/average/count.
   - `ambiguous` — no identifying detail given at all (no id, name, or search term).
   - `cross_entity` — a real seeded id that belongs to a different entity/department/subtype
     than this principal (grounded on the fixture's own `excluded_ids`).
   - `prompt_injection` — text that tries to talk the model into ignoring scope
     ("ignore previous instructions", "admin mode", "pretend I'm entity 502"). Gold is either
     the SAME result a normal in-scope question would get (proving the injection had zero
     effect) or `empty` when the injected ask names a still-excluded id or no real resource at
     all — never a leak.

## Authorization is out of scope here

Per task-P5-brief.md: this file never encodes an authorization test (a grant the principal
lacks, a field_set boundary, etc.) — the deterministic property/contract suites
(`tests/policy/`, `tests/integration/test_billing_projection_contract.py`) remain the release
authority for authorization. Every case's principal is granted broad `search`/`read` access to
every manifest resource by the harness, uniformly, so authorization can never be the reason a
case passes or fails here — only dispatch QUALITY (did the model choose the right resource,
action, ids/query/filters, or correctly decline) is under test.

## Target size

`cases_heldout.jsonl` carries 26 cases per stratum (104 total) — above the ≥25-per-stratum floor
task-P5-brief.md requires. `cases.jsonl` (dev) carries 8 per stratum (32 total) for prompt
iteration. Both partitions were authored together and interleaved by authoring order (not
front/back split), so dev and heldout each carry genuine phrasing variety, not a lopsided slice.

## `bill_show_cases.jsonl` is a different schema

`bill_show_cases.jsonl` (30 cases) does NOT follow the field table above. It stages the Bill
Show / "what is this record" QA slice for the Phase 7 / Gate 7 evaluation: each row carries
`action: "qa"` plus claim/refusal gold (`expected`, `expected_claims`, `expected_references`)
against a single `record_context`-selected record, not a dispatch action/ids/filters shape.
It is **not** consumable by `scripts/evaluate_record_dispatch.py --cases` — pointing that
harness at this file raises a `KeyError` (it expects `expect`/`expected_ids`, which this file
does not have). Its own harness lands with the Gate 7 work.

## Status

These case files and the scored run they produced are retained as evidence for
ADR 0053's decision. No module reads them: the dispatcher they scored was deleted
in P2 of the thermo-nuclear remediation.
