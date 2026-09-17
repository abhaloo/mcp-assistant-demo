-- xd-20 line items and jobs on one named invoice (number filled in by the probe)
SELECT 'item' AS kind, p.name AS name, bi.quantity AS quantity FROM bills b JOIN bill_items bi ON bi.bill_id = b.id LEFT JOIN products p ON p.id = bi.product_id
WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.invoice_number = 'E7416'
UNION ALL
SELECT 'job', w.work_number, NULL FROM work_orders w JOIN bills b ON b.id = (COALESCE((SELECT b.id FROM bills b WHERE b.id = w.bill_id AND b.type = 'Invoice'),
         (SELECT MAX(bco.id) FROM bills bco WHERE bco.customer_order_id = w.customer_order_id AND bco.type = 'Invoice')))
WHERE w.entity_id = 1 AND b.type = 'Invoice' AND b.invoice_number = 'E7416'
ORDER BY 1, 2
