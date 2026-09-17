-- xd-11 AR aging (as of 2026-07-15) for customers with a job IN PROGRESS
SELECT CASE WHEN DATEDIFF('2026-07-15', o.due_date) <= 0 THEN 'Current/not due'
            WHEN DATEDIFF('2026-07-15', o.due_date) <= 30 THEN '1-30'
            WHEN DATEDIFF('2026-07-15', o.due_date) <= 60 THEN '31-60'
            WHEN DATEDIFF('2026-07-15', o.due_date) <= 90 THEN '61-90'
            ELSE '91+' END AS bucket,
       COUNT(*) AS invoices_count, ROUND(SUM(o.outstanding), 2) AS bill_outstanding
FROM (SELECT b.id AS bill_id, b.customer_id, b.due_date, b.created_at,
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
WHERE o.outstanding > 0 AND o.due_date IS NOT NULL
  AND o.customer_id IN (SELECT w.customer_id FROM work_orders w WHERE w.entity_id = 1 AND w.status = 'IN PROGRESS')
GROUP BY 1 ORDER BY 1
