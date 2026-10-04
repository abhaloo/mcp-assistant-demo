-- ct-01 (store) the largest unpaid invoices: the cells an anchor check grades pair each
-- rendered invoice number with the id this oracle returns for it (/bills/show/{id}),
-- independent of lk-01.sql's latest-invoice ordering.
SELECT id, invoice_number, ROUND(outstanding, 2) AS outstanding
FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Invoice' AND outstanding > 0
ORDER BY outstanding DESC
LIMIT 20
