# Security Policy

This repository is a generated snapshot of the MCP Assistant Demo product.

## Reporting a vulnerability

Please report security issues privately:

- Open a [GitHub private security advisory](https://github.com/advisories) when the repo is on GitHub
- Or email the maintainer directly (do not open a public issue with exploit details)

## Scope notes

- All API routes require a valid HS256 JWT (`Authorization: Bearer …`).
- The demo database lives at `data/mcp_demo_v2.sql.gz`.
- Never commit `.env` or live credentials.
