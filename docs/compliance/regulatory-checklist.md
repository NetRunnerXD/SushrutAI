# Regulatory & launch checklist

Status legend: ✅ done in code · 🟡 partly done · ⬜ needs people/organisations outside engineering

| # | Item | Status | Owner / notes |
|---|---|---|---|
| 1 | DPDP itemised notice + granular consent + consent ledger | ✅ | `security/consent.py`, `consent_events` |
| 2 | Rights: access, correction, erasure (crypto-shred), portability (JSON + FHIR) | ✅ | `/me/export`, `/me/export/fhir`, `DELETE /me` |
| 3 | Grievance officer + DPO contact published | ⬜ | Operator to appoint; fill `GRIEVANCE_OFFICER_EMAIL` |
| 4 | Verifiable parental consent for users < 18 | 🟡 | `guardians` table exists; verification flow (DigiLocker / age token) not built |
| 5 | Data localisation for PHI | ✅ | Local-first; India-hosted APIs preferred; images never leave |
| 6 | Encryption at rest/in transit | 🟡 | Field + file encryption ✅; TLS termination (reverse proxy / Cloudflare Tunnel) at deployment |
| 7 | Row-level isolation | ✅ | Postgres RLS with non-superuser role (`infra/sql/01_app_role.sql`) |
| 8 | DPIA | 🟡 | `DPIA.md` draft — needs DPO/counsel sign-off |
| 9 | Incident runbook (CERT-In 6 h) | 🟡 | `incident-runbook.md` draft — needs named people + drill |
| 10 | VAPT by CERT-In empanelled auditor | ⬜ | Before public beta |
| 11 | CDSCO opinion on SaMD classification (Medical Devices Rules 2017) | ⬜ | Product positioned as information/triage; get written opinion |
| 12 | Telemedicine Practice Guidelines — no prescribing | ✅ | Prompt rules + medicine-advice filter + red-team tests |
| 13 | Clinician review of red-team set and silver Rx labels | ⬜ | `eval/safety_redteam.jsonl`, `data/eval/rx_mirage100/labels_silver_v1.json` |
| 14 | Prospective clinical validation (IEC approval; CTRI registration if applicable) | ⬜ | Partner hospital/clinic |
| 15 | ABDM: ABHA linking, PHR app certification (sandbox → production) | ⬜ | Register at sandbox.abdm.gov.in; FHIR export already in R4 |
| 16 | Licences: DDInter (NC), MMS-TTS (NC), MIRAGE (unknown), Qwen/MedGemma terms | 🟡 | `docs/licence-register.md` — replace NC data before commercial launch |
| 17 | Native-speaker review of UI translations (8 languages) | ⬜ | `apps/web/lib/messages.ts` |
| 18 | Phone OTP (Firebase project) / email OTP (SMTP) | ⬜ | Operator accounts; code ready |
