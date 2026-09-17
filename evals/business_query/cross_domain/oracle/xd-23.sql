-- xd-23 units of Art Papers 300 gsm used this year on jobs for customers invoiced this year
SELECT ROUND(SUM(wi.quantity), 2) AS product_units_used
FROM work_order_items wi JOIN work_orders w ON w.id = wi.work_order_id JOIN products p ON p.id = wi.product_id
WHERE w.entity_id = 1 AND p.name = 'Art Papers 300 gsm - (Matt) 64x90'
  AND wi.created_at >= '2026-01-01' AND wi.created_at < '2026-07-16'
  AND w.customer_id IN (SELECT b.customer_id FROM bills b WHERE b.entity_id = 1 AND b.type = 'Invoice'
                        AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16')
