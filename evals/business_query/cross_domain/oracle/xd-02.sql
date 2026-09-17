-- xd-02 invoiced revenue this year split by whether the customer has any customer order
SELECT CASE WHEN EXISTS (SELECT 1 FROM customer_orders co WHERE co.customer_id = c.id) THEN 1 ELSE 0 END AS has_orders,
       ROUND(SUM(i.items_total), 2) AS invoiced_revenue
FROM bills b JOIN customers c ON c.id = b.customer_id JOIN (SELECT bi.bill_id,
        SUM((bi.price * bi.quantity - bi.discount)
            + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
   FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id
WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16'
GROUP BY 1 ORDER BY 1
