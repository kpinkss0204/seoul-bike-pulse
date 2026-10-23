SELECT
 COUNT(*) AS total_rows,
COUNT_IF(
 is_functioning_day
 ) AS functioning_rows,
COUNT_IF(
 NOT is_functioning_day
 ) AS non_functioning_rows,
ROUND(
 AVG(
 CASE
 WHEN is_functioning_day
 THEN rented_bike_count
 ELSE NULL
 END
 ),
 2
 ) AS avg_hourly_rentals_functioning
FROM bikepulse_dev.__TABLE__
WHERE "version" = 'dv001';
