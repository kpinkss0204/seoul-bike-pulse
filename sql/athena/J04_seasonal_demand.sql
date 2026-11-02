SELECT
 season,
 COUNT(*) AS observation_count,
 ROUND(
 AVG(rented_bike_count),
 2
 ) AS avg_rentals,
 AI MLOps 실무프로젝트 페이지 30
 ) AS avg_rentals,
 SUM(rented_bike_count) AS total_rentals,
 MAX(rented_bike_count) AS max_hourly_rentals
FROM bikepulse_dev.__TABLE__
WHERE
 "version" = 'dv001'
 AND is_functioning_day = true
GROUP BY season
ORDER BY avg_rentals DESC;
