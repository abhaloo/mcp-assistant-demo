-- 2025 TZS invoiced revenue per calendar month, rebuilt from bills and bill_items
-- as rc-01.sql rebuilds items_total; bills.created_at holds Dar es Salaam wall time.
SELECT DATE_FORMAT(b.created_at, '%Y-%m') AS month,
       ROUND(SUM(COALESCE(i.items_total, 0)), 2) AS invoiced_revenue,
       COUNT(*) AS invoices
FROM bills b
JOIN currencies cur ON cur.id = b.currency_id
LEFT JOIN (SELECT bi.bill_id,
                  SUM((bi.price * bi.quantity - bi.discount)
                      + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
           FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id
WHERE b.entity_id = 1 AND b.type = 'Invoice' AND cur.currency_code = 'TZS'
  AND b.created_at >= '2025-01-01 00:00:00' AND b.created_at < '2026-01-01 00:00:00'
GROUP BY DATE_FORMAT(b.created_at, '%Y-%m')
ORDER BY invoiced_revenue DESC
LIMIT 3;
