# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

(A responsive web app used on mid-range Android phones and on desktop browsers; no native app.)

## Users

Four groups, all first-class (confirmed by the owner). When their needs conflict, serve them through modes and settings rather than by favouring one:

- **Patients on their own phones:** adults in India managing their own health, often in Hindi or another Indian language, some with low literacy; voice is the main way in.
- **Family caregivers:** usually a grown child managing parents' and children's medicines, reports and vaccines through family profiles.
- **Elderly users:** older people using it largely on their own; they need big type, Simple mode and read-aloud.
- **Health workers:** ASHA/ANM-style workers using it with many patients in the field.

Common situation: someone is unwell, anxious or caring for someone who is, often on a small screen, sometimes with patchy connectivity, sometimes unable to read English.

## Product Purpose

A voice-first, multilingual health assistant for India. It helps people:

- understand symptoms and when to seek care;
- read prescriptions and lab reports;
- find cheaper generic medicines (Jan Aushadhi);
- take medicines on time;
- keep family health records;
- follow India's vaccine and pregnancy check-up schedules;
- prepare for a doctor visit.

Success means people get to the right care sooner, take medicines correctly, and spend less, without the app ever pretending to be a doctor.

## Positioning

It runs on the family's own computer: the AI models are local, and health data is encrypted per person and never leaves home unless the user consents to a named cloud service. It speaks 10 Indian languages (Hindi, English, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Odia) by voice. It grounds answers in Indian public sources: ICMR standard treatment workflows, MoHFW schedules, the CDSCO banned list, Jan Aushadhi prices.

## Operating Context

- **Deployment (confirmed):** self-hosted on the user's or family's own PC (Windows, 6 GB GPU, Ollama), started with `python main.py`; phones on the same network open it in a browser. "Your data stays at home" is literally true. Offline mode blocks every cloud call.
- **Real artefacts people bring:** handwritten and printed prescriptions, lab report printouts and PDFs, X-ray photos, the government MCP card, readings from home BP and sugar meters.
- **Real places it hands off to:**
  - ambulance 108 and emergency 112;
  - Tele-MANAS 14416 (mental health);
  - eSanjeevani (free government teleconsultation);
  - Jan Aushadhi Kendras;
  - ASHA/ANM workers and doctors.

## Capabilities and Constraints

- **Features:**
  - symptom chat with intent routing (no diagnosis for greetings or food questions);
  - an adaptive symptom checker with deterministic urgency;
  - prescription reading with line-by-line review;
  - lab report reading with reference ranges and trends;
  - medical image description (radiologist review always required);
  - a medicine catalogue with cheaper alternatives;
  - a dose tracker and reminders (calendar export);
  - family profiles (separate encryption per person);
  - vaccine and pregnancy check-up schedules;
  - a doctor-visit summary (print or PDF);
  - an SOS sheet with an emergency card;
  - voice actions (log a reading, mark a dose, navigate), each confirmed before saving;
  - universal search;
  - daily check-ins;
  - Simple mode.
- **Safety rules:**
  - deterministic red-flag rules and Llama Guard run before any model;
  - no dosing instructions and no definitive diagnoses;
  - emergencies short-circuit to 108/112.
- **Privacy rules:** per-user envelope encryption, purpose-based consent (DPDP Act), row-level security on Postgres, data export and crypto-shredding erasure.
- **Technical limits:**
  - small local models (4B), so answers are slower and less accurate than large cloud models;
  - a single GPU, so concurrent model work slows everything down.
- **Terminology:** "information, not a diagnosis"; "see a doctor today / this week"; "Jan Aushadhi"; "MCP card"; "ASHA".
- **Open decisions:**
  - native-speaker review of 8 of the 10 languages (only English and Hindi are complete);
  - a licensed brand-name medicine price dataset;
  - commercial licensing of DDInter and MMS-TTS (both non-commercial);
  - clinician review of the safety prompt set;
  - access to Indic Parler-TTS.

## Brand Commitments

- **Name:** SushrutAI, after Sushruta, the ancient Indian physician.
- **Never a doctor:** every surface says it gives health information, not a diagnosis, and points to real care: 108/112, eSanjeevani, a doctor, ASHA/ANM.
- **Voice:** plain, warm, short sentences in simple words, comfortable mixing English medical terms (in brackets) with Indian languages and Hinglish.
- **Not binding:** the current Field Notebook visual direction (paper/ink/turmeric) is today's design, not a brand commitment.

## Evidence on Hand

Measured results (reports in `eval/reports/`):

| Area | Result |
|---|---|
| Prescription reading (MIRAGE set, qwen3-vl) | About 48% medicine recall; hence the mandatory review step. |
| Lab report reading (MedOCR-Vision) | 91% recall, 99.4% of values exact. |
| Chest X-rays | Modality identified 100%; abnormality noted on 86.7% of pneumonia films, 20% false alarms on normal films. |
| Safety red-team (72 prompts) | 72/72 before round 3; 70/72 now, fix in progress. |
| MedMCQA | 51%. |
| Chat routing | 80/80 labelled; 20/24 on unseen phrases at first try. |

Data on hand:
- Jan Aushadhi catalogue: 2,453 products with prices.
- CDSCO banned list: 444 entries.
- DDInter interactions: 160,235 pairs (non-commercial licence).
- Knowledge base: 4,977 chunks (ICMR STW, MedlinePlus, drug labels for 214 generics, Indian guidelines).
- India's National Immunization Schedule (NHM) and the ICMR antenatal workflow, encoded with source links.

Absences future work must not fabricate:
- no real users, testimonials, ratings, clinical validation, partner organisations or press;
- no brand-name medicine prices;
- no certification claims.

## Product Principles

1. **Safety before helpfulness:** route anything urgent to real care first, never diagnose or change a dose, and always show uncertainty honestly.
2. **The person's data is theirs:** local by default, every cloud use consented and named, and export or erase any time.
3. **Speak first, read second:** every key action works by voice and is read aloud, in the person's own language.
4. **One household, many people:** caregivers act for others safely, and it's always clear whose record is open.
5. **Earn trust with sources:** answers cite Indian public sources; when there is none, say so.

## Accessibility & Inclusion

- WCAG 2.2 AA as the floor: 48px touch targets, visible focus, reduced motion respected.
- Large-text, high-contrast and Simple modes.
- Must work for low-literacy and elderly users and on mid-range Android phones at 360px width.
- Supports 10 Indian scripts, with line-height that doesn't clip vowel signs.
- Read-aloud and voice input everywhere, and captions for every spoken output.
