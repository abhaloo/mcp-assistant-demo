-- lk-04 (admin) latest journal entries: ledger_entry declares no record route, so the id column never links;
-- bill_id cells link /bills/show/{bill_id} (link_key invoice). Superset of both orderings the planner may pick.
SELECT id, bill_id FROM (
  SELECT id, bill_id FROM ai_v1_bq_ledger_entry WHERE entity_id = 1 ORDER BY post_date DESC, id DESC LIMIT 20
) by_date
UNION
SELECT id, bill_id FROM (
  SELECT id, bill_id FROM ai_v1_bq_ledger_entry WHERE entity_id = 1 ORDER BY id DESC LIMIT 20
) by_id
ORDER BY id DESC
