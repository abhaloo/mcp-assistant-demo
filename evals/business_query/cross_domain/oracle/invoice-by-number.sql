-- Date and currency of listed invoices (LT1 turn 3 check). Replace the IN list with
-- the invoice numbers the answer listed before running.
SELECT b.invoice_number AS invoice_number,
       DATE(b.created_at) AS invoice_date,
       cur.currency_code AS currency_code
FROM bills b
JOIN currencies cur ON cur.id = b.currency_id
WHERE b.entity_id = 1 AND b.type = 'Invoice'
  AND b.invoice_number IN ('E0000')
ORDER BY b.invoice_number;
