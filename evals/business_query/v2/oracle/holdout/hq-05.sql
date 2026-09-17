-- hq-05 relative_period_last_month: invoices issued in the month before the business
-- date (2026-07-15), i.e. the whole of 2026-06.
-- That month sits entirely inside the snapshot, so month-to-date and whole-month
-- resolve to the same set and the answer cannot turn on which one is meant.
SELECT COUNT(*) AS invoices_issued
FROM bills
WHERE entity_id = 1
  AND type = 'Invoice'
  AND created_at >= '2026-06-01' AND created_at < '2026-07-01'
