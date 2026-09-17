# Executor brief — prod Ask AI E2E

You drive the browser and record what you see. **You do not decide whether an answer is
right.** A separate grader does that, from your evidence alone. Your job is to make that
possible: if the grader cannot reconstruct a case from your record, the case is lost.

You are not given the expected answers. That is deliberate — `run-plan.json` has them
stripped. Do not try to infer them, and never write "correct", "wrong", "as expected",
"correctly enforced", or a score anywhere. Those words are the grader's, not yours.

## Inputs

- `evals/prod_ask_e2e/run-plan.json` — personas, DOM selectors, ordered cases
- The shared test password — supplied in your dispatch message, not stored here

## Setup

1. App: `http://127.0.0.1:8158`. Log in with the persona's username and the supplied password.
2. Open the Ask AI panel: click `[data-mcp-ask-ai-toggle]` or press `Ctrl+L`.
3. If a page-context chip is showing (`[data-mcp-ask-ai-context]`) and the case says
   `page_context: detached`, remove it before sending. Ambient page context silently
   changes the route and makes the case untestable.
4. To switch persona, submit the logout form directly
   (`document.getElementById('topbarLogoutForm').submit()`). The logout button has been
   observed unresponsive to synthetic clicks.

## Per case

1. **Isolation.** `conversation: isolated` → click `[data-mcp-ask-new]` first and confirm
   the thread is empty. `conversation: reuse:<id>` → stay in that case's thread.
2. **Start the clock**, then type into `#mcp-ask-ai-input` and click
   `button[aria-label='Send question']`.
3. **Poll from the moment you send** — every ~250ms, not once at the end. See "Capture
   during streaming" below; two of the required fields cease to exist at terminal state.
4. The turn is done when streaming has stopped *and* the action row (`[data-copy]`,
   `[data-regenerate]`) has rendered. Never use a fixed sleep — it either truncates a slow
   answer or inflates every timing.
5. **Screenshot** the completed answer (see the path rule below).
6. **Keep going after a failure.** A broken case must not hide the ones after it.

## Capture during streaming — not after

Two fields are gone by the time the answer finishes:

- **`phases`** — `ol[aria-label='Answer progress']` is rendered ONLY while `msg.streaming`
  is true and is removed at terminal state. Read its `innerText` while polling. If you poll
  properly and it never appears, record `phases: []` and note it — that is a real finding.
  Never leave it `null` without having tried.
- **`timing_ms.first_status` / `first_content`** — both are transitions you can only see by
  watching. `first_content` is the first real answer text, not a progress label.

`phases` is the executor's main basis for `pipeline_observed`. Lose it and the grader
cannot evaluate routing for any case.

## Screenshots — use an absolute path

The browser tool resolves relative paths against **its own** working directory, not the
evidence folder, and has been observed scattering PNGs into a repository root. Always pass
the full absolute path:

```
<evidence-folder>/screenshots/<case-id>.png
```

One per case. This is a required deliverable, not a nice-to-have.

## What to record per case

Follow `.claude/skills/visual-judge/references/evidence-contract.md`. Per case:

| field | how |
|---|---|
| `question` | exactly as sent |
| `answer` | the **rendered** text, near-verbatim. Do not summarise or clean it up. |
| `answer_html` | `innerHTML` of that turn's `article[aria-label='AI answer']` |
| `phases` | labels from the progress list, in order (captured during streaming) |
| `record_links` | for every `a.mcp-ask-ai__inline-record` and every link in `.mcp-ask-ai__record-refs`: `{label, href}` |
| `citations` | every `[data-citation-marker]`; open one and record the card's type/title/snippet |
| `citation_note` | text of `.mcp-ask-ai__citation-note` if present |
| `suggestions` | text of every `.mcp-ask-ai__chip`, in order |
| `errors` | text of `.mcp-ask-ai__error`; whether `.mcp-ask-ai__try-again` is present |
| `timing_ms` | `first_status`, `first_content`, `complete` |
| `pipeline_observed` | `sql`, `documents`, `denied`, or `uncertain` — plus `pipeline_observation_basis` saying what in the UI proves it. **If nothing proves it, say `uncertain`.** A guess recorded as fact corrupts the routing analysis. |
| `screenshots` | paths actually written |

Use `null` only where you tried and could not capture. Never invent a value to fill a field.

## Cases needing extra steps

- **`links_resolve`** (any case with record links): open ONE link in a new tab, record the
  landing URL and whether it shows the record whose label was linked. Then close the tab.
  Do not click links that trigger a browser dialog. Zero links is a valid observation —
  record `record_links: []`.
- **`fu-02`**: click the first chip. Record whether it sent by itself or only filled the
  input box, and the `data-msg-id` of the message carrying the new chip set. Record the new
  answer as its own case record.
- **`ux-01`**: run its anchor case to completion, click `[data-mcp-ask-new]`, confirm the
  thread is visually empty, screenshot that empty state, then send the case question.
- **`ux-02`**: observational on its anchor case. Also dump the browser console for the whole
  run to `console.log` in the evidence folder.

## Output

Write into a single folder `docs/superpowers/evidence/<UTC-date>-prod-ask-e2e/`:

```
test-results.json     one record per case, in run order
execution-notes.md    deviations, retries, anything that made a case unrepresentative
screenshots/          <case-id>.png
console.log           browser console for the run
```

Write `test-results.json` **incrementally**, every 3–4 cases, so a crash near the end does
not lose the run.

Then report: cases attempted, cases with complete records, cases you could not run and why.
**No scores, no verdicts, no "looks correct".**

## Hard rules

- **Finish every case you were assigned.** Do not stop early because "the pattern is
  established" — a previous executor did exactly that and left 14 cases unrun.
- **A case marked `status: "not_run"` in the run plan is OUT OF SCOPE.** Do not attempt it,
  do not spend a model call on it; record it in your report as NOT RUN (out of scope) and
  move on. "Every case you were assigned" means every case WITHOUT that marker.
- **Claim only what you did.** Do not report a file as written without confirming it exists
  at the path you claim. A false completion claim is worse than an admitted gap, because it
  costs the grader a case it thinks it has.
- Never write the password into `test-results.json`, `execution-notes.md`, a screenshot, or
  your report.
- Never change application code, config, permissions, or the database to make a case pass.
  If a case cannot run as written, record that and move on — a case that only passes after
  you altered the system is worse than no case.
- Never re-send a question to get a "better" answer. If you re-run for an infrastructure
  reason (session dropped, panel failed to open), record both attempts and say why.
