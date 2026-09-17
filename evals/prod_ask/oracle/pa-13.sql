-- pa-13: how many jobs were created in Q1 2026 (entity 1)?
-- Base table only; never the semantic layer the module compiles against.
SELECT COUNT(*) AS jobs_created
FROM work_orders
WHERE entity_id = 1
  AND created_at >= '2026-01-01'
  AND created_at <  '2026-04-01'
