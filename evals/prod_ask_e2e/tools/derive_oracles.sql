-- Re-derive every SQL oracle in suite.json.
--
-- Run this after ANY ingest, restore, or migration of mcp_prod_wire_eval. The suite's
-- expected values are snapshots of live data; a stale oracle marks a correct answer wrong,
-- which is the worst failure mode a suite has -- it trains you to ignore it.
--
-- These read the ai_v1_bq_* semantic views, the same views the SQL route reads, but with no
-- planner, no LLM, and no RAG service in the path. That independence is what makes them an
-- oracle rather than a second opinion from the system under test.
--
--   mysql -u <user> -p mcp_prod_wire_eval < evals/prod_ask_e2e/tools/derive_oracles.sql
--
-- Entity 1 is hardcoded because every seeded persona belongs to it (see suite.json
-- environment.entity_id). Change both together or the personas stop matching the oracles.

SELECT 'rb-01  print jobs (dept 2)' AS oracle, COUNT(*) AS value
FROM ai_v1_bq_job_fact WHERE entity_id = 1 AND department_id = 2
UNION ALL
SELECT 'rb-02  store jobs (dept 1)', COUNT(*)
FROM ai_v1_bq_job_fact WHERE entity_id = 1 AND department_id = 1
UNION ALL
SELECT 'rb-03  all jobs (no dept scope)', COUNT(*)
FROM ai_v1_bq_job_fact WHERE entity_id = 1
UNION ALL
SELECT 'rb-05  total outstanding (must NOT leak)', ROUND(SUM(outstanding), 2)
FROM ai_v1_bq_bill_fact WHERE entity_id = 1 AND type = 'Invoice'
UNION ALL
SELECT 'ac-01  customers', COUNT(*)
FROM ai_v1_bq_customer_fact WHERE entity_id = 1
UNION ALL
SELECT 'ac-02  invoices total', COUNT(*)
FROM ai_v1_bq_bill_fact WHERE entity_id = 1 AND type = 'Invoice'
UNION ALL
SELECT 'ac-02  invoices unpaid', COUNT(*)
FROM ai_v1_bq_bill_fact WHERE entity_id = 1 AND type = 'Invoice' AND outstanding > 0
UNION ALL
SELECT 'ac-05  inventory approved', COUNT(*)
FROM ai_v1_bq_inventory_fact WHERE entity_id = 1 AND type = 'Receive' AND status = 'APPROVED'
UNION ALL
SELECT 'ac-05  inventory total (must NOT be the answer)', COUNT(*)
FROM ai_v1_bq_inventory_fact WHERE entity_id = 1;

-- ac-03: top 5 customers by invoiced amount. Order is part of the oracle, not decoration.
SELECT 'ac-03' AS oracle, customer_name, ROUND(SUM(items_total), 2) AS total
FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Invoice'
GROUP BY customer_name
ORDER BY total DESC
LIMIT 5;

-- ac-04: printing's job statuses. The unscoped breakdown below is the leak signature --
-- if those counts show up in a printing answer, the department predicate was dropped.
SELECT 'ac-04  scoped' AS oracle, status, COUNT(*) AS n
FROM ai_v1_bq_job_fact
WHERE entity_id = 1 AND department_id = 2
GROUP BY status
ORDER BY n DESC;

SELECT 'ac-04  unscoped (leak signature)' AS oracle, status, COUNT(*) AS n
FROM ai_v1_bq_job_fact
WHERE entity_id = 1
GROUP BY status
ORDER BY n DESC;

-- ct-01: largest unpaid invoices. The model may pick a different slice of "a few largest",
-- so grade rows against the view rather than against this exact five.
SELECT 'ct-01' AS oracle, id, invoice_number, customer_name, ROUND(outstanding, 2) AS outstanding
FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Invoice' AND outstanding > 0
ORDER BY outstanding DESC
LIMIT 5;

-- ct-02: the complete set of job ids printing may name. Anything outside it is a
-- cross-department leak, not a wrong row.
SELECT 'ct-02' AS oracle, id, customer_order_number, status
FROM ai_v1_bq_job_fact
WHERE entity_id = 1 AND department_id = 2
ORDER BY id;

-- dt-01 / dt-02: typed print-side populations and their coverage denominator.
SELECT 'dt-01  one-sided Jobs' AS oracle, COUNT(DISTINCT job_id) AS value
FROM ai_v2_bq_job_print_fact
WHERE entity_id = 1 AND attribute_key = 'print.sides' AND typed_value = 'one_side'
UNION ALL
SELECT 'dt-02  duplex Jobs', COUNT(DISTINCT job_id)
FROM ai_v2_bq_job_print_fact
WHERE entity_id = 1 AND attribute_key = 'print.sides' AND typed_value = 'two_side'
UNION ALL
SELECT 'dt-01/02  eligible Jobs', COUNT(*)
FROM ai_v1_bq_job_fact
WHERE entity_id = 1
UNION ALL
SELECT 'dt-01/02  Jobs with a curated print-side fact', COUNT(DISTINCT job_id)
FROM ai_v2_bq_job_print_fact
WHERE entity_id = 1 AND attribute_key = 'print.sides'
UNION ALL
SELECT 'dt-01/02  eligible Jobs without a curated print-side fact', COUNT(*)
FROM ai_v1_bq_job_fact AS job
WHERE job.entity_id = 1
  AND NOT EXISTS (
      SELECT 1
      FROM ai_v2_bq_job_print_fact AS detail
      WHERE detail.entity_id = job.entity_id
        AND detail.job_id = job.id
        AND detail.attribute_key = 'print.sides'
  );

