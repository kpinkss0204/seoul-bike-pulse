SELECT
 day_of_week("date") AS weekday_num,
 CASE day_of_week("date")
 WHEN 1 THEN 'Monday'
 WHEN 2 THEN 'Tuesday'
 WHEN 3 THEN 'Wednesday'
 WHEN 4 THEN 'Thursday'
 WHEN 5 THEN 'Friday'
 WHEN 6 THEN 'Saturday'
 WHEN 7 THEN 'Sunday'
 END AS weekday_name,
 COUNT(*) AS observation_count,
 ROUND(
 AVG(rented_bike_count),
 2
 ) AS avg_rentals
FROM bikepulse_dev.__TABLE__
WHERE
 "version" = 'dv001'
 AND is_functioning_day = true
GROUP BY day_of_week("date")
ORDER BY weekday_num;
