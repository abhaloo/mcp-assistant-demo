-- xd-13 the latest invoice (by created_at, id as tie-break) and the jobs whose effective bill it is
SELECT b.invoice_number, b.created_at, w.id AS job_id, w.work_number, w.status
FROM bills b
LEFT JOIN work_orders w ON w.entity_id = 1 AND (COALESCE((SELECT b.id FROM bills b WHERE b.id = w.bill_id AND b.type = 'Invoice'),
         (SELECT MAX(bco.id) FROM bills bco WHERE bco.customer_order_id = w.customer_order_id AND bco.type = 'Invoice'))) = b.id
WHERE b.id = (SELECT b2.id FROM bills b2 WHERE b2.entity_id = 1 AND b2.type = 'Invoice'
              ORDER BY b2.created_at DESC, b2.id DESC LIMIT 1)
ORDER BY w.id
