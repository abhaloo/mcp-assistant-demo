-- xd-28 suppliers with at least one approved stock receipt this year
SELECT COUNT(*) AS suppliers_count FROM suppliers s
WHERE s.entity_id = 1 AND s.id IN (SELECT i.supplier_id FROM inventories i WHERE i.entity_id = 1 AND i.type = 'Receive'
      AND i.status = 'APPROVED' AND i.inventory_date >= '2026-01-01' AND i.inventory_date < '2026-07-16')
