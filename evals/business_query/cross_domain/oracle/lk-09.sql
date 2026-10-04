-- lk-09 (sales) a customer name that matches two rows in the population the resolver queries
-- (the entity-scoped projection view, app/business_query/plan/value_resolver/sql.py binding.projection_view):
-- the clarification card shows one View link per candidate, /customer/show/{id}.
SELECT name, COUNT(*) AS n
FROM ai_v1_bq_customer_fact
WHERE entity_id = 1
GROUP BY name
HAVING COUNT(*) > 1
ORDER BY n DESC, name
LIMIT 3;
-- candidates for the chosen name
SELECT id, name FROM ai_v1_bq_customer_fact WHERE entity_id = 1 AND name = 'ACT Wazalendo Zanzibar' ORDER BY id
