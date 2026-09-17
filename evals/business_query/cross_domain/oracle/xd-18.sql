-- xd-18 units sold on invoice lines this year, per customer
SELECT c.name AS customer_name, SUM(bi.quantity) AS product_units_sold
FROM bill_items bi JOIN bills b ON b.id = bi.bill_id LEFT JOIN customers c ON c.id = b.customer_id
WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16'
GROUP BY c.id, c.name ORDER BY product_units_sold DESC, c.name
