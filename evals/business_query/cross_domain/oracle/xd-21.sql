-- xd-21 average jobs per customer order for orders created this year (jobs on those orders / orders)
SELECT j.jobs_count, o.orders_count, ROUND(j.jobs_count / o.orders_count, 3) AS jobs_per_order
FROM (SELECT COUNT(*) AS jobs_count FROM work_orders w JOIN customer_orders co ON co.id = w.customer_order_id
      WHERE co.entity_id = 1 AND co.created_at >= '2026-01-01' AND co.created_at < '2026-07-16') j,
     (SELECT COUNT(*) AS orders_count FROM customer_orders co
      WHERE co.entity_id = 1 AND co.created_at >= '2026-01-01' AND co.created_at < '2026-07-16') o
