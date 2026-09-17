-- hq-03 bare_month_current_year: customer orders received in a bare-named month.
-- Business date is 2026-07-15, so a month named without a year is that month in the
-- CURRENT business year. Resolving it to an earlier year is the failure this case
-- exists to catch; the same month in 2025 holds no orders at all.
SELECT COUNT(*) AS orders_received
FROM customer_orders
WHERE entity_id = 1
  AND created_at >= '2026-03-01' AND created_at < '2026-04-01'
