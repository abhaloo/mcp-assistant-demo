-- bq-14 cash_received. Period moved to March 2025: cash/bank receipt postings in
-- this snapshot STOP at 2025-08-28 while invoicing runs to 2026-07-15, so any
-- recent window is structurally zero and would test nothing (bq-18 already owns
-- the honest-empty case). March 2025 is the densest cash month (375 postings).
-- Approved definition: cash and bank receipts ONLY (debit > 0); discounts and
-- other non-cash reductions belong to receivable_settlement, not here.
-- Time dimension is invoice.created_at per the approved definition, i.e. cash on
-- invoices RAISED in the period.
-- b.type = 'Invoice' added 2026-08-17: without it this key also counted cash on a
-- Payable Quotation (1,250,000.00), which the 'invoices RAISED' definition above
-- excludes and which every sibling oracle (bq-02/03/10) already filters out.
SELECT ROUND(SUM(j.debit), 2) AS cash_received
FROM journals j
JOIN accounts a ON a.id = j.account_id
JOIN bills b ON b.id = j.bill_id
WHERE j.entity_id = 1
  AND a.account_type IN ('CASH', 'BANK')
  AND j.debit > 0
  AND b.type = 'Invoice'
  AND b.created_at >= '2025-03-01' AND b.created_at < '2025-04-01'
