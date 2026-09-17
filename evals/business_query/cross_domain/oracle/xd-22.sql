-- xd-22 products both received into stock (approved receipts) and sold on invoices this year
SELECT p.name AS product_name
FROM products p
WHERE p.id IN (SELECT ii.product_id FROM inventory_items ii JOIN inventories i ON i.id = ii.inventory_id
               WHERE i.entity_id = 1 AND i.type = 'Receive' AND i.status = 'APPROVED'
               AND i.created_at >= '2026-01-01' AND i.created_at < '2026-07-16')
  AND p.id IN (SELECT bi.product_id FROM bill_items bi JOIN bills b ON b.id = bi.bill_id
               WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16')
ORDER BY p.name
