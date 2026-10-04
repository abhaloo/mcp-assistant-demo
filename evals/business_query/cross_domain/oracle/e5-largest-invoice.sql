-- E5 "who has the biggest order by invoice": the largest single invoice by
-- items_total and its customer (owner D7, 2026-09-28). items_total is rebuilt from
-- bill_items as rc-01.sql does; every invoice currency counts (the clone holds TZS only,
-- currency-check.sql). The second row shows the margin; a tie is reported, never broken.
SELECT b.invoice_number AS invoice_number,
       c.name AS customer_name,
       cur.currency_code AS currency_code,
       ROUND(COALESCE(i.items_total, 0), 2) AS items_total
FROM bills b
LEFT JOIN customers c ON c.id = b.customer_id
JOIN currencies cur ON cur.id = b.currency_id
LEFT JOIN (SELECT bi.bill_id,
                  SUM((bi.price * bi.quantity - bi.discount)
                      + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
           FROM bill_items bi GROUP BY bi.bill_id) i ON i.bill_id = b.id
WHERE b.entity_id = 1 AND b.type = 'Invoice'
ORDER BY i.items_total DESC
LIMIT 2
