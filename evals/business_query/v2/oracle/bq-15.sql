-- bq-15 customers_with_no_orders: HOW MANY customers have never ordered.
-- Corrected twice. First version counted while the question asked "which ones",
-- so it was changed to a list. That list was the alphabetical first 20 of 597 —
-- unmeetable, because nothing in the question pins an ordering and no plan may
-- return more than 50 rows. Scored on `total_count` instead: the module must
-- report 597 as its total and show some of them.
SELECT COUNT(*) AS customers_never_ordered
FROM customers cu
WHERE cu.entity_id = 1
  AND NOT EXISTS (SELECT 1 FROM customer_orders co WHERE co.customer_id = cu.id)
