-- xd-anchor-03 latest 10 suppliers and their receipts; suppliers with none stay
SELECT s.name AS supplier_name, i.id AS receipt_id
FROM (
  SELECT id, name FROM suppliers
  WHERE entity_id = 1
  ORDER BY id DESC
  LIMIT 10
) s
LEFT JOIN inventories i ON i.entity_id = 1 AND i.supplier_id = s.id AND i.type = 'Receive'
ORDER BY s.name, i.id
