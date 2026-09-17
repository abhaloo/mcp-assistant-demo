-- xd-05 outstanding balance per customer for the top 3 customers by jobs created this year
-- (39, 30, 30 jobs; the 4th has 28, so the boundary is not a tie)
SELECT c.name AS customer_name, ROUND(SUM(o.outstanding), 2) AS bill_outstanding
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
  WHERE b.entity_id = 1 AND b.type = 'Invoice') o JOIN customers c ON c.id = o.customer_id
WHERE o.customer_id IN (
  SELECT customer_id FROM (
    SELECT w.customer_id, COUNT(*) n FROM work_orders w
    WHERE w.entity_id = 1 AND w.created_at >= '2026-01-01' AND w.created_at < '2026-07-16'
    GROUP BY w.customer_id ORDER BY n DESC, w.customer_id ASC LIMIT 3) t)
GROUP BY c.id, c.name ORDER BY c.name
