# Business Query eval suite v2 — your review

**What this is:** 20 draft questions, one per business topic, for measuring how often the
module gets things right. I wrote them; you make them sound like your people.

**What I need from you:** read the `question` field on each line of `cases.dev.jsonl` and
change anything that doesn't sound real. Everything else in the file is mine to maintain —
ignore it.

Rough time: 10 minutes. Nothing else is blocked on it except the run itself.

## How the questions were grounded

Every question points at something that actually exists in the frozen snapshot
(`mcp_eval`, 4,830 invoices / 6,113 jobs / 812 customers, data ending **2026-07-15**):

- **Customers:** Sea Cliff Resort & Spa (224 jobs), Zanzibar Serena Hotel (147), The Residence Hotel
- **Departments:** TISSUE, LARGE FORMAT, PRINTING PRESS, GRAPHICS, INDIGO-DIGITAL MACHINE — and the
  genuinely misspelled `CUTTING DEPARTMNET ` (trailing space included)
- **Products:** `Art Papers 300 gsm - (Matt) 64x90`, `NCR yellow-CF-55GSM 61x86`
- **Real order/job pairs:** order 744 → jobs 24693 + 24694; job 24692 → order 743
- **Stores:** Main Store, Tissue Store, Large Format Store, Finished Goods, Spare Parts

**Business date is 2026-07-15**, not today. The snapshot stops mid-July, so "this month" and
"leo" have to mean July or every answer is zero.

## The 20 questions

| id | topic | style | question |
|---|---|---|---|
| bq-01 | customer's last order | noisy | when did sea cliff last order from us |
| bq-02 | Serena's last tissue order | mixed | serena hotel last tissue order ilikuwa lini? |
| bq-03 | last tissue job | Kiswahili | kazi ya mwisho ya tissue tulifanya lini? |
| bq-04 | jobs on an order | clear | which jobs are on order 744? |
| bq-05 | order for a job | noisy | whats the order number for job 24692 |
| bq-06 | open orders count | Kiswahili | tuna oda ngapi ambazo bado hazijakamilika? |
| bq-07 | jobs by department | mixed | nionyeshe jobs breakdown kwa kila department |
| bq-08 | product usage | noisy | how much art paper 300gsm did we use this month |
| bq-09 | stock receipts | clear | how many stock receipts were recorded for the Tissue Store this month? |
| bq-10 | overdue total | noisy | how much are we still owed on invoices more than a month past due? |
| bq-11 | receivables aging | clear | show me the receivables aging buckets as of today |
| bq-12 | invoiced revenue | mixed | mwezi huu tume-invoice kiasi gani kwa jumla? |
| bq-13 | ledger revenue | clear | what was our ledger revenue for july? |
| bq-14 | cash actually received | Kiswahili | wiki hii tumepokea pesa kiasi gani hasa? |
| bq-15 | customers who never ordered | Kiswahili | kuna wateja gani ambao hawajawahi kuagiza kitu? |
| bq-16 | biggest debtor (vague) | noisy | who owes us the most money |
| bq-17 | bare "revenue" (vague) | clear | what's our revenue? |
| bq-18 | a genuinely empty answer | mixed | tulifanya jobs ngapi mwezi wa nane? |
| bq-19 | can't be answered | mixed | who's order is ready for delviery leo? |
| bq-20 | must be refused | Kiswahili | nionyeshe ripoti ya mapato ya kampuni nzima |

Five of each style: plain English, messy English, Kiswahili, and mixed.

## Things I'd especially like your eye on

1. **bq-19 is your sentence, verbatim** — typos and all (`who's`, `delviery`). Tell me if you
   want it cleaned up or kept raw. I'd keep it raw.
2. **Does anyone actually write Kiswahili into a search box?** If your staff type English and
   only *speak* Kiswahili, then bq-03/06/14/15/20 are testing something that never happens, and
   I should shift the balance toward mixed instead.
3. **"oda" or "order"?** I've used "oda" in bq-06. Borrowed words are where written register
   goes wrong most easily.
4. **bq-08 and bq-16 are deliberately vague** — I expect the system to ask a clarifying question
   rather than answer. If you'd rather it just picked a sensible default, say so; that changes
   what counts as correct.
5. **Names:** I used real company names, following the existing suite which already ships
   "Zanzibar Serena Hotel". No individual people, emails, or phone numbers. Say if even company
   names should be swapped out.

## What happens after you edit

1. I write the true answer for each question — twice over: counted by hand from the data, and
   as a small SQL file that recomputes it. If the two ever disagree, we get told. (This is the
   option B you picked.)
