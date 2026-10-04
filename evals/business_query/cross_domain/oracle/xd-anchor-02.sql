-- xd-anchor-02 latest 10 customers and their orders; customers with none stay
SELECT c.name AS customer_name, co.order_number
FROM (
  SELECT id, name FROM customers
  WHERE entity_id = 1
  ORDER BY id DESC
  LIMIT 10
) c
LEFT JOIN customer_orders co ON co.entity_id = 1 AND co.customer_id = c.id
ORDER BY c.name, co.order_number
