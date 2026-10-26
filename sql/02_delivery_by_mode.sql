-- Delivery performance by shipping mode: how often and by how much promises are missed
SELECT shipping_mode,
       COUNT(*)                                        AS shipments,
       ROUND(AVG(is_late) * 100, 1)                    AS late_pct,
       ROUND(AVG(ship_days_scheduled), 2)              AS promised_days,
       ROUND(AVG(ship_days_actual), 2)                 AS actual_days,
       ROUND(AVG(CASE WHEN is_late = 1 THEN delay_days END), 2) AS avg_delay_when_late
FROM order_lines
WHERE shipment_cancelled = 0
GROUP BY shipping_mode
ORDER BY late_pct DESC;
