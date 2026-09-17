# MCP Assistant Demo

Natural-language questions over a print-shop billing database, compiled through
versioned business-definition bundles, plus grounded document Q&A with citations.

- **Structured SQL analytics** — JWT-gated queries against MariaDB views (`data/mcp_demo_v2.sql.gz`).
- **Fail-closed access** — the same question can answer for `demo_finance` and refuse for `demo_production`.
- **Grounded document Q&A** — retrieve from the bundled handbook and cite sources.

## Architecture

A finance user asks who owes us, opens an invoice, then asks about that selected record.
This repository is the Ask API. The [record-links replay](docs/replays/demo-s2-who-owes-us.html) is the billing panel that calls it.

```mermaid
flowchart TB
  you[You]
  recCtx[Selected invoice]
  api["POST /api/ask"]
  you --> recCtx --> api

  jwt["Signed JWT: record_access includes document_tiers"]
  api --> jwt

  redis[Redis transcript]
  jwt --> redis

  subgraph lg [LangGraph coordinator]
    decide["Orchestrator (Decide)"]
    explain[explain_sources]
    clarify[Clarify]
    finish[Finish]
    stop[Stop]
    decide --> explain
    decide --> clarify
    decide --> finish
    decide -.-> stop
    explain --> decide

    subgraph bqLine [ ]
      direction LR
      bqTool["tool: sql_query_business_records"]
      maria[MariaDB]
      cube[Cube compiler]
      results[Results]
      bqTool --> maria --> cube --> results
    end

    subgraph docLine [ ]
      direction LR
      docTool["tool: search_documents"]
      chroma["ChromaDB (hybrid retrieval)"]
      docTool --> chroma
    end

    decide --> bqTool
    decide --> docTool
    results --> decide
    docTool --> decide
  end
  redis --> decide

  sse["SSE: activity, thought, table, card"]
  decide -.-> sse
  finish --> persist[Persist transcript]
  clarify --> persist
  persist --> json[JSON answer]
```

LangGraph `Orchestrator (Decide)` owns the turn. It calls `tool: sql_query_business_records`, `tool: search_documents`, or `explain_sources`, then loops until it finishes, clarifies, or stops.
`tool: sql_query_business_records` runs MariaDB through a Cube compiler to results. `tool: search_documents` retrieves from ChromaDB with hybrid retrieval. `explain_sources` restores cited evidence. It is not a catalog tool.
SSE frames go out while the loop runs. The transcript is written after the turn ends.
This demo sets `CONVERSATION_COORDINATOR_ENABLED=true`.

## Ask flow

```mermaid
sequenceDiagram
  participant U as You
  participant P as Billing Ask panel
  participant M as demo_mint_jwt.py
  participant A as /api/ask
  participant R as Redis
  participant G as Orchestrator (Decide)
  participant D as MariaDB or ChromaDB

  U->>M: mint JWT (--persona demo_finance)
  M-->>U: Bearer token
  U->>P: open unpaid invoices
  P->>A: POST question + JWT
  Note over P,A: Optional SSE uses Accept text/event-stream
  A->>A: verify record_access
  A->>R: load transcript
  A->>G: start Orchestrator (Decide) loop
  loop until finish, clarify, or stop
    G->>D: tool: sql_query_business_records and/or tool: search_documents
    D-->>G: Cube results or passages
    G-->>P: SSE activity and tables
  end
  alt selected record
    U->>P: open invoice 3377
    P->>A: POST with record_context
    A->>G: decide with that invoice in scope
  else coordinator follow-up
    G-->>P: clarify in the thread
    U->>P: next question
    P->>A: POST new_question
  else answer
    G-->>A: finish
    A->>R: persist transcript
    A-->>P: JSON answer
  end
```

You mint a JWT, then ask from the billing panel or from `/api/ask`.
If you open a row, the next question carries that record.
The coordinator can ask a follow-up in the same thread.

## Try these

| Question | Persona | Expected |
|---|---|---|
| How many invoices in July 2025? | `demo_finance` | **85** |
| What was the collection rate in July 2025? | `demo_finance` | **3.5%** |
| What was the collection rate in July 2025? | `demo_production` | refusal — no `3.5%` / figures |
| How many paid time off days do full-time staff accrue per year? | `demo_finance` | **18 days** (handbook) |

## Demo video

Open the [record-links replay](docs/replays/demo-s2-who-owes-us.html) in a browser.

It lists unpaid invoices as linked rows, opens invoice 3377, then answers on that selected record.
The replay is the billing Ask panel. This repository is the API behind it.

## Quick start

1. Clone this repository.
2. Copy `.env.example` to `.env` and set `MODEL_API_KEY` / `BASE_URL`.
3. Start the stack:

   ```powershell
   docker compose up --build --wait
   ```

4. Open interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)
5. Ingest the handbook corpus:

   ```powershell
   python scripts/deploy/ingest.py
   ```

6. Mint a JWT (`demo_mint_jwt.py` reads `.env` automatically):

   ```powershell
   python scripts/demo_mint_jwt.py --persona demo_finance
   ```

7. Health checks:

   ```powershell
   curl http://localhost:8000/healthz
   curl http://localhost:8000/readyz
   ```

8. Ask a structured question:

   ```powershell
   $TOKEN = python scripts/demo_mint_jwt.py --persona demo_finance
   curl -X POST http://localhost:8000/api/ask `
     -H "Authorization: Bearer $TOKEN" `
     -H "Content-Type: application/json" `
     -d "{\"question\":\"How many invoices in July 2025?\"}"
   ```

## Sample response screenshot

See `docs/sample-ask.PLACEHOLDER.txt` — a `/api/ask` capture will replace this after compose smoke.

## Contributing

This tree is a generated snapshot. Do not open feature PRs here.

## License

MIT — see [LICENSE](LICENSE).
