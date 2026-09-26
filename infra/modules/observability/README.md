# observability

Log groups `/jarvis/work`, `/jarvis/personal`, `/jarvis/cloud-init`, `/jarvis/ofw` (30 days,
jarvis CMK), the four metric filters and six alarms of DESIGN.md 7.2 and 7.3 plus the three ofw
filters and alarms of PLAN AD37 (`jarvis-ofw-login-failures`, `jarvis-ofw-layout-changed`,
`jarvis-heartbeat-ofw`; the disk and instance-status alarms exist only while `instance_enabled`), the CMK-encrypted `jarvis-alerts` topic with the policy of
DESIGN-IAM.md 3.11, the email subscription (`alert_email`, confirm the link), and the
`jarvis-monthly` $60 budget with ACTUAL 50/80/100 % notifications.
