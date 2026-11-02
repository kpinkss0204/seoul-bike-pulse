SELECT
 CASE
 WHEN rainfall_mm = 0 THEN 'No Rain'
 ELSE 'Rain'
 END AS rainfall_group,
 COUNT(*) AS observation_count,
 ROUND(
 AVG(rented_bike_count),
 2
 ) AS avg_rentals,
 ROUND(
 AVG(temperature_c),
 2
 ) AS avg_temperature_c
FROM bikepulse_dev.__TABLE__
WHERE
 "version" = 'dv001'
 AND is_functioning_day = true
GROUP BY
 CASE
 WHEN rainfall_mm = 0 THEN 'No Rain'
 ELSE 'Rain'
 END
ORDER BY rainfall_group;
