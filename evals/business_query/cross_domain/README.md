# Cross-domain business-query eval

## Cube shadow

Bring Cube up with the factory compose for this slug, plus the cube overlay
and profile. Project name is `rag-<slug>` (see `env-run.json`). Overlay path
is `migration-factory/.agent-runs/<slug>/rag.cube.yml` — never commit it.

```
docker compose -p rag-analytics-w2 --profile cube -f docker-compose.yml -f <rag.override.yml> -f <rag.cube.yml> up -d
```

G1 probes. Cube publishes no host port; use `exec` for `/readyz`.

```
docker compose -p rag-analytics-w2 ps cube
docker inspect --format '{{index .RepoDigests 0}}' cubejs/cube:v1.7.18
docker compose -p rag-analytics-w2 exec cube node -e "fetch('http://localhost:4000/readyz').then(r=>r.text().then(t=>{console.log(t); process.exit(r.ok?0:1)})).catch(()=>process.exit(1))"
curl -s http://127.0.0.1:<api-port>/readyz
docker compose -p rag-analytics-w2 exec api python - <<'PY'
import json, os, time, httpx, jwt
url = os.environ["BUSINESS_QUERY_CUBE_URL"].rstrip("/") + "/meta?onlyViews=true"
token = jwt.encode({"cross_entity": True, "exp": int(time.time()) + 60}, os.environ["BUSINESS_QUERY_CUBE_API_SECRET"], algorithm="HS256")
body = httpx.get(url, headers={"Authorization": token}, timeout=10).json()
print(json.dumps([{"name": c["name"], "type": c.get("type"), "meta": c.get("meta")} for c in body["cubes"]], indent=2))
PY
```

Expect cube `healthy`; API `/readyz` `checks.cube: true` and
`effective_settings.cube_configured: true`; meta lists view `business` with
`meta.model_revision` equal to `deploy/cube/model/REVISION`.

## Harness

32 cases. Engine:

```
python scripts/eval/cross_domain_bench.py oracle
python scripts/eval/cross_domain_bench.py engine --database mcp_analytics_w2 --adapter chain --api-readyz http://localhost:8000/readyz
python scripts/eval/cross_domain_bench.py diff <cube> <internal> <chain>
```

Four `capability_gap` rows in `cases.jsonl`: xd-15, 16, 22 (bundle), xd-27
(granularity; D5(a) keeps it internal). G2 reports 15/16/22 as gaps and 27 as
the internal ignored-granularity result.

## Results (Task 8 remeasure, 20260918T000821Z cube / 000838Z internal / 000839Z chain)

| Gate | Observation |
| --- | --- |
| G2 cube | 8 pass (xd-01,02,08,17,18,30,31,32), not 9: xd-29 `wrong_answer` (timezone); xd-12 alt row_count 4 vs 7. Mixed xd-12/20 `engine_rule` (in_set). xd-13/14/23 `invalid_plan` (pick / nested set). xd-27 `capability_gap`. Timezone risk: DB session SYSTEM / America/Denver vs Cube Africa/Dar_es_Salaam CONVERT_TZ. |
| G2 internal | 20 pass. xd-01/31/32 `optional_join_unsupported`; xd-29 `fanout_unsafe`; xd-27 `wrong_answer` 460 vs 6. Same three `invalid_plan`. |
| G2 chain | 23 pass. Cube-only adapters all `cube`. Mixed: xd-12 `[cube, internal]`, xd-20 `[cube, internal]`, xd-21 `[internal, cube]`. xd-31 now passes via Cube (was internal-only before). |
| G3 | No bidirectional-join error. xd-29 LEFT JOIN order; xd-12 alt two-CTE multi-fact. Alt values not expected to match. |
| G4 | Container: 4 passed, 2 failed (orphan NULL status key; department join-through measure set mismatch). |
| G5 | p95 of 13 non-alt `cube_calls_ms` on runs 2–3 = 83.5 ms vs 1.0 s (D2). Live recording is 14 calls because xd-12 alt is timed (harness bug, not a p95 fail). |
| G6 | `g6_both_answered=4` ids xd-08,17,18,30 all `agree`. xd-02 both-pass but `disagree`. Forecast {02,08,17,18,30} is not the criterion. |
| G7 | 0 matches for 3 patterns (see `g7-grep.txt`). |
