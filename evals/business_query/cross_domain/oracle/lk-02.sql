-- lk-02 (admin, appended to xd-06) the 10 newest suppliers and their receipts, with both ids:
-- Supplier cells link /suppliers/show/{supplier_id}; receipt rows link /inventory/show/{receipt_id}.
SELECT s.id AS supplier_id, s.name AS supplier_name, i.id AS receipt_id
FROM (
  SELECT id, name FROM ai_v1_bq_supplier_fact
  WHERE entity_id = 1
  ORDER BY id DESC
  LIMIT 10
) s
LEFT JOIN ai_v1_bq_inventory_fact i ON i.entity_id = 1 AND i.supplier_id = s.id AND i.type = 'Receive'
ORDER BY s.id DESC, i.id
