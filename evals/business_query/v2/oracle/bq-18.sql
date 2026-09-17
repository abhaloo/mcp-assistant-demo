-- bq-18 honest_empty_result: jobs created in August 2026.
-- Snapshot data ends 2026-07-15, so the true answer is 0 — empty, not an error.
SELECT COUNT(*) AS jobs_in_august
FROM work_orders
WHERE entity_id = 1 AND created_at >= '2026-08-01' AND created_at < '2026-09-01'
