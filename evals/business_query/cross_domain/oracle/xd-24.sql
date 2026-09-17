-- xd-24 open customer orders whose customer has an invoice more than 30 days past due with money outstanding
SELECT co.order_number, c.name AS customer_name, CASE WHEN co.status = 'CANCELLED' THEN 'CANCELLED'
     WHEN co.status = 'FINISHED' THEN 'FINISHED'
     WHEN EXISTS (SELECT 1 FROM work_orders wo WHERE wo.customer_order_id = co.id) THEN 'IN_PROGRESS'
     ELSE 'NEW' END AS computed_status
FROM customer_orders co JOIN customers c ON c.id = co.customer_id
WHERE co.entity_id = 1 AND (CASE WHEN co.status = 'CANCELLED' THEN 'CANCELLED'
     WHEN co.status = 'FINISHED' THEN 'FINISHED'
     WHEN EXISTS (SELECT 1 FROM work_orders wo WHERE wo.customer_order_id = co.id) THEN 'IN_PROGRESS'
     ELSE 'NEW' END) IN ('NEW','IN_PROGRESS')
  AND co.customer_id IN (SELECT o.customer_id FROM (SELECT b.id AS bill_id, b.customer_id, b.due_date, b.created_at,
        COALESCE(i.items_total,0) - COALESCE(s.settlement,0) AS outstanding
   FROM bills b LEFT JOIN (SELECT bi.bill_id,
        SUM((bi.price * bi.quantity - bi.discount)
            + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
   FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id LEFT JOIN (SELECT j.bill_id,
        SUM(j.debit) AS settlement,
        SUM(CASE WHEN a.account_type IN ('CASH','BANK') THEN j.debit ELSE 0 END) AS cash
   FROM journals j JOIN accounts a ON a.id = j.account_id
  WHERE j.debit > 0 AND a.account_type IN ('OPERATING_EXPENSE','PAYABLE','CASH','BANK')
  GROUP BY j.bill_id) s ON s.bill_id = b.id
  WHERE b.entity_id = 1 AND b.type = 'Invoice') o
                         WHERE o.outstanding > 0 AND o.due_date IS NOT NULL AND DATEDIFF('2026-07-15', o.due_date) > 30)
ORDER BY co.order_number
