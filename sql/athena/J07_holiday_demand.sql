SELECT
 CASE
 WHEN is_holiday THEN 'Holiday'
 ELSE 'No Holiday'
 END AS holiday_group,
 COUNT(*) AS observation_count,
 ROUND(
 AVG(rented_bike_count),
 2
 ) AS avg_rentals
FROM bikepulse_dev.__TABLE__
WHERE
 "version" = 'dv001'
 AND is_functioning_day = true
GROUP BY is_holiday
ORDER BY holiday_group;
