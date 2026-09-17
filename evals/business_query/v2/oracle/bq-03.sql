-- bq-03 last_tissue_job: the most recent tissue sale, any customer.
-- Same invoice-line route as bq-02 (see it for why department/work-order routes
-- are empty); scoped company-wide rather than to one customer.
SELECT b.invoice_number, TRIM(cu.name) AS customer, MAX(b.created_at) AS invoiced_at
FROM bills b
JOIN bill_items bi ON bi.bill_id = b.id
JOIN products p ON p.id = bi.product_id
JOIN customers cu ON cu.id = b.customer_id
WHERE b.entity_id = 1
  AND b.type = 'Invoice'
  AND LOWER(TRIM(p.name)) LIKE '%tissue%'
GROUP BY b.invoice_number, cu.name
ORDER BY invoiced_at DESC
LIMIT 1
