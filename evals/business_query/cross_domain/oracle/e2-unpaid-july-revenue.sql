-- E2 invoiced revenue of unpaid July 2025 TZS invoices (the bare-revenue default).
-- invoiced_revenue = SUM(items_total) (bundle ef739e38), rebuilt from bill_items as
-- rc-01.sql does; unpaid = outstanding > 0 with the rc-01 settlement rule; July 2025
-- in the business time zone (bills.created_at holds Dar es Salaam wall time).
SELECT ROUND(SUM(COALESCE(i.items_total, 0)), 2) AS invoiced_revenue
FROM bills b
JOIN currencies cur ON cur.id = b.currency_id
LEFT JOIN (SELECT bi.bill_id,
                  SUM((bi.price * bi.quantity - bi.discount)
                      + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
           FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id
LEFT JOIN (SELECT j.bill_id, SUM(j.debit) AS settlement
           FROM journals j JOIN accounts a ON a.id = j.account_id
           WHERE j.debit > 0 AND a.account_type IN ('OPERATING_EXPENSE', 'PAYABLE', 'CASH', 'BANK')
           GROUP BY j.bill_id) s ON s.bill_id = b.id
WHERE b.entity_id = 1 AND b.type = 'Invoice' AND cur.currency_code = 'TZS'
  AND COALESCE(i.items_total, 0) - COALESCE(s.settlement, 0) > 0
  AND b.created_at >= '2025-07-01 00:00:00' AND b.created_at < '2025-08-01 00:00:00'
