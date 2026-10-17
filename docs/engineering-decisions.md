# Engineering decisions

This page lists the changes we measured before keeping or dropping them. Each result comes from the private development repo. That repo holds the frozen test suites, the run files and the full write-ups.

## How we measure

- **One change per run.** Each comparison changes one thing: a prompt, a model, a setting or a code path. Everything else stays the same.
- **Frozen suites.** The cases, expected answers and scoring code are fixed before the run.
- **Development and held-out sets.** We tune against the development set. The held-out set is only run to check that a gain carries over. Rows say which set they use.
- **Paired confidence intervals.** For accuracy, we compare the two arms case by case and report a 95% bootstrap interval. A gain counts only when the interval stays above zero.
- **Predictions written first.** For the larger experiments, we wrote down the expected result and the pass bar before the run.
- **Repeats.** Most cases run 3 or 5 times, so one lucky answer does not decide a result.
- **Negative results stay in.** A change that does not clear its bar is reverted, and it stays on this page.

Model names are the ones in use on the date of each run.

## Kept in the current design

| Date | Decision | Setup | Before | After | Caveat |
|---|---|---|---|---|---|
| 2026-07-07 | Split the handbook by markdown headings | 62 retrieval cases | Right passage in top 5: 14 of 62 (0.23). Citation precision 0.69. Recall@5 0.92 | 46 of 62 (0.74). Citation precision 0.79. Recall@5 0.94 | One run per arm, development corpus |
| 2026-10-05 | Input screen: per-question thresholds on TypeSafe Jev, instead of a keyword rule | 48 held-out attacks, 156 normal messages | Keyword rule caught 25 of 48. AUC 0.76 | Caught 45 of 48. AUC 0.999. Normal messages flagged: 0 of 156 in both | One attack class (fake data fields) caught 1 of 4. A code rule added later caught all 16 in a new set |
| 2026-10-11 | Keep TypeSafe Jev over the OpenAI Decisions API for screening | 786 calls per arm, alternating order | OpenAI: 31 of 48 attacks blocked, 8 of 12 multi-turn attacks, p50 151 ms, $0.00016 per turn | Jev: 39 of 48, 11 of 12, p50 91 ms, $0.000055 per turn | The questions were written and tuned for Jev over three rounds. OpenAI got them as they were |
| 2026-08-17 | Planner reasoning effort "high", not "xhigh" (accuracy) | 19 cases, 3 repeats per arm | xhigh: 39 of 57 passed | high: 39 of 57 passed, the same cases | xhigh used 64% more reasoning tokens for the same result. Prediction written before the run |
| 2026-09-02 | Planner reasoning effort "high" (latency) | Two-plan question, 2 runs per arm | xhigh: median 22.1 s, both runs over the 18 s planner limit | high: median 12.6 s, both under | 2 runs per arm |
| 2026-10-01 | Move the coordinator and planner to GPT-6 Luna | 8 cases, 3 repeats per arm (24 turns) | Cost per answered turn $0.0044. 23 of 24 answered | $0.0022. 23 of 24 answered | p90 latency rose from 20.0 s to 29.3 s. Arms ran an hour apart, not interleaved |
| 2026-08-29 | New Ask panel instead of the old one | Same 48-case suite, only the feature flag changed | p90 17.8 s. 2 confidently wrong numbers | p90 12.5 s. 0 confidently wrong numbers | The overall score fell from 5.25 to 4.92, because some panel features were not connected yet |

## Tried and dropped

| Date | Change | Setup | Result | Decision |
|---|---|---|---|---|
| 2026-10-10 | Send the planner a shortlist of tables, not the full catalog | 18 cases, 3 repeats, 2 arms | Prompt tokens fell 40%. Passes stayed at 36 of 36. Latency gain not shown: 90% lower bound -889 ms against a +500 ms target | Stopped at the first gate |
| 2026-10-01 | DeepSeek V4.1 Flash for every model call on the Ask path | 24 turns per arm | Answered 17 of 24, against 23 of 24. Cost per answered turn rose 43% | Not adopted |
| 2026-07-07 | Rerank retrieved passages | 62 retrieval cases | Recall@5 fell from 0.935 to 0.903 (interval -0.081 to 0.000) | Off |
| 2026-05-20 | Nine chunk size and splitter settings | 62 retrieval cases, bootstrap intervals | No setting's interval cleared zero. The corpus was too small to separate them | Kept the default, retest on a larger corpus |

## Earlier architecture, since replaced

Before the semantic layer, the assistant used a LangChain SQL agent that wrote SQL directly, on gpt-4o-mini. That code is not in this repository. These results shaped the current design.

| Date | Decision | Setup | Before | After | Caveat |
|---|---|---|---|---|---|
| 2026-06-24 | Stop the SQL agent from making parallel tool calls (one line) | 39 development cases, 5 repeats | p95 89.1 s. 3.52 attempts per question | p95 25.8 s. 1.11 attempts. Tokens down 28%. Accuracy change +0.036 (interval 0.000 to +0.092) | Held-out accuracy flat |
| 2026-06-27 | Send payment-status questions to the stronger model | 44 development and 23 held-out cases, 5 repeats | Development 0.572, held-out 0.713 | Accuracy +0.083 on development (interval +0.020 to +0.161), +0.109 on held-out (+0.009 to +0.246) | Share of questions sent to the stronger model rose from 9% to 16% |
| 2026-06-25 | Put the full schema in every prompt | Development and held-out sets, 5 repeats | Held-out 0.714 | Held-out 0.600: -11.4 points (interval -0.286 to 0.000). Cost up 18% on development, 80% on held-out | Reverted |
| 2026-06-25 | Use gpt-4.1 for every question | Development and held-out sets, 5 repeats | gpt-4o-mini | Held-out +8.6 points, but the interval crossed zero (-0.086 to +0.257), at 5.8 times the cost | Kept the small model. Hard questions are escalated instead |

## What is not published, and why

- **Test cases and run files.** The cases include real staff questions and real business records, so they stay private.
- **The development corpus.** It differs from the demo handbook in this repository.
- **Latency.** Latency was measured on one development machine against hosted model APIs. Your numbers will differ.