2. I write the scorer and do a free local dry run.
3. I show you the exact commit and hashes, you say go, and the real run costs about 5p.

## Notes for me, not you

- Snapshot has **no semantic views** — the run needs a disposable clone with views created, same
  protocol as the canary. `mcp_eval` / `mcp_local` are read-only and must never be written to.
- Snapshot is **TZS-only** and has **no ACTIVE orders / no DELIVERED jobs**, so the multi-currency
  family and the N-1 ACTIVE fix aren't exercisable here — those need the seeded oracle DB.
- `EVAL_SNAPSHOT_DATABASE_URL` resolves to `mcp_eval`, but the runbook mandates `/mcp_local`.
  Unreconciled; flagged.

## Changes made while deriving the answer key

The data forced four questions to move. Each is recorded in the case's `grounding`
field and in the SQL header comment:

| case | change | why |
|---|---|---|
| bq-07 | added "kwa sasa" (right now) | Owner rule: job questions exclude FINISHED/CANCELLED unless asked |
| bq-09 | Tissue Store → **Main Store** | Tissue Store has **zero** stock receipts in the whole snapshot |
| bq-14 | "this week" → **March 2025** | Cash/bank postings stop 2025-08-28; every recent window is structurally zero |
| bq-02, bq-03 | department route → **invoice-line route** | Tissue appears only on invoice lines (161), never on work-order items (0 of 4,435) |

**15 of 20 answers are data-backed.** The other five (bq-08, bq-16, bq-17, bq-19, bq-20)
test behaviour — asking a clarifying question, refusing honestly, denying access — where
"correct" is what the system *does*, not a number it returns.

### Data facts worth knowing regardless of this eval

- Payment recording stops **2025-08-28** while invoicing runs to 2026-07-15. This is why
  **2,494 of 3,520 invoices** show as 30+ days overdue, with **5.42bn TZS** sitting in the
  91+ aging bucket. Almost certainly a recording gap, not a collections collapse.
- **88 journal entries carry impossible dates** — one in year 24, one in year 5251.
- The **department field records workflow stage, not department**: 1,026 of 1,197 active jobs
  sit in `DELIVERED&CHARGED`; the TISSUE department holds 4 jobs company-wide.
- `Serena tissue box facial 1x150pcs` is stored **twice**, with differing stray whitespace.

### Cross-checks that passed

- bq-04 returned exactly jobs 24693 + 24694, matching a separate direct query.
- bq-15's anti-join (597) matches 812 total − 215 distinct customers with orders.
- bq-11's aging buckets sum to **2,494** across 31-60/61-90/91+ — exactly bq-10's
  independently written overdue query. Two different queries, same number.


## Changes made on 2026-08-11 (second pass)

Two cases were unmeetable as written — no correct answer could have passed them —
and both were rewritten rather than left to score the module unfairly.

**bq-10** used to ask *which* invoices are most overdue. 91 invoices tie at the
maximum 1,184 days past due, so "the most overdue" has no single correct row set,
and a plan may return at most 50 rows against 2,494 matches. It now asks for the
total still owed, which tests the same two definitions (what counts as overdue,
what counts as outstanding) and has exactly one right answer.

**bq-15** keeps its question — 597 customers really have never ordered — but is
now scored on `total_count`: the module must report 597 as its total and show
some of them. The previous key was the alphabetical first 20 of 597, and nothing
in the question pins an alphabetical order, so any other correct 20 failed.

A side effect worth naming: the old keys held 2,494 rows of live receivables and
20 customer names, all committed to git. The answer key file went from 70KB to
3.4KB, and no customer names or balances remain in it.

## scoring-specs.jsonl provenance

Frozen 2026-08-14 (`reviewed_by: oracle-to-bundle-mapping`): maps oracle columns
onto current cube member names. sha256 `a4cc923c…` is the exact file used by the
2026-08-14 paid diagnostic runs (`openai-luna-xhigh-paid`, `deepseek-direct-max-paid`).

`cases.dev.jsonl` sha256 `054d34626e1db751520fd7f49fc09fdd33aeabc011532d8b9d9affd91a9f80f5`
is a drift tripwire only (not an equality test). Pre-edit hash was
`4c72730af20699dc6ece3feb1a6c683b41a49a26af807975c5263a9ed6cdb567`.

## bq-07 diagnostic exclusion

bq-07 stays in this file for gold/history and prompt-leak guards. `eval_status:
excluded` drops it from `_select_run_cases` until gold reapproval (jobs_count vs
active_jobs_count; oracle deny-list vs measure allow-list). Owner 2026-08-14.
