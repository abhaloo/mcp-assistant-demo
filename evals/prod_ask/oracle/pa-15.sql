-- pa-15: five busiest customers by job count, 2025-07-01 to 2026-06-30 (entity 1).
SELECT c.name        AS customer_name,
       COUNT(w.id)   AS jobs_count
FROM work_orders w
JOIN customers c ON c.id = w.customer_id
WHERE w.entity_id = 1
  AND w.created_at >= '2025-07-01'
  AND w.created_at <  '2026-07-01'
GROUP BY c.name
ORDER BY jobs_count DESC, c.name ASC
LIMIT 5
