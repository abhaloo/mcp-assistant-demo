-- hq-02 invoice_number_vs_id: the amount on the invoice the user quoted by its
-- human-facing number.
-- The quoted number is unique across every bill in the snapshot. Its numeric part is
-- ALSO a live bills.id belonging to a different invoice with a different total, so a
-- plan that filters the database id returns a wrong answer rather than nothing.
-- Approved definition: items_total is computed TERM BY TERM per item,
--   (price*qty - discount) + (price*qty - discount) * tax/100
-- never price*qty*(1+tax/100). Re-derived here from base tables.
SELECT ROUND(SUM((bi.price * bi.quantity - bi.discount)
                 + (bi.price * bi.quantity - bi.discount) * bi.tax / 100), 2) AS invoice_total
FROM bills b
JOIN bill_items bi ON bi.bill_id = b.id
WHERE b.entity_id = 1
  AND b.type = 'Invoice'
  AND b.invoice_number = 'E4830'
