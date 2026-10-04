-- lk-03 (admin) latest 20 quotations: the quotation resource shares the bills table
-- (type = 'Quotation') and its route /bills/show/{id}; quotation number cells link via quotation.id.
-- LIMIT 20 is the superset the page size allows; the rendered rows are graded by the
-- ordering this oracle returns (latest first), not by a count.
SELECT id, invoice_number
FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Quotation'
ORDER BY created_at DESC, id DESC
LIMIT 20
