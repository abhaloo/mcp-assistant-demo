-- bq-07 jobs_grouped_by_status_or_department: jobs per department RIGHT NOW.
-- Owner rule (2026-08-11): job questions exclude FINISHED/CANCELLED unless the
-- asker explicitly wants them. "Which department is this job in" means where it
-- is being worked, not where it ended up.
SELECT d.name AS department, COUNT(*) AS jobs
FROM work_orders w
LEFT JOIN departments d ON d.id = w.department_id
WHERE w.entity_id = 1
  AND w.status NOT IN ('FINISHED', 'CANCELLED')
GROUP BY d.name
ORDER BY jobs DESC, department
