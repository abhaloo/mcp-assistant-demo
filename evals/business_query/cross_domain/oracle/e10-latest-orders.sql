-- e10 the 20 most recent customer orders and their work orders.
-- Identity column: order_number (card members customer_order.order_number and
-- job.customer_order_number carry the same value).
-- The consistency gate compares the set of order numbers a painted table
-- holds with the set this returns, null order numbers dropped first. A
-- painted set anchored on customer orders may be a subset (a plan with a
-- smaller limit still answers "latest orders"); a set anchored on jobs is
-- only required to be non-empty.
SELECT co.order_number, co.created_at, wo.work_number, wo.status
FROM customer_orders co
LEFT JOIN work_orders wo ON wo.customer_order_id = co.id AND wo.entity_id = co.entity_id
WHERE co.entity_id = 1
  AND co.id IN (SELECT id FROM (SELECT id FROM customer_orders
                                WHERE entity_id = 1 ORDER BY created_at DESC LIMIT 20) latest)
ORDER BY co.created_at DESC, wo.work_number
