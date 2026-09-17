-- xd-07 customers with both a customer order and an invoice this year
SELECT COUNT(*) AS customers_count
FROM customers c
WHERE c.entity_id = 1
  AND c.id IN (SELECT co.customer_id FROM customer_orders co WHERE co.entity_id = 1
               AND co.created_at >= '2026-01-01' AND co.created_at < '2026-07-16')
  AND c.id IN (SELECT b.customer_id FROM bills b WHERE b.entity_id = 1 AND b.type = 'Invoice'
               AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16')
