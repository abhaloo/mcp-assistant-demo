-- xd-19 invoices this year that carry the product 'Business Cards'
SELECT COUNT(DISTINCT b.id) AS invoices_count
FROM bills b JOIN bill_items bi ON bi.bill_id = b.id JOIN products p ON p.id = bi.product_id
WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16'
  AND p.name = 'Business Cards'
