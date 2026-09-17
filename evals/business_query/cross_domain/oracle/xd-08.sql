-- xd-08 Neptune Pwani: jobs and invoices raised this year (two facts)
SELECT (SELECT COUNT(*) FROM work_orders w WHERE w.entity_id = 1 AND w.customer_id = 295
          AND w.created_at >= '2026-01-01' AND w.created_at < '2026-07-16') AS jobs_count,
       (SELECT COUNT(*) FROM bills b WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.customer_id = 295
          AND b.created_at >= '2026-01-01' AND b.created_at < '2026-07-16') AS invoices_count
