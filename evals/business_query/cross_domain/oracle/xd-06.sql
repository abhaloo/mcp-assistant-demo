-- xd-06 customers quoted this year with no invoice this year
SELECT c.name AS customer_name
FROM customers c
WHERE c.entity_id = 1
  AND c.id IN (SELECT q.customer_id FROM bills q WHERE q.entity_id = 1 AND q.type = 'Quotation'
               AND q.created_at >= '2026-01-01' AND q.created_at < '2026-07-16')
  AND c.id NOT IN (SELECT i.customer_id FROM bills i WHERE i.entity_id = 1 AND i.type = 'Invoice'
               AND i.created_at >= '2026-01-01' AND i.created_at < '2026-07-16' AND i.customer_id IS NOT NULL)
ORDER BY c.name
