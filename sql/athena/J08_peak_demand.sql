SELECT
 "date",
 hour,
 rented_bike_count,
 temperature_c,
 humidity_pct,
 rainfall_mm,
 season
FROM bikepulse_dev.__TABLE__
WHERE
 "version" = 'dv001'
 AND is_functioning_day = true
ORDER BY rented_bike_count DESC
LIMIT 10;
