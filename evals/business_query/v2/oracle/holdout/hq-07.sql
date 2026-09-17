-- hq-07 genuine_zero_count: stock receipts into a named store.
-- The store exists and is flagged as able to receive, but carries ZERO inventory
-- movements of any type in the whole snapshot, so the count is genuinely 0 rather
-- than absent. Distinct root cause from hq-06 on purpose: a single bug must not be
-- able to fail both cases.
-- Approved definition (stock_receipts_recorded): every type='Receive' inventory row,
-- regardless of approval status.
SELECT COUNT(*) AS stock_receipts
FROM inventories i
JOIN ware_houses w ON w.id = i.ware_house_id
WHERE i.entity_id = 1
  AND i.type = 'Receive'
  AND TRIM(w.name) = 'Spare Parts'
