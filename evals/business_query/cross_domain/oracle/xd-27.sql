-- xd-27 per invoice month, Jan-Jun 2025: invoiced revenue and cash received against those invoices
-- (cash is attributed to the invoice's month, not the receipt date; payment recording stops 2025-08-28)
SELECT DATE_FORMAT(b.created_at, '%Y-%m') AS month,
       ROUND(SUM(i.items_total), 2) AS invoiced_revenue, ROUND(SUM(COALESCE(s.cash, 0)), 2) AS cash_received
FROM bills b JOIN (SELECT bi.bill_id,
        SUM((bi.price * bi.quantity - bi.discount)
            + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
   FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id LEFT JOIN (SELECT j.bill_id,
        SUM(j.debit) AS settlement,
        SUM(CASE WHEN a.account_type IN ('CASH','BANK') THEN j.debit ELSE 0 END) AS cash
   FROM journals j JOIN accounts a ON a.id = j.account_id
  WHERE j.debit > 0 AND a.account_type IN ('OPERATING_EXPENSE','PAYABLE','CASH','BANK')
  GROUP BY j.bill_id) s ON s.bill_id = b.id
WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.created_at >= '2025-01-01' AND b.created_at < '2025-07-01'
GROUP BY 1 ORDER BY 1
