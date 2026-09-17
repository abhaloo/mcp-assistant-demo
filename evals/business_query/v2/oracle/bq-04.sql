-- bq-04 jobs_for_order: which jobs sit on order 744?
SELECT w.id AS job_id
FROM work_orders w
JOIN customer_orders co ON co.id = w.customer_order_id
WHERE w.entity_id = 1 AND co.order_number = '744'
ORDER BY w.id
