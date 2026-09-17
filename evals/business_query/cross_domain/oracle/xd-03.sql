-- xd-03 invoices raised this year to customers whose record was created this year
SELECT COUNT(*) AS invoices_count
FROM bills b JOIN customers c ON c.id = b.customer_id
WHERE b.entity_id = 1 AND b.type = 'Invoice'
  AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16'
  AND c.created_at >= '2026-01-01' AND c.created_at < '2026-07-16'
