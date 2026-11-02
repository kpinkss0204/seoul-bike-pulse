SELECT
 "version",
 COUNT(*) AS row_count,
 MIN("date") AS first_date,
 MAX("date") AS last_date
FROM bikepulse_dev.__TABLE__
GROUP BY "version"
ORDER BY "version";
