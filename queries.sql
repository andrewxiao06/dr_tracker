-- Named queries. The dashboard reads ONLY through these, so UI and data logic stay separate.
-- Each block starts with "-- name: <query_name>". Parameter: :patient_id

-- name: patients
SELECT id, name, diagnosis, start_date FROM patients ORDER BY id;

-- name: weekly_summary
-- Q1: symptom trend per week, shown next to that week's prednisone dose.
SELECT
    CAST((julianday(d.day) - julianday(p.start_date)) / 7 AS INTEGER) + 1 AS week,
    m.dose_mg                       AS prednisone_mg,
    COUNT(*)                        AS check_ins,
    ROUND(AVG(d.stools), 1)         AS avg_stools_per_day,
    ROUND(AVG(d.bristol), 1)        AS avg_bristol,
    SUM(d.blood)                    AS blood_days,
    SUM(d.urgency)                  AS urgency_days,
    SUM(d.nocturnal)                AS night_waking_days,
    SUM(d.review_items)             AS items_to_review
FROM daily_summary d
JOIN patients p ON p.id = d.patient_id
LEFT JOIN medication_doses m
       ON m.patient_id = d.patient_id AND m.drug = 'prednisone'
      AND d.day BETWEEN m.start_date AND m.end_date
WHERE d.patient_id = :patient_id
GROUP BY week
ORDER BY week;

-- name: dose_change_impact
-- Q2: symptoms in the 7 days before vs. after each prednisone step-down.
SELECT
    m.start_date                                                     AS change_date,
    prev.dose_mg                                                     AS from_mg,
    m.dose_mg                                                        AS to_mg,
    ROUND(AVG(CASE WHEN d.day <  m.start_date THEN d.stools END), 1) AS stools_before,
    ROUND(AVG(CASE WHEN d.day >= m.start_date THEN d.stools END), 1) AS stools_after,
    SUM(CASE WHEN d.day <  m.start_date THEN d.blood ELSE 0 END)     AS blood_days_before,
    SUM(CASE WHEN d.day >= m.start_date THEN d.blood ELSE 0 END)     AS blood_days_after
FROM medication_doses m
JOIN medication_doses prev
  ON prev.patient_id = m.patient_id AND prev.drug = m.drug
 AND date(prev.end_date, '+1 day') = m.start_date
JOIN daily_summary d
  ON d.patient_id = m.patient_id
 AND d.day BETWEEN date(m.start_date, '-7 days') AND date(m.start_date, '+6 days')
WHERE m.patient_id = :patient_id AND m.drug = 'prednisone'
GROUP BY m.id
HAVING COUNT(CASE WHEN d.day >= m.start_date THEN 1 END) > 0
ORDER BY m.start_date;

-- name: daily_trend
SELECT d.day, d.stools, d.bristol, d.blood, d.urgency, d.nocturnal, d.cramping, d.review_items,
       m.dose_mg AS prednisone_mg
FROM daily_summary d
LEFT JOIN medication_doses m
       ON m.patient_id = d.patient_id AND m.drug = 'prednisone'
      AND d.day BETWEEN m.start_date AND m.end_date
WHERE d.patient_id = :patient_id
ORDER BY d.day;

-- name: dose_schedule
SELECT drug, dose_mg, start_date, end_date FROM medication_doses
WHERE patient_id = :patient_id ORDER BY drug, start_date;

-- name: report_events
-- Every check-in with what was extracted: traceability from chart back to patient words.
SELECT r.id AS report_id, r.reported_at, r.raw_text,
       e.symptom, e.present, e.severity, e.count, e.bristol_type, e.evidence,
       e.needs_review, e.review_reason
FROM reports r
LEFT JOIN symptom_events e ON e.report_id = r.id
WHERE r.patient_id = :patient_id
ORDER BY r.reported_at DESC, e.id;
