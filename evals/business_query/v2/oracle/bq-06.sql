-- bq-06 open_customer_orders_count.
-- Approved definition (BusinessDefinitionRegistry open_customer_orders): stored
-- status NOT IN ('FINISHED','CANCELLED') — open-world, so ACTIVE counts as open.
SELECT COUNT(*) AS open_orders
FROM customer_orders
WHERE entity_id = 1 AND status NOT IN ('FINISHED', 'CANCELLED')