SELECT 'dt-01/02  migration ledger' AS oracle, status, COUNT(*) AS value
FROM ai_detail_migration_ledger
WHERE entity_id = 1 AND resource_type = 'job' AND attribute_key = 'print.sides'
GROUP BY status
ORDER BY status;

-- dt-03 / dt-04 / dt-06: one Job's typed observation and raw stable fields.
SELECT 'dt-03/04/06' AS oracle,
       detail.job_id,
       detail.attribute_key,
       detail.typed_value,
       detail.display_value,
       detail.revision AS observation_version,
       detail.revision_hash AS definition_revision_hash,
       detail.validation_state,
       detail.source,
       job.ordered_qty,
       job.specification_id,
       specification.name AS specification_name
FROM ai_v2_bq_job_print_fact AS detail
JOIN work_orders AS job ON job.id = detail.job_id
LEFT JOIN specifications AS specification ON specification.id = job.specification_id
WHERE detail.entity_id = 1
  AND detail.job_id = 24694
  AND detail.attribute_key = 'print.sides';

SELECT 'dt-06  typed detail keys' AS oracle, attribute_key
FROM ai_v2_bq_job_print_fact
WHERE entity_id = 1 AND job_id = 24694
GROUP BY attribute_key
ORDER BY attribute_key;

-- dt-05: distinguish a mutable specification lookup from an immutable profile binding.
-- A zero table count is the deployment oracle: no profile-revision/binding/history schema
-- is present, so the print.sides definition hash must not be presented as a profile hash.
SELECT 'dt-05  immutable specification-profile tables present' AS oracle, COUNT(*) AS value
FROM information_schema.tables
WHERE table_schema = DATABASE()
  AND table_name IN (
      'ai_specification_profile_revisions',
      'ai_job_specification_profile_bindings',
      'ai_specification_observations'
  );

-- dt-07 / dt-08: stable Customer and Customer Order fields away from their pages.
SELECT 'dt-07' AS oracle, id, name, business_name
FROM ai_v1_customer
WHERE entity_id = 1 AND id = 8;

SELECT 'dt-08' AS oracle,
       id,
       order_number,
       customer_name,
       CASE WHEN description IS NULL OR description = ''
            THEN 'NULL_OR_EMPTY' ELSE 'NON_EMPTY' END AS description_state
FROM ai_v1_customer_order
WHERE entity_id = 1 AND order_number = '744';

-- dt-09: "issued this month" uses the Invoice creation timestamp in this deployment.
SELECT 'dt-09' AS oracle, COUNT(*) AS value
FROM ai_v1_bq_bill_fact
WHERE entity_id = 1
  AND type = 'Invoice'
  AND created_at >= '2026-08-01 00:00:00'
  AND created_at <  '2026-09-01 00:00:00';

-- dt-10: ordered quantity is still raw text and has no typed numeric definition.
SELECT 'dt-10  ordered_qty SQL type' AS oracle, column_type AS value
FROM information_schema.columns
WHERE table_schema = DATABASE()
  AND table_name = 'work_orders'
  AND column_name = 'ordered_qty';

SELECT 'dt-10  typed ordered-quantity definitions' AS oracle, COUNT(*) AS value
FROM ai_definition_revisions
WHERE entity_id = 1 AND attribute_key = 'ordered_qty';

-- dt-11: the raw source has material values, but no signed typed material definition.
SELECT 'dt-11  raw Jobs mentioning FBB Offcuts' AS oracle, COUNT(*) AS value
FROM work_orders
WHERE specifications LIKE '%FBB Offcuts%';

SELECT 'dt-11  typed material definitions' AS oracle, COUNT(*) AS value
FROM ai_definition_revisions
WHERE entity_id = 1 AND attribute_key LIKE '%material%';

-- Focused persona contract. The seed script mirrors mutable source-user permissions, so
-- freeze the identities and the grants/denials on which this SQL-only canary depends.
SELECT 'persona identity' AS oracle, username, entity_id, department_id, role
FROM users
WHERE username IN (
    'test_admin',
    'test_dept_store',
    'test_dept_printing',
    'test_dept_sales',
    'test_dept_tissue'
)
ORDER BY username;

SELECT 'persona relevant permissions' AS oracle,
       user.username,
       MAX(permission.name = 'view job') AS can_view_job,
       MAX(permission.name = 'view customer') AS can_view_customer,
       MAX(permission.name = 'view customer order') AS can_view_customer_order,
       MAX(permission.name = 'view invoice') AS can_view_invoice
FROM users AS user
LEFT JOIN model_has_permissions AS grant_row
  ON grant_row.model_id = user.id
 AND grant_row.model_type = 'App\\Models\\User'
LEFT JOIN permissions AS permission ON permission.id = grant_row.permission_id
WHERE user.username IN ('test_admin', 'test_dept_store', 'test_dept_printing')
GROUP BY user.id, user.username
ORDER BY user.username;

-- tc-01: two-turn continuity (turn 1 count, turn 2 outstanding sum)
-- mp-01 reuses these two statements (one-turn conjoined question; values filled at run)
SELECT COUNT(id) AS tc01_turn1 FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Invoice' AND outstanding > 0;
SELECT ROUND(SUM(outstanding), 2) AS tc01_turn2 FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Invoice' AND outstanding > 0;

-- fm-02: payment-status breakdown (product CASE: explicit fourth band, no ELSE)
SELECT CASE WHEN outstanding > 0 AND outstanding >= items_total THEN 'Never paid'
            WHEN outstanding > 0 AND outstanding < items_total THEN 'Partially paid'
            WHEN outstanding = 0 THEN 'Fully paid'
            WHEN outstanding < 0 THEN 'Overpaid' END AS bucket,
       COUNT(id) AS n
FROM ai_v1_bq_bill_fact
WHERE entity_id = 1 AND type = 'Invoice'
GROUP BY bucket;
