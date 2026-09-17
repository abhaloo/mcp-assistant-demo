-- pa-16: operating revenue posted in June 2026 (entity 1).
SELECT ROUND(SUM(j.credit) - SUM(j.debit), 2) AS operating_revenue
FROM journals j
JOIN accounts a ON a.id = j.account_id
WHERE j.entity_id = 1
  AND a.account_type = 'OPERATING_REVENUE'
  AND j.post_date >= '2026-06-01'
  AND j.post_date <  '2026-07-01'
