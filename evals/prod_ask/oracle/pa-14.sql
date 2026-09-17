-- pa-14: invoices raised per month, January to June 2026 (entity 1).
-- Six rows with two shared measures, so the answer must render a table.
-- Invoice value is derived from bill_items exactly as the billing app derives
-- it: (price * quantity - discount) plus tax on that net. bills.payable is
-- zero throughout this snapshot and must not be used as the money column.
SELECT DATE_FORMAT(b.created_at, '%Y-%m')      AS month,
       COUNT(DISTINCT b.id)                    AS invoices_count,
       ROUND(SUM((bi.price * bi.quantity - bi.discount)
                 + (bi.price * bi.quantity - bi.discount) * bi.tax / 100), 2) AS invoiced_total
FROM bills b
JOIN bill_items bi ON bi.bill_id = b.id
WHERE b.entity_id = 1
  AND b.type = 'Invoice'
  AND b.created_at >= '2026-01-01'
  AND b.created_at <  '2026-07-01'
GROUP BY DATE_FORMAT(b.created_at, '%Y-%m')
ORDER BY month
