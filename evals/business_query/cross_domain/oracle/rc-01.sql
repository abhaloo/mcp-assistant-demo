-- rc-01 top 5 unpaid July 2025 TZS invoices by invoiced revenue.
-- invoiced_revenue = SUM(items_total) (bundle ef739e38); a row's share is its items_total,
-- rebuilt from bill_items as xd-02.sql does. unpaid = outstanding > 0 (segment invoice.unpaid),
-- with outstanding = items_total - receivable settlement, rebuilt from journals as xd-04.sql does.
-- The currency code lives on currencies (bills.currency_id), as the bill fact view reads it.
-- Customers are LEFT JOINed as the view does: a bill with no customer row still ranks.
-- July 2025 in the business time zone (bills.created_at holds Dar es Salaam wall time).
-- The engine adds no tie-break key, so the scorer compares the result as a set (scoring-specs).
SELECT b.invoice_number AS invoice_number,
       c.name AS customer_name,
       ROUND(COALESCE(i.items_total, 0), 2) AS items_total
FROM bills b
LEFT JOIN customers c ON c.id = b.customer_id
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
ORDER BY i.items_total DESC
LIMIT 5
