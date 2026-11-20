# Security incident & personal-data breach runbook (draft)

> Draft for the DPO / security lead. Timelines reflect CERT-In Directions (28 Apr 2022, report within **6 hours**
> of noticing) and the DPDP Rules 2025 (intimate the Data Protection Board and affected Data Principals without
> delay; detailed report to the Board within 72 hours). Confirm against the current notified text before launch.

## 0. Roles
| Role | Who | Backup |
|---|---|---|
| Incident commander | | |
| DPO (Board & user notices) | | |
| CERT-In point of contact | | |
| Engineering on-call | | |

## 1. Detect (T+0)
Signals: audit_log anomalies (bulk exports, many erasures), auth failures/OTP abuse, unexpected outbound
traffic from the model host, DB access outside the app role, reports from users/researchers.

## 2. Contain (T+0 → T+2h)
1. Revoke credentials: rotate `JWT_SECRET` (logs everyone out), DB passwords, API keys (Groq/Sarvam/SMTP).
2. Turn on `OFFLINE_MODE=1` to cut all cloud paths.
3. Snapshot affected systems (DB, logs) for forensics **before** changing them further.
4. If the master key (KEK) may be exposed: generate a new KEK and re-wrap all user DEKs.

## 3. Report (≤ 6 h to CERT-In)
- CERT-In: incident report form at https://www.cert-in.org.in (email: incident@cert-in.org.in). Keep logs
  for 180 days (CERT-In requirement) — configure log retention accordingly.
- Data Protection Board: intimation without delay; detailed report within 72 h (nature, extent, timing, likely
  impact, mitigation, remedial steps).

## 4. Notify affected users (without delay)
Plain-language notice in the user's preferred language: what happened, what data, likely consequences,
what we did, what they can do, contact of the DPO. Template: `breach-notice-template.md` (to be written).

## 5. Recover & review
Root-cause analysis, fixes, tests added to CI, DPIA updated, post-incident report filed.
