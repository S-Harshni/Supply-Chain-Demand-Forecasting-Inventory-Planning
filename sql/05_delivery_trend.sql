-- Monthly on-time delivery trend
SELECT strftime('%Y-%m', order_date)      AS month,
       COUNT(*)                           AS shipments,
       ROUND(AVG(is_late) * 100, 1)       AS late_pct,
       ROUND(AVG(delay_days), 2)          AS avg_delay_days
FROM order_lines
WHERE shipment_cancelled = 0
GROUP BY month
ORDER BY month;
