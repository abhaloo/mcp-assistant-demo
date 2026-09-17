-- bq-11 accounts_receivable_aging as of business date 2026-07-15.
-- Approved buckets: Current/not due (<=0), 1-30, 31-60, 61-90, 91+ days past due.
-- Applies filter: outstanding > 0 AND due_date IS NOT NULL. Result shape is
-- [invoices_count, bill_outstanding].
SELECT bucket, COUNT(*) AS invoices_count, ROUND(SUM(outstanding), 2) AS bill_outstanding
FROM (
    SELECT CASE
             WHEN DATEDIFF('2026-07-15', b.due_date) <= 0  THEN 'Current/not due'
             WHEN DATEDIFF('2026-07-15', b.due_date) <= 30 THEN '1-30'
             WHEN DATEDIFF('2026-07-15', b.due_date) <= 60 THEN '31-60'
             WHEN DATEDIFF('2026-07-15', b.due_date) <= 90 THEN '61-90'
             ELSE '91+'
           END AS bucket,
           items.items_total - COALESCE(paid.payments_total, 0) AS outstanding
    FROM bills b
    JOIN (
        SELECT bi.bill_id,
               SUM((bi.price * bi.quantity - bi.discount)
                   + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS items_total
        FROM bill_items bi GROUP BY bi.bill_id
    ) items ON items.bill_id = b.id
    LEFT JOIN (
        SELECT j.bill_id, SUM(j.debit) AS payments_total
        FROM journals j JOIN accounts a ON a.id = j.account_id
        WHERE j.debit > 0
          AND a.account_type IN ('OPERATING_EXPENSE', 'PAYABLE', 'CASH', 'BANK')
        GROUP BY j.bill_id
    ) paid ON paid.bill_id = b.id
    WHERE b.entity_id = 1 AND b.type = 'Invoice' AND b.due_date IS NOT NULL
) aged
WHERE outstanding > 0
GROUP BY bucket
ORDER BY FIELD(bucket, 'Current/not due', '1-30', '31-60', '61-90', '91+')
