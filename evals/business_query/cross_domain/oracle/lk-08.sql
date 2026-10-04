-- lk-08 (admin) xd-06's full 36-row expansion: page 2 (rows 21-36 in the product's order) must draw its supplier ids
-- from this set, share none of page 1's receipt ids, and keep the Supplier anchors.
SELECT s.id AS supplier_id, s.name AS supplier_name, i.id AS receipt_id
FROM (
  SELECT id, name FROM ai_v1_bq_supplier_fact
  WHERE entity_id = 1
  ORDER BY id DESC
  LIMIT 10
) s
LEFT JOIN ai_v1_bq_inventory_fact i ON i.entity_id = 1 AND i.supplier_id = s.id AND i.type = 'Receive'
ORDER BY s.id DESC, i.id
