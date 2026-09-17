-- bq-09 stock_receipts_recorded, Main Store, July 2026.
-- Repointed from Tissue Store: it has ZERO 'Receive' rows in the entire snapshot.
-- Main Store is the only warehouse with receipts (109, latest 2026-07-01).
-- Approved definition: all type='Receive' rows regardless of approval status;
-- time dimension is inventory.inventory_date, not created_at.
SELECT COUNT(*) AS stock_receipts
FROM inventories i
JOIN ware_houses wh ON wh.id = i.ware_house_id
WHERE i.entity_id = 1
  AND i.type = 'Receive'
  AND TRIM(wh.name) = 'Main Store'
  AND i.inventory_date >= '2026-07-01' AND i.inventory_date < '2026-08-01'
