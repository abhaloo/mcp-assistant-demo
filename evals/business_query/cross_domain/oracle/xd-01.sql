-- xd-01 jobs per customer-order display status (jobs that sit on an order)
SELECT CASE WHEN co.status = 'CANCELLED' THEN 'CANCELLED'
     WHEN co.status = 'FINISHED' THEN 'FINISHED'
     WHEN EXISTS (SELECT 1 FROM work_orders wo WHERE wo.customer_order_id = co.id) THEN 'IN_PROGRESS'
     ELSE 'NEW' END AS computed_status, COUNT(*) AS jobs_count
FROM work_orders w JOIN customer_orders co ON co.id = w.customer_order_id
WHERE w.entity_id = 1
GROUP BY 1 ORDER BY 1
