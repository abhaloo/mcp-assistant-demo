-- bq-21 open_orders_for_top_customers: open orders for the top 10 customers
-- by invoiced revenue (TZS), the derived-set incident scenario.
-- Ranked anchor: invoiced revenue per customer from Invoice-type bills, using
-- the same term-by-term revenue formula as bq-12 (never price*qty*(1+tax/100)).
-- Open per the bq-06 approved definition: status NOT IN ('FINISHED','CANCELLED');
-- this snapshot's only non-terminal status is 'CREATED'.
SELECT co.id AS order_id,
       co.order_number,
       co.customer_id,
       c.name AS customer_name,
       co.status
FROM customer_orders co
JOIN customers c ON c.id = co.customer_id
WHERE co.entity_id = 1
  AND co.status NOT IN ('FINISHED', 'CANCELLED')
  AND co.customer_id IN (
      SELECT ranked.customer_id
      FROM (
          SELECT b.customer_id,
                 SUM((bi.price * bi.quantity - bi.discount)
                     + (bi.price * bi.quantity - bi.discount) * bi.tax / 100) AS revenue
          FROM bill_items bi
          JOIN bills b ON b.id = bi.bill_id
          WHERE b.entity_id = 1
            AND b.type = 'Invoice'
            AND b.customer_id IS NOT NULL
          GROUP BY b.customer_id
          ORDER BY revenue DESC, b.customer_id ASC
          LIMIT 10
      ) ranked
  )
ORDER BY co.id ASC
