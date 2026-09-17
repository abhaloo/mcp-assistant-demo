-- xd-12 customers with more than 20 jobs this year: jobs and invoiced revenue this year
SELECT c.name AS customer_name, j.jobs_count, ROUND(COALESCE(r.invoiced_revenue, 0), 2) AS invoiced_revenue
FROM (SELECT w.customer_id, COUNT(*) jobs_count FROM work_orders w
      WHERE w.entity_id = 1 AND w.created_at >= '2026-01-01' AND w.created_at < '2026-07-16'
      GROUP BY w.customer_id HAVING COUNT(*) > 20) j
JOIN customers c ON c.id = j.customer_id
LEFT JOIN (SELECT b.customer_id, SUM(i.items_total) invoiced_revenue FROM bills b JOIN (SELECT bi.bill_id,
        SUM((bi.price * bi.quantity - bi.discount)
            + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
   FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id
      WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16'
      GROUP BY b.customer_id) r ON r.customer_id = j.customer_id
ORDER BY j.jobs_count DESC, c.name
