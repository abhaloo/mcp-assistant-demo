# Expected answers

These queries produce the expected answers in the README. We ran them against the demo data before asking the assistant.

## How to run them

Finish step 4 of [Run locally](../README.md#run-locally) first. That step creates the `ai_v1_*` views. Then open a MariaDB shell:

```bash
$dc exec mariadb mariadb -u root -pchange-me-root mcp_demo
```

Paste a query from below. The invoice data runs to early June 2026, so Q2 2026 is incomplete. That is why the comparison uses Q1 2026 and Q4 2025.

## 1. Compare invoiced revenue in Q1 2026 vs Q4 2025 (Finance)

Expected: Q1 2026 is 77,769.14 from 180 invoices. Q4 2025 is 155,583.40 from 280 invoices. That is down 50.0%.

```sql
SELECT ROUND(SUM(items_total), 2) AS q1_2026, COUNT(*) AS invoices
  FROM ai_v1_bq_bill_fact
 WHERE entity_id = 1 AND type = 'Invoice' AND currency_code = 'USD'
   AND created_at >= '2026-01-01' AND created_at < '2026-04-01';

SELECT ROUND(SUM(items_total), 2) AS q4_2025, COUNT(*) AS invoices
  FROM ai_v1_bq_bill_fact
 WHERE entity_id = 1 AND type = 'Invoice' AND currency_code = 'USD'
   AND created_at >= '2025-10-01' AND created_at < '2026-01-01';
```

## 2. What was our collection rate in July 2025? (Finance)

Collection rate is cash received divided by the amount invoiced, for invoices issued that month.

Expected: 3,695.67 / 69,890.16 = 0.0529, shown as 5.3%, from 104 invoices.

```sql
SELECT ROUND(SUM(cash_received), 2) AS cash_received,
       ROUND(SUM(items_total), 2) AS invoiced,
       ROUND(SUM(cash_received) / NULLIF(SUM(items_total), 0), 4) AS collection_rate,
       COUNT(*) AS invoices
  FROM ai_v1_bq_bill_fact
 WHERE entity_id = 1 AND type = 'Invoice' AND currency_code = 'USD'
   AND created_at >= '2025-07-01' AND created_at < '2025-08-01';
```

## 3. Which customers owe us the most right now? (Finance)

Expected order: Upland Society, then Vinemount Society, then Palmgrove Advertising.

```sql
SELECT customer_name, ROUND(SUM(outstanding), 2) AS owed, COUNT(*) AS open_invoices
  FROM ai_v1_bq_bill_fact
 WHERE entity_id = 1 AND type = 'Invoice' AND currency_code = 'USD' AND outstanding > 0
 GROUP BY customer_id, customer_name
 ORDER BY owed DESC
 LIMIT 3;
```

## 4. How many finished jobs have no invoice? (Warehouse)

Expected: 1,463.

```sql
SELECT COUNT(*) AS finished_jobs_without_invoice
  FROM ai_v1_bq_job_fact
 WHERE entity_id = 1 AND status IN ('FINISHED', 'DELIVERED') AND effective_bill_id IS NULL;
```

## Answers that need no query

- **Warehouse asks for the collection rate.** The assistant refuses, because the Warehouse account has no invoice permission.
- **Paid time off.** Full-time staff get 18 days a year. This comes from [the handbook](../data/corpus/company/all/handbook.md).
