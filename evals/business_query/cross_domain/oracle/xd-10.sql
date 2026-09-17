-- xd-10 same as xd-09 for this month and last month (June 2026)
SELECT DATE_FORMAT(b.created_at, '%Y-%m') AS month, ROUND(SUM(i.items_total), 2) AS invoiced_revenue
FROM bills b JOIN (SELECT bi.bill_id,
        SUM((bi.price * bi.quantity - bi.discount)
            + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
   FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id
WHERE b.entity_id = 1 AND b.type = 'Invoice'
  AND b.created_at >= '2026-06-01' AND b.created_at < '2026-08-01'
  AND b.customer_id IN (SELECT co.customer_id FROM customer_orders co
    WHERE co.entity_id = 1 AND (CASE WHEN co.status = 'CANCELLED' THEN 'CANCELLED'
     WHEN co.status = 'FINISHED' THEN 'FINISHED'
     WHEN EXISTS (SELECT 1 FROM work_orders wo WHERE wo.customer_order_id = co.id) THEN 'IN_PROGRESS'
     ELSE 'NEW' END) IN ('NEW','IN_PROGRESS'))
GROUP BY 1 ORDER BY 1
