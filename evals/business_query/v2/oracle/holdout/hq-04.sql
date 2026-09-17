-- hq-04 explicit_month_and_year_past: jobs opened in an explicitly named past month.
-- Counted on creation, matching the question's wording (jobs OPENED). No status
-- filter: the owner rule about excluding FINISHED/CANCELLED applies to "where is this
-- job now" questions, not to a creation count over a closed historical window.
SELECT COUNT(*) AS jobs_opened
FROM work_orders
WHERE entity_id = 1
  AND created_at >= '2025-11-01' AND created_at < '2025-12-01'
