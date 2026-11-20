# Data Protection Impact Assessment — SushrutAI (draft v0.1, 2026-11-20)

> Working draft prepared by the engineering team for review by the Data Protection Officer and legal counsel.
> It describes the system as built; it is not legal advice. Law: Digital Personal Data Protection Act 2023
> ("DPDP Act") and DPDP Rules 2025 (phased commencement); IT Act 2000 / CERT-In Directions 2022; Telemedicine
> Practice Guidelines 2020; ICMR Ethical Guidelines for AI in Biomedical Research & Healthcare 2023.

## 1. Processing overview
| Item | Description |
|---|---|
| Data Fiduciary | Operator of the SushrutAI deployment (to be named before launch) |
| Purpose | Health *information* and triage support; reading prescriptions/lab reports; medicine price/generic lookup; reminders. **Not** diagnosis, prescribing or telemedicine. |
| Data principals | Adults using the app; children only via a verified guardian (DPDP s.9) |
| Personal data | Phone/email (login), date of birth, sex, state/district, health profile (allergies, conditions, medicines, vitals), prescriptions & lab reports (images + extracted data), chat/voice transcripts, consent records |
| Sensitive aspects | Health data; potentially children's data; voice recordings |
| Volume (MVP) | Single-site laptop deployment; beta target < 10,000 users |

## 2. Data flows and where data goes
| Flow | Processor | Location | Default |
|---|---|---|---|
| All AI (chat, triage, Rx/lab reading, image description, translation, STT, TTS) | Local open-weight models (Ollama, IndicConformer, Whisper, MMS-TTS, IndicTrans2) | Same machine/server | **On** |
| Speech fallback | Sarvam AI / Bhashini (India) | India | Off — needs `cloud_speech` consent |
| Complex-question fallback (de-identified text only) | Groq | Outside India | Off — needs `cloud_llm_deidentified` consent |
| Drug-label lookup (molecule name only, no personal data) | openFDA | USA | On (no personal data) |
| Phone OTP | Firebase Authentication | Google | Only if phone login used |

Images and lab reports are **never** sent to any external service (enforced in code: `PrivacyViolation`).
"Offline mode" (per user, or `OFFLINE_MODE=1` server-wide) blocks every cloud path.

## 3. Necessity & proportionality
- Data minimisation: the model sees a compact profile summary, not the full record; no Aadhaar collected.
- Guest mode works without any identifier; history is stored only with `history_storage` consent.
- Retention: guest data deleted on erasure; inactive-account purge (3 years, notice 48 h) — **to implement before launch**.

## 4. Risks and controls
| Risk | Likelihood / impact | Controls in place | Residual / to do |
|---|---|---|---|
| Unauthorised access to PHI | Medium / High | AES-256-GCM per-user envelope encryption; per-user DEK; Postgres row-level security (FORCE, non-superuser app role); short-lived JWTs; OTP rate limits | Pen-test (CERT-In empanelled auditor); key in KMS/OpenBao for production |
| Data leakage to third parties | Low / High | Local-first; consent-gated cloud tiers; PII scrubber (Aadhaar, ABHA, PAN, phone, email, names); images never leave | Presidio NER layer for names in Indic scripts |
| Harmful medical output | Medium / High | Deterministic red-flag rules before the model; Llama-Guard; urgency floor; medicine-advice filter; mandatory Rx confirmation; disclaimers; red-team suite in `eval/` | Clinician review of red-team set; prospective clinical validation |
| Inaccurate Rx/lab reading | High / Medium | Low-confidence flags, human confirmation, pharmacist referral | Larger verified eval set; fine-tuned reader |
| Children's data | Low / High | Guardian table; no behavioural tracking | Verifiable parental consent flow (DigiLocker / age token) — **to implement** |
| Breach | Low / High | Audit log; incident runbook (`incident-runbook.md`) | 24×7 contact; CERT-In reporting drill |

## 5. Data principal rights (implemented)
Access/portability (`GET /me/export`, FHIR `GET /me/export/fhir`), correction (profile/Rx edit), erasure with
crypto-shredding (`DELETE /me`), consent withdrawal (`DELETE /consents/{purpose}`), consent ledger
(`consent_events`). Grievance officer & nomination: **to publish before launch**.

## 6. Sign-off
| Role | Name | Date | Decision |
|---|---|---|---|
| DPO | | | |
| Legal counsel | | | |
| Clinical safety lead | | | |
