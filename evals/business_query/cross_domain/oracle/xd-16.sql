-- xd-16 stock issues booked this year to jobs of ZANZIBAR SERENA HOTEL (customer 77)
SELECT COUNT(*) AS stock_issues
FROM inventories i JOIN work_orders w ON i.work_order_id REGEXP '^[0-9]+$' AND CAST(i.work_order_id AS UNSIGNED) = w.id
WHERE i.entity_id = 1 AND i.type = 'Issue'
  AND i.created_at >= '2026-01-01' AND i.created_at < '2026-07-16'
  AND w.customer_id = 77
