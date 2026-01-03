# Reliability Run 2026-01-03T11:38:06.977253Z

- git_sha: a7b46cd698c9f37489472200c670d770a7058f0d
- model_id: unknown
- seed: 123
- scenarios: 112

## Metrics
- pass_rate: 0.9375
- date_extraction_rate: 0.6875
- customer_window_iou_avg: 0.9545
- query_window_iou_avg: 0.9545
- incident_window_iou_avg: 0.9167
- incident_detection_precision: 0.9818
- incident_detection_recall: 0.9
- schema_failure_rate: 0.0
- tool_error_rate: 0.0
- avg_latency_ms.triage: 11.5728
- avg_latency_ms.log: 15.8586

## Recent Failures
1. scenario_fail_0001 - incident_fn; incident_window_miss - customer_iou=1.0, incident_iou=0.1667
   text: Synthetic failing case: service down at 12:00 UTC today....
2. tag_incident_fp - incident_fp - customer_iou=0.0, incident_iou=0.0
   text: Synthetic canary: vague issue, no time given, but heavy errors in logs....
3. tag_incident_miss - incident_fn; incident_window_miss - customer_iou=1.0, incident_iou=0.1667
   text: Synthetic canary: outage at 12:00 UTC today (should be missed)....
4. tag_no_logs_guardrail - incident_fn; incident_window_miss - customer_iou=1.0, incident_iou=0.1667
   text: Synthetic canary: customer reports 10:00-12:00 UTC, but logs are empty. Should trigger claim-safety phrasing....
5. tag_time_shift - incident_fn; incident_window_miss - customer_iou=1.0, incident_iou=0.1667
   text: Synthetic canary: errors at 18:00 UTC, customer reported 10:00-12:00 UTC....
6. tag_time_window_miss_range - incident_fn; incident_window_miss - customer_iou=1.0, incident_iou=0.1667
   text: Synthetic canary: customer reports 09:00-10:00 UTC, errors actually at 18:00 UTC....
7. tag_timezone_miss - time_iou_low; incident_fn; incident_window_miss - customer_iou=0.0, incident_iou=0.1667
   text: Synthetic canary: customers reported 5pm PT (UTC-7) on May 03, but logs only show activity at 05:00 UTC....