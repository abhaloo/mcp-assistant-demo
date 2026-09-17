# Execution Notes - Prod Ask AI E2E (Successful Backend Run)

## Run Details
- Date: 2026-08-26
- Persona: adeel
- Billing App: http://127.0.0.1:8158
- RAG API: http://127.0.0.1:8358 (Ready: true, all health checks pass)

## Observations

### Case 1: sample-01 ('How many jobs do we have?')
- Sent: 'How many jobs do we have?'
- Answer rendered: 'jobs_count: 6113'
- Trust badge: 'Verified Query'
- Trust drawer / Support reference: aq_9cdc17c8a985870b63538a48540e337d, plain English explanation: 'Summary metrics calculated from matching business records.', count: '1 of 1 matched'.
- Follow-up suggestions: ['Show the most recent jobs']
- Action controls: Copy, feedback up/down, regenerate rendered at terminal state.
- Timing: first_content: 56ms, complete: 6681ms.

### Case 2: sample-02 ('Who are our top customers?')
- Sent: 'Who are our top customers?'
- Answer rendered: 'Top customers by which measure—revenue, invoice amount, or order count?'
- Follow-up suggestions: ['Show the most recent customers']
- Action controls: Copy, feedback up/down, regenerate rendered at terminal state.
- Timing: first_content: 64ms, complete: 8522ms.

### Case 3: sample-03 ('Show invoices for Acme')
- Sent: 'Show invoices for Acme'
- Answer rendered: 'No matching rows.'
- Trust badge: 'Verified Query'
- Trust drawer / Support reference: aq_1b235474c25bc6f76af70e85de317d6b, plain English explanation: 'Showing invoices for customer Acme.', filters: 'Customer: Acme', count: '0 of 0 matched'.
- Follow-up suggestions: ['Show the most recent invoices']
- Action controls: Copy, feedback up/down, regenerate rendered at terminal state.
- Timing: first_content: 54ms, complete: 8642ms.

## Screenshots
- screenshots/sample-01.png
- screenshots/sample-02.png
- screenshots/sample-03.png
