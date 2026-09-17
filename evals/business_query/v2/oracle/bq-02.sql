-- bq-02 serena_hotels_last_tissue_order: Serena's most recent invoice carrying a
-- tissue product.
-- Route matters: tissue products appear ONLY on invoice lines (161 rows). They
-- never appear on work_order_items (0 of 4435), and the TISSUE department holds
-- just 4 jobs company-wide. 29 work orders mention tissue in free text only,
-- which the semantic layer cannot reach by design.
SELECT b.invoice_number, MAX(b.created_at) AS invoiced_at
FROM bills b
JOIN bill_items bi ON bi.bill_id = b.id
JOIN products p ON p.id = bi.product_id
JOIN customers cu ON cu.id = b.customer_id
WHERE b.entity_id = 1
  AND b.type = 'Invoice'
  AND UPPER(TRIM(cu.name)) LIKE '%SERENA%'
  AND LOWER(TRIM(p.name)) LIKE '%tissue%'
GROUP BY b.invoice_number
ORDER BY invoiced_at DESC
LIMIT 1
