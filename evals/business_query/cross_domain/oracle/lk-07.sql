-- lk-07 (admin, appended to xd-01) the ten latest invoices with their ids: E7406 exists twice (4817 here, 4813 older);
-- the restored turn must carry the same /bills/show/{id} anchors as the live paint.
SELECT id, invoice_number
FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Invoice'
ORDER BY created_at DESC, id DESC
LIMIT 10
