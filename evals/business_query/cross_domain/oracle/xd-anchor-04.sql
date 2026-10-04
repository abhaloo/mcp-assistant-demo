-- xd-anchor-04 four latest invoices (selection smaller than limit 10) and their jobs
SELECT b.invoice_number, w.id AS job_id, w.work_number, w.status
FROM (
  SELECT id, invoice_number FROM bills
  WHERE entity_id = 1 AND type = 'Invoice'
  ORDER BY created_at DESC, id DESC
  LIMIT 4
) b
LEFT JOIN work_orders w ON w.entity_id = 1 AND (
  COALESCE(
    (SELECT b2.id FROM bills b2 WHERE b2.id = w.bill_id AND b2.type = 'Invoice'),
    (SELECT MAX(bco.id) FROM bills bco WHERE bco.customer_order_id = w.customer_order_id AND bco.type = 'Invoice')
  )
) = b.id
ORDER BY b.invoice_number, w.id
