SELECT
 COUNT(*) AS total_rows,
 MIN("date") AS first_date,
 MAX("date") AS last_date,
 SUM(
 CASE
 WHEN is_functioning_day THEN 1
 ELSE 0
 END
 ) AS functioning_rows,
 SUM(
 CASE
 WHEN NOT is_functioning_day THEN 1
 ELSE 0
 END
 ) AS non_functioning_rows,
 ROUND(
 AVG(rented_bike_count),
 2
 ) AS avg_rentals
FROM bikepulse_dev.__TABLE__
WHERE "version" = 'dv001';
