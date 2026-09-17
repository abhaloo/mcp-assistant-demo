-- bq-13 ledger_revenue for July 2026.
-- Approved definition: SUM(credit - debit) on OPERATING_REVENUE / NON_OPERATING_REVENUE.
-- Sales Discount is OPERATING_EXPENSE and is therefore excluded.
-- Must NOT reconcile with bq-12 — different measures by design.
SELECT ROUND(SUM(j.credit - j.debit), 2) AS ledger_revenue
FROM journals j
JOIN accounts a ON a.id = j.account_id
WHERE j.entity_id = 1
  AND a.account_type IN ('OPERATING_REVENUE', 'NON_OPERATING_REVENUE')
  AND j.post_date >= '2026-07-01' AND j.post_date < '2026-08-01'
