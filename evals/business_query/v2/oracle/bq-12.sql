-- bq-12 invoiced_revenue for July 2026 (business date 2026-07-15).
-- Approved definition: SUM(items_total) where items_total is TERM BY TERM per item:
--   (price*qty - discount) + (price*qty - discount) * tax/100
-- Never price*qty*(1+tax/100). Independent re-derivation from base tables.
SELECT ROUND(SUM((bi.price * bi.quantity - bi.discount)
                 + (bi.price * bi.quantity - bi.discount) * bi.tax / 100), 2) AS invoiced_revenue
FROM bill_items bi
JOIN bills b ON b.id = bi.bill_id
WHERE b.entity_id = 1
  AND b.type = 'Invoice'
  AND b.created_at >= '2026-07-01' AND b.created_at < '2026-08-01'
