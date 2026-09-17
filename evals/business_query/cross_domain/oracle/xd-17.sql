-- xd-17 units of product entered on job planning lines this year, by job department
SELECT d.name AS department_name, ROUND(SUM(wi.quantity), 2) AS product_units_used
FROM work_order_items wi JOIN work_orders w ON w.id = wi.work_order_id LEFT JOIN departments d ON d.id = w.department_id
WHERE w.entity_id = 1 AND wi.created_at >= '2026-01-01' AND wi.created_at < '2026-07-16'
GROUP BY d.name ORDER BY d.name
