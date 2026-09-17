-- xd-25 customer orders placed this year by the top 3 customers by invoiced revenue this year
-- (ties broken by customer id ascending, as the engine's ranked set does)
SELECT co.order_number, c.name AS customer_name, co.created_at
FROM customer_orders co JOIN customers c ON c.id = co.customer_id
WHERE co.entity_id = 1 AND co.created_at >= '2026-01-01' AND co.created_at < '2026-07-16'
  AND co.customer_id IN (
    SELECT customer_id FROM (
      SELECT b.customer_id, SUM(i.items_total) rev FROM bills b JOIN (SELECT bi.bill_id,
        SUM((bi.price * bi.quantity - bi.discount)
            + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
   FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id
      WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16'
      GROUP BY b.customer_id ORDER BY rev DESC, b.customer_id ASC LIMIT 3) t)
ORDER BY co.created_at, co.id
