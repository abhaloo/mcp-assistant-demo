-- hq-08 grouped_breakdown_per_status: customer orders split by their stored status.
-- Stored status vocabulary is CREATED / ACTIVE / FINISHED / CANCELLED; the computed
-- NEW / IN_PROGRESS values are accessor-only and never stored. No status filter is
-- applied: the question asks for the whole split, so every status present must appear.
-- Three statuses occur in the snapshot, so the answer is small, tie-free and far
-- inside the 50-row plan cap.
SELECT status, COUNT(*) AS orders
FROM customer_orders
WHERE entity_id = 1
GROUP BY status
ORDER BY status
