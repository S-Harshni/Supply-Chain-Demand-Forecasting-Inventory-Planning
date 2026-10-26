-- Units, revenue and orders per month (real demand only: no cancelled or fraud lines)
SELECT strftime('%Y-%m', order_date)          AS month,
       SUM(quantity)                          AS units,
       ROUND(SUM(net_sales), 2)               AS net_sales,
       COUNT(DISTINCT order_id)               AS orders,
       COUNT(DISTINCT product_id)             AS active_products,
       MAX(after_catalogue_break)             AS after_catalogue_break
FROM order_lines
WHERE is_demand = 1
GROUP BY month
ORDER BY month;
