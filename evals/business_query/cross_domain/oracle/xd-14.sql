-- xd-14 jobs on the most recent customer order
SELECT co.order_number, w.id AS job_id, w.work_number, w.status
FROM customer_orders co LEFT JOIN work_orders w ON w.customer_order_id = co.id
WHERE co.id = (SELECT c2.id FROM customer_orders c2 WHERE c2.entity_id = 1 ORDER BY c2.created_at DESC, c2.id DESC LIMIT 1)
ORDER BY w.id
