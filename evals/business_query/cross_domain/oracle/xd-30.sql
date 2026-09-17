-- xd-30 jobs created this year in a finished state with no effective bill (never invoiced)
SELECT w.id AS job_id, w.work_number, w.status
FROM work_orders w
WHERE w.entity_id = 1 AND w.created_at >= '2026-01-01' AND w.created_at < '2026-07-16'
  AND w.status IN ('FINISHED','DELIVERED') AND (COALESCE((SELECT b.id FROM bills b WHERE b.id = w.bill_id AND b.type = 'Invoice'),
         (SELECT MAX(bco.id) FROM bills bco WHERE bco.customer_order_id = w.customer_order_id AND bco.type = 'Invoice'))) IS NULL
ORDER BY w.id
