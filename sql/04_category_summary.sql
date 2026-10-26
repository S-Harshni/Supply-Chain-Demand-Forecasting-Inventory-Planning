-- Revenue, margin and delivery performance per category (real demand only)
SELECT p.department,
       o.category,
       SUM(o.quantity)                                   AS units,
       ROUND(SUM(o.net_sales), 2)                        AS net_sales,
       ROUND(SUM(o.profit), 2)                           AS profit,
       ROUND(SUM(o.profit) * 100.0 / SUM(o.net_sales), 1) AS margin_pct,
       ROUND(AVG(o.is_late) * 100, 1)                    AS late_pct
FROM order_lines o
JOIN products p ON p.product_id = o.product_id
WHERE o.is_demand = 1
GROUP BY p.department, o.category
ORDER BY net_sales DESC;
