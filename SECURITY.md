# Security Policy

This repository is a generated snapshot of the AI Operations Assistant demo.

## Reporting a vulnerability

Report security issues privately through this repository's [Security tab](https://github.com/abhaloo/mcp-assistant-demo/security). Select **Report a vulnerability** to use GitHub private vulnerability reporting.

Do not open a public issue with exploit details.

## Scope notes

- The Ask API requires a valid HS256 service JWT (`Authorization: Bearer <token>`) for `POST /api/ask`, `POST /api/ask/v2`, `POST /api/cancel`, `POST /api/feedback`, `POST /api/query-record/timing`, and `POST /api/restore-evidence`.
- Each service JWT is valid for 30 seconds, and its `jti` can be used only once.
- The health and readiness endpoints (`GET /healthz` and `GET /readyz`) do not require a JWT. The release-metadata endpoint (`GET /capabilities`) also does not require a JWT.
- Never commit `.env` files or live credentials.
