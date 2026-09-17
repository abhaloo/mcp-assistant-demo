-- xd-26 jobs created this year that sit on a customer order already marked FINISHED
SELECT COUNT(*) AS jobs_count
FROM work_orders w JOIN customer_orders co ON co.id = w.customer_order_id
WHERE w.entity_id = 1 AND w.created_at >= '2026-01-01' AND w.created_at < '2026-07-16' AND co.status = 'FINISHED'
