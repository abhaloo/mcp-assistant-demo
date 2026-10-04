-- lk-01 (store) latest 20 invoices with the id behind each link: Invoice cells link /bills/show/{id};
-- customer_order_id is the id a viewer WITH `view customer order` would link to (store lacks it).
-- LIMIT 20 is the superset the page size allows; the question states no count, and the
-- rendered rows are graded by the ordering this oracle returns (latest first).
SELECT id, invoice_number, customer_order_id, customer_order_number
FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Invoice'
ORDER BY created_at DESC, id DESC
LIMIT 20
