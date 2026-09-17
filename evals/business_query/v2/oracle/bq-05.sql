-- bq-05 order_for_job: which order does job 24692 belong to?
SELECT co.order_number
FROM work_orders w
JOIN customer_orders co ON co.id = w.customer_order_id
WHERE w.entity_id = 1 AND w.id = 24692
