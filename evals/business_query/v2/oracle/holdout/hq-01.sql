-- hq-01 supplier_partial_name_match: how many stock receipts came from the supplier
-- whose stored name the user typed only a fragment of.
-- Approved definition (stock_receipts_recorded): every type='Receive' inventory row,
-- regardless of approval status. No period is named, so the window is all time.
-- The fragment matches exactly ONE supplier row, and every inventory row for that
-- supplier is a Receive, so the answer cannot drift with the type filter.
SELECT COUNT(*) AS stock_receipts
FROM inventories i
JOIN suppliers s ON s.id = i.supplier_id
WHERE i.entity_id = 1
  AND i.type = 'Receive'
  AND s.name LIKE '%JAMANA%'
