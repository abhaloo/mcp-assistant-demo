-- hq-09 money_total_over_period: total invoiced across the current business year.
-- Snapshot data stops 2026-07-15, so year-to-date and the full calendar year are the
-- same window and the answer cannot turn on which one is meant.
-- Approved definition (invoiced_revenue / bill_amount): SUM(items_total), where
-- items_total is TERM BY TERM per item,
--   (price*qty - discount) + (price*qty - discount) * tax/100
-- Re-derived from base tables, not read from any view. Snapshot is TZS-only, so a
-- per-currency breakdown carries this same single amount.
SELECT ROUND(SUM((bi.price * bi.quantity - bi.discount)
                 + (bi.price * bi.quantity - bi.discount) * bi.tax / 100), 2) AS invoiced_total
FROM bills b
JOIN bill_items bi ON bi.bill_id = b.id
WHERE b.entity_id = 1
  AND b.type = 'Invoice'
  AND b.created_at >= '2026-01-01' AND b.created_at < '2027-01-01'
