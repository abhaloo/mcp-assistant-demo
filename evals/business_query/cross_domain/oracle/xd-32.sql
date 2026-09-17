-- xd-32 jobs created this year on a FINISHED customer order, asked as a direct lookup (no set)
SELECT COUNT(*) AS jobs_count
FROM work_orders w JOIN customer_orders co ON co.id = w.customer_order_id
WHERE w.entity_id = 1 AND w.created_at >= '2026-01-01' AND w.created_at < '2026-07-16' AND co.status = 'FINISHED'
