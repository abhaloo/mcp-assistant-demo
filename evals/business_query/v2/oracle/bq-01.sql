-- bq-01 customer_last_order: when did Sea Cliff last order?
-- Definition: latest customer_order created_at for that customer, entity-scoped.
SELECT MAX(co.created_at) AS last_order_at
FROM customer_orders co
JOIN customers cu ON cu.id = co.customer_id
WHERE co.entity_id = 1
  AND TRIM(cu.name) = 'Sea Cliff Resort & Spa'
