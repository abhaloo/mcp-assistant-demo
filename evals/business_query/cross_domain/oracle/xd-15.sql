-- xd-15 last approved stock receipt date per supplier (suppliers with at least one)
SELECT s.name AS supplier_name, MAX(i.inventory_date) AS last_receipt_date
FROM inventories i JOIN suppliers s ON s.id = i.supplier_id
WHERE i.entity_id = 1 AND i.type = 'Receive' AND i.status = 'APPROVED'
GROUP BY s.id, s.name ORDER BY s.name
