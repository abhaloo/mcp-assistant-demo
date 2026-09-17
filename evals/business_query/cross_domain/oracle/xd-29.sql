-- xd-29 this month's invoices with the display status of the order they belong to (NULL = direct invoice)
SELECT b.invoice_number, b.created_at, ROUND(i.items_total, 2) AS items_total,
       CASE WHEN co.id IS NULL THEN NULL ELSE CASE WHEN co.status = 'CANCELLED' THEN 'CANCELLED'
     WHEN co.status = 'FINISHED' THEN 'FINISHED'
     WHEN EXISTS (SELECT 1 FROM work_orders wo WHERE wo.customer_order_id = co.id) THEN 'IN_PROGRESS'
     ELSE 'NEW' END END AS order_status
FROM bills b JOIN (SELECT bi.bill_id,
        SUM((bi.price * bi.quantity - bi.discount)
            + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
   FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id LEFT JOIN customer_orders co ON co.id = b.customer_order_id
WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.created_at >= '2026-07-01' AND b.created_at < '2026-08-01'
ORDER BY b.created_at, b.id
