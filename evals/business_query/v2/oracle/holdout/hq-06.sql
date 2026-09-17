-- hq-06 honest_empty_list: orders from the named year that are still open.
-- Customer orders in this snapshot begin in 2026, so NO order of any status exists in
-- that year and the honest answer is an empty list, not an error and not a count.
-- Open follows the approved definition (open_customer_orders): stored status NOT IN
-- ('FINISHED','CANCELLED'). The result is empty under every reading of "open", so the
-- case isolates period handling rather than status handling.
SELECT co.order_number
FROM customer_orders co
WHERE co.entity_id = 1
  AND co.status NOT IN ('FINISHED', 'CANCELLED')
  AND co.created_at >= '2025-01-01' AND co.created_at < '2026-01-01'
ORDER BY co.order_number
