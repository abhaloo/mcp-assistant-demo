-- E5 job detail: the jobs of invoice E3914 by the job fact's effective-bill rule
-- (Billing SemanticViews.php:277-279): a job's own invoice, else the latest invoice
-- of its customer order.
SELECT wo.work_number AS work_number,
       wo.title AS title,
       wo.ordered_qty AS ordered_qty,
       wo.status AS status
FROM work_orders wo
WHERE COALESCE((SELECT b.id FROM bills b WHERE b.id = wo.bill_id AND b.type = 'Invoice'),
               (SELECT MAX(bco.id) FROM bills bco
                WHERE bco.customer_order_id = wo.customer_order_id AND bco.type = 'Invoice'))
      = (SELECT b2.id FROM bills b2 WHERE b2.invoice_number = 'E3914' AND b2.type = 'Invoice')
ORDER BY wo.work_number;
