-- bq-10 overdue total: money still owed on invoices more than 30 days past due,
-- as of business date 2026-07-15.
-- Reworded 2026-08-11: this case used to ask for a LIST. 91 invoices tie at the
-- maximum 1184 days past due, so "the most overdue invoices" has no single
-- correct row set and no answer key could be satisfied. The total tests the same
-- two definitions (what counts as overdue, and what is still outstanding).
-- outstanding = items_total - payments_total, where payments_total is
-- Bill::payments(): journals with debit > 0 on account types
-- OPERATING_EXPENSE / PAYABLE / CASH / BANK.
SELECT ROUND(SUM(items.items_total - COALESCE(paid.payments_total, 0)), 2) AS still_owed
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
WHERE b.entity_id = 1
  AND b.type = 'Invoice'
  AND b.due_date IS NOT NULL
  AND DATEDIFF('2026-07-15', b.due_date) > 30
  AND (items.items_total - COALESCE(paid.payments_total, 0)) > 0
