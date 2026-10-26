-- Late-delivery rate by market and shipping mode
SELECT market,
       shipping_mode,
       COUNT(*)                         AS shipments,
       ROUND(AVG(is_late) * 100, 1)     AS late_pct
FROM order_lines
WHERE shipment_cancelled = 0
GROUP BY market, shipping_mode
ORDER BY market, late_pct DESC;
