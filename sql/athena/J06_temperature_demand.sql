WITH grouped AS (
 SELECT
 CASE
 WHEN temperature_c < 0 THEN 'Below 0'
 WHEN temperature_c < 10 THEN '0 to 10'
 WHEN temperature_c < 20 THEN '10 to 20'
 WHEN temperature_c < 30 THEN '20 to 30'
 ELSE '30 and Above'
 END AS temperature_group,
 CASE
 WHEN temperature_c < 0 THEN 1
 WHEN temperature_c < 10 THEN 2
 WHEN temperature_c < 20 THEN 3
 WHEN temperature_c < 30 THEN 4
 ELSE 5
 END AS sort_order,
 rented_bike_count
 FROM bikepulse_dev.__TABLE__
 WHERE
 "version" = 'dv001'
 AND is_functioning_day = true
)
SELECT
 temperature_group,
 COUNT(*) AS observation_count,
 ROUND(
 AVG(rented_bike_count),
 2
 ) AS avg_rentals
FROM grouped
GROUP BY temperature_group, sort_order
ORDER BY sort_order;
