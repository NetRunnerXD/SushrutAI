# SushrutAI — Voice-First Indic Medical Assistant: Build Plan (local-first + free APIs)

## Context

`D:\Project\Scaler Health` is empty, so this is a greenfield build. SushrutAI is a voice-first health information dashboard for Indian users. It works in 10 languages (hi, en, bn, ta, te, mr, gu, kn, ml, or) and covers prescription OCR, a patient profile, multimodal health chat, generic-medicine alternatives, adaptive symptom triage and dosage guidance. All data sources are real, and it is designed to comply with the DPDP Act 2023 and the DPDP Rules 2025.

**Revised constraints (per your feedback):**
- **Models run locally on this PC:**
  - GPU: NVIDIA RTX 3060 Laptop, **6 GB VRAM**
  - CPU: AMD Ryzen 9 5900HX (8C/16T)
  - RAM: **32 GB**
  - OS: Windows 11
  - Disk: D: has 224 GB free; **C: has only 20 GB free**, so all models, Docker data and datasets go on D:.
- Otherwise the stack uses **free or freemium APIs only**: free tiers, free government APIs and starter credits. No paid infrastructure is required to build and run the MVP.
- The architecture still scales to a real deployment later: every model sits behind an OpenAI-compatible provider interface, so a local 4B model can later be swapped for a hosted 27B model without code changes.

**Positioning (this drives regulatory scope):** SushrutAI provides *health information and triage support*. It does not diagnose or prescribe. The UI says "possible conditions to discuss with a doctor", never "diagnosis".

**Deliverable after approval:** a monorepo scaffold, then Phase 0–1 (the MVP) as laid out in the roadmap below, running entirely on this laptop.

---

## 1. Design principle: local-first, cloud-optional, privacy-tiered

Every AI capability goes through a **provider router** (LiteLLM proxy for LLMs, plus small adapter classes for STT, TTS, OCR and translation). Each call is tagged with a privacy tier:

| Tier | Where it runs | What may be sent | Default for |
|---|---|---|---|
| **L — Local** | This PC (Ollama, llama.cpp, CPU/GPU Python services) | Anything, including PHI | All PHI: chat, Rx images, profile-aware answers, medical images |
| **IN — India-hosted free API** | Bhashini (Govt of India), Sarvam AI (free credits) | De-identified text/audio, only with `cloud_speech` consent | Indic STT/TTS/translation when local quality or speed is insufficient |
| **C — Global free API** | Groq free tier, Gemini API free tier, OpenRouter `:free` models, HF Inference credits | **Public / non-PHI only**: KB translation, eval generation, synthetic-free reformatting of public text. Never PHI. Gemini's free tier may use prompts to improve Google products, so it never receives user data. | Offline batch jobs on public knowledge |

PII is stripped with **Microsoft Presidio**, extended with custom Indic recognizers for Aadhaar, ABHA, PAN, Indian phone numbers and names in Indic scripts, before anything leaves Tier L. The user can switch to **"Offline mode"**, which forces Tier L for everything.

---

## 2. System Architecture

```
 ┌─────────────────────── Client: Next.js PWA (localhost:3000) ───────────────────────┐
 │ Voice orb · Chat · Rx upload/camera · Profile · History · Medicine search          │
 │ Browser fallbacks: Web Speech API (STT/TTS), WebCrypto (guest-mode encryption)     │
 └──────────────┬──────────── WebSocket (audio + tokens) ────── REST/SSE ─────────────┘
                │                                              │
       ┌────────▼────────────────────── FastAPI (api) ─────────▼───────────────────┐
       │ auth/OTP · consent · profile · documents · chat · triage · medicines       │
       │ Orchestrator (LangGraph): red-flag rules → lang-ID → translate → intent →  │
       │   tools (SQL drug DB, RAG, profile) → LLM → grounding check → render       │
       └──┬──────────┬───────────┬──────────────┬──────────────┬─────────────────────┘
          │          │           │              │              │
  ┌───────▼───┐ ┌────▼──────┐ ┌──▼──────────┐ ┌─▼───────────┐ ┌▼────────────────────┐
  │ LiteLLM   │ │ Speech svc│ │ OCR worker  │ │ Translate   │ │ Postgres16+pgvector │
  │ proxy     │ │ faster-   │ │ (arq/Redis) │ │ IndicTrans2 │ │ Redis · MinIO        │
  │  ├Ollama  │ │ whisper,  │ │ Surya,      │ │ dist-200M   │ │ (all Docker, on D:)  │
  │  │(GPU)   │ │ Indic-    │ │ PaddleOCR,  │ │ (CPU, CT2)  │ └──────────────────────┘
  │  ├Groq*   │ │ Conformer │ │ TrOCR,      │ └─────────────┘
  │  └Sarvam* │ │ Piper/    │ │ Qwen2.5-VL  │   * = optional free API, privacy-tiered
  └───────────┘ │ IndicTTS, │ │ via Ollama  │
                │ Bhashini* │ └─────────────┘
                └───────────┘
```

### 2.1 GPU memory budget (6 GB VRAM: the binding constraint)

Only **one GPU-resident LLM/VLM** at a time. Everything else runs on CPU, which works well on 16 threads with 32 GB RAM.

| Workload | Device | Approx. memory |
|---|---|---|
| MedGemma-4B-it Q4_K_M + vision projector (Ollama) | GPU | ~3.5 GB VRAM |
| KV cache (8k ctx, q8 cache) | GPU | ~1 GB |
| Qwen2.5-VL-3B Q4 (Rx reader; Ollama swaps it in on demand) | GPU | ~3 GB when loaded |
| faster-whisper large-v3-turbo int8 | CPU (or GPU when the LLM is idle) | ~1.6 GB RAM |
| IndicConformer-600M (ONNX) | CPU | ~2.5 GB RAM |
| IndicTrans2 dist-200M ×2 (CTranslate2 int8) | CPU | ~1 GB RAM |
| bge-m3 embeddings + bge-reranker-v2-m3 | CPU | ~3 GB RAM |
| Surya / PaddleOCR / TrOCR | CPU (Surya can use GPU between LLM calls) | ~3 GB RAM |
| Indic Parler-TTS / Piper | CPU | ~3 GB / 0.1 GB RAM |
| Postgres, Redis, MinIO, API, web | CPU | ~3 GB RAM |

Ollama settings: `OLLAMA_MODELS=D:\ollama\models`, `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_KEEP_ALIVE=10m`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `OLLAMA_FLASH_ATTENTION=1`.

---

## 3. Recommended Tech Stack (all free / open source)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind, shadcn/ui, next-intl, Serwist (PWA) | Noto Sans font family for all Indic scripts |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, LangGraph | |
| Job queue | **arq** (async, Redis) | Lighter than Celery on a laptop |
| DB | **PostgreSQL 16 + pgvector + pgcrypto** (Docker) | One DB for PHI, drug master and vectors (fewer services). pgvector HNSW + `tsvector` for hybrid search. |
| Cache/queue | Redis 7 (Valkey) | |
| Object store | MinIO (Docker; data dir on D:) | S3-compatible, so moving to cloud later is trivial |
| Fuzzy drug search | Postgres `pg_trgm` + `fuzzystrmatch` (dmetaphone) | No separate Meilisearch needed |
| LLM runtime | **Ollama for Windows** (llama.cpp, CUDA) | Native Windows, OpenAI-compatible API, vision support |
| LLM gateway | **LiteLLM proxy** | Routes to Ollama, Groq, Sarvam and OpenRouter with fallbacks and privacy-tier enforcement |
| Auth | Custom FastAPI auth (JWT + refresh). Email OTP via **Brevo free (300 emails/day)** or Gmail SMTP. Phone OTP via **Firebase Authentication** (Spark plan: free daily SMS quota, and Firebase handles Indian DLT compliance). | Signup is optional, and guest mode is free |
| Secrets/encryption | `cryptography` (AES-256-GCM envelope). Master key from the Windows DPAPI keyring locally; OpenBao later. | |
| PII scrubbing | Microsoft Presidio + custom Indic recognizers | |
| Observability | Langfuse (self-hosted Docker, optional), Python logging + OpenTelemetry | |
| Containers | Docker Desktop + WSL2 (data root on D:), with NVIDIA GPU passthrough for the CUDA services | |
| Fine-tuning compute | **Kaggle Notebooks** (free GPU hours/week, T4×2 or P100) and **Google Colab free** (T4); **Unsloth** QLoRA | Local 6 GB is fine for QLoRA on ≤4B models for small runs |
| Annotation | Label Studio (Docker) | |

---

## 4. LLM / VLM Selection (local, 6 GB VRAM)

| Role | Local primary (Ollama, GGUF Q4_K_M) | Free-API fallback (Tier IN/C rules apply) | Why |
|---|---|---|---|
| **Clinical reasoning + medical vision** | **google/medgemma-4b-it** (Gemma 3 base, medical post-training; multimodal CXR/derm/fundus/pathology; GGUF from `unsloth/medgemma-4b-it-GGUF` imported via Modelfile) | Groq `openai/gpt-oss-120b` or `llama-3.3-70b-versatile` (free tier), for **de-identified, consented** hard cases only | Best medical model that fits 6 GB with vision. Weaker reasoning than 27B, so we compensate with RAG, SQL tools and a structured schema. |
| **Deep-reasoning mode (local, slower)** | **openai/gpt-oss-20b** (MoE, 3.6B active, MXFP4 ~13 GB; GPU+CPU offload using the 32 GB RAM, expect ~8–15 tok/s) | — | Optional "think carefully" path that stays fully local. Behind a feature flag, and benchmarked before enabling. |
| **Indic surface / translation** | **ai4bharat IndicTrans2 distilled 200M** (en-indic + indic-en) on CPU via CTranslate2; **Gemma-3-4B-it** (strong multilingual) for natural Indic phrasing when needed | **Sarvam-M** via Sarvam API (free starter credits; India-hosted); **Bhashini NMT** (free, Govt of India) | IndicTrans2 covers all 22 scheduled languages. Sarvam-M is trained on exactly our 10 languages, including romanized and code-mixed text. |
| **Prescription / document VLM** | **Qwen2.5-VL-3B-Instruct** (Ollama `qwen2.5vl:3b`), later LoRA-tuned on our Rx data via Unsloth on Kaggle; `qwen2.5vl:7b` Q4 (~6 GB) as a slower high-accuracy pass with partial offload | — (Rx images are PHI and never go to free cloud APIs) | Strong document understanding, emits JSON |
| **Safety classifier** | **Llama-Guard-3-1B** (Ollama `llama-guard3:1b`, ~1 GB) | — | Small enough to run alongside |
| **Embeddings / rerank** | **BAAI/bge-m3** + **bge-reranker-v2-m3** (CPU, sentence-transformers / FlagEmbedding) | — | Multilingual, including Indic |

**How a turn flows (local):**
1. A red-flag rule engine runs first. It is deterministic and needs no model.
2. Language ID with IndicLID (or fastText `lid.176`), then IndicTrans2 indic→en. The original text is kept.
3. MedGemma-4B gets the English text, a compact profile summary and the top 6 RAG chunks, and emits **constrained JSON** (Ollama `format` = JSON schema).
4. A grounding check: MedGemma verifies its own claims against the cited chunks in a second short pass. Unsupported claims are dropped.
5. Templated rendering, with IndicTrans2 en→indic for free-text parts. Medical terms keep the English term in brackets.

**Model choice is eval-gated** (§15). In Phase 0 we benchmark on MedMCQA (Indian PG entrance questions) and on a clinician-reviewed Hindi/Bengali medical QA set:
- MedGemma-4B vs Gemma-3-4B vs Qwen3-4B vs gpt-oss-20b (local)
- Qwen2.5-VL-3B vs 7B on our Rx set

We also re-check newer small open-weight releases at each phase gate.

---

## 5. Free / Freemium APIs used

| Service | Free tier | Used for | Tier |
|---|---|---|---|
| **Bhashini / ULCA** (Govt of India, MeitY) | Free after registration | Indic ASR, NMT, TTS in 22 languages, as fallback for local speech | IN |
| **Sarvam AI** | Free starter credits | Saarika/Saaras STT, Bulbul TTS, Mayura translation, Sarvam-M chat. These are the best-quality Indic voice options. | IN |
| **Groq** | Free rate-limited tier | Whisper-large-v3 STT (fast), gpt-oss-120b / Llama-3.3-70B for de-identified hard cases | C (consented, de-identified) |
| **Google Gemini API** | Free tier (data may be used for product improvement) | **Public-content batch jobs only**, e.g. drafting KB translations for clinician review | C (no user data) |
| **OpenRouter** | `:free` models | Alternative fallback for public-content jobs | C |
| **Hugging Face** | Free monthly inference credits; free model and dataset downloads | Model and dataset hosting | — |
| **Firebase Authentication** | Spark plan, free daily SMS quota | Phone OTP | — |
| **Brevo / Resend** | 300 emails/day / 3,000 emails/month | Email OTP | — |
| **openFDA API** | Free (a free key raises limits) | Drug labels: ADRs, contraindications, warnings | public |
| **NLM RxNav / RxNorm API** | Free | Ingredient normalization | public |
| **MedlinePlus Connect + Web Service** | Free | Patient-education content by code | public |
| **NCBI E-utilities** | Free (a free key raises limits) | PubMed / PMC OA retrieval for the KB | public |
| **WHO ICD-11 API** | Free with registration | Coding of conditions | public |
| **OpenStreetMap Nominatim / Overpass** | Free (fair use) | Pharmacy / Jan Aushadhi Kendra locator | public |
| **Web Speech API** (Chrome/Edge) | Free, in browser | Zero-setup STT/TTS fallback (hi-IN, bn-IN, ta-IN, te-IN, mr-IN, gu-IN, kn-IN, ml-IN). Audio goes to the browser vendor, so it needs consent and is off by default. | C |
| **Cloudflare Tunnel** | Free | Expose the local app over HTTPS for demos and phone testing | — |
| **Oracle Cloud Always Free** (Mumbai/Hyderabad regions) | 4 ARM cores, 24 GB RAM, free forever | Optional India-hosted backend + Postgres for a public beta (CPU models only) | — |
| **Vercel Hobby** | Free | Optional frontend hosting | — |
| **Kaggle / Colab** | Free GPU hours | Fine-tuning TrOCR, Qwen2.5-VL-3B Rx-LoRA, STT lexicon adaptation | — |

All API keys live in `.env` (git-ignored). Each adapter degrades gracefully to local when a key is missing or a quota is exhausted.

---

## 6. Datasets & Data Sources (real only; licence verified before use)

### 6.1 Medicine & pharmacy (India)
| Source | Use | Access |
|---|---|---|
| **PMBJP Jan Aushadhi product list + MRP** (janaushadhi.gov.in), about 2,000 generics | Generic alternatives and prices | Public |
| **Jan Aushadhi Kendra store list** | Store locator | Public |
| **NPPA ceiling prices (DPCO 2013)** (nppaindia.nic.in) | Price ceilings | Public |
| **NLEM 2022** | Essential-medicine flags, dosage forms | Public PDF |
| **CDSCO banned FDC list and approved drugs** | Banned-combination warnings | Public |
| **openFDA labels / DailyMed SPL** | ADRs, contraindications, warnings, administration | Public domain |
| **RxNorm** | Ingredient normalization | Free (UMLS licence) |
| **DDInter 2.0** | Drug–drug interactions | Free download (academic; confirm terms) |
| **WHO ATC/DDD index** | Classification | Free for non-commercial use |
| **SIDER 4.1** | Side-effect frequencies (eval only) | CC BY-NC-SA |
| **National Formulary of India (IPC)** | India-specific dosing | Paid licence. Deferred; until then openFDA + NLEM + ICMR STW cover dosing. |
| **Indian brand→composition map** | Brand-to-generic lookup | No clean free source. **Plan:** (a) build our own map from Jan Aushadhi, NPPA notified formulations (which list brand + composition) and CDSCO records; (b) Kaggle "A-Z Medicine Dataset of India" (scraped from 1mg) for bootstrapping and validation only, because its ToS risk rules it out of production; (c) users and pharmacists can add or verify brands, with moderation. |

### 6.2 Clinical knowledge (RAG)
- **ICMR Standard Treatment Workflows**, MoHFW/NHM STGs, NTEP (TB), NCVBDC (malaria, dengue), NP-NCD (diabetes, hypertension).
- **MedlinePlus** (public domain), WHO fact sheets (check licence), PMC Open Access (commercial-use subset), StatPearls (check per-chapter licence).
- **ICMR-NIN Dietary Guidelines for Indians 2024** and **IFCT 2017** (Indian food composition).
- **Ontologies:** SNOMED CT (free in India via NRCeS), ICD-10/11, LOINC (free), **HPO** disease–phenotype annotations (free).

### 6.3 Speech
**IndicVoices** (AI4Bharat, CC BY 4.0), the **Vistaar** benchmark, Kathbath, Shrutilipi, Google FLEURS, Mozilla Common Voice. Medical-term speech in Indic languages does not exist publicly, so we will record a consented in-house set: clinician-scripted symptom phrases read by volunteers.

### 6.4 OCR / handwriting
**IIIT-INDIC-HW-WORDS** (8 Indic scripts), **IIIT-HW-Dev**, **IAM Handwriting** (most Indian Rx are handwritten in English), the **Doctor's Handwritten Prescription BD** dataset (Kaggle), IIIT-ILST, AI4Bharat IndicSTR12. **Core gap:** there is no public Indian prescription dataset, so we run a consented, de-identified collection with Label Studio annotation. This is the data moat.

### 6.5 Medical imaging (eval/research)
CQ500 (Indian head CT), NIH ChestX-ray14, CheXpert, VinDr-CXR, Shenzhen and Montgomery TB CXR, VQA-RAD, SLAKE. These are mostly non-commercial, so they are used for evaluation only.

### 6.6 QA / eval
**MedMCQA** (AIIMS/NEET-PG questions), MedQA, PubMedQA, MMLU-medical, MILU (Indic). **DDXPlus** is *simulated* patients, so it is used only to unit-test the triage policy, never as a source of medical facts.

---

## 7. OCR Pipeline (printed + handwritten Indian prescriptions; all local)

```
Upload/camera → 0 Pre-check → 1 Image prep → 2 Layout → 3 Region OCR → 4 VLM read → 5 Fuse+normalize → 6 Validate → 7 Human confirm
```

0. **Client pre-check:** blur (variance of Laplacian), glare and resolution checks with live "hold steady" feedback.
1. **Prep (OpenCV, CPU):** perspective correction, deskew, CLAHE, denoise, binarization for printed regions. PDFs are rasterized with PyMuPDF at 300 dpi.
2. **Layout + script detection:** **Surya** layout model detects the letterhead, patient block, **℞ medication block**, investigations, advice and signature/stamp. Surya also detects the script of each region.
3. **Region OCR (routed):**
   - Printed text, Latin and Indic: **Surya OCR** (90+ languages), with **PaddleOCR PP-OCRv5** as an ensemble member and **Tesseract 5** (Indic `traineddata`) as a CPU baseline.
   - Handwritten Latin: **microsoft/trocr-base-handwritten**, fine-tuned on IAM + BD-Rx + our data (on Kaggle).
   - Handwritten Indic: a TrOCR/CRNN fine-tuned on IIIT-INDIC-HW-WORDS.
   - Each model outputs text, confidences and top-k beams.
4. **VLM structured read:** **Qwen2.5-VL-3B** (Ollama, with JSON schema `format`) receives the image plus the OCR hints and outputs:
   ```json
   {"doctor":{},"date":"","diagnosis":[{"text":"","icd10":""}],
    "medications":[{"raw":"","brand":"","generic":"","strength":"","form":"TAB|CAP|SYP|INJ|OINT|DROPS",
      "frequency_raw":"1-0-1","frequency":{"morning":1,"afternoon":0,"night":1},
      "timing":"after_food|before_food|empty_stomach|HS|SOS","duration":"5 days","confidence":0.0}],
    "tests":[{"raw":"","loinc":""}],"advice":[],"follow_up":""}
   ```
   Low-confidence medication lines are re-read with `qwen2.5vl:7b` Q4 (slower, more accurate).
5. **Fusion and normalization (India-specific):**
   - **Abbreviation lexicon:** OD, BD, TDS, QID, HS, SOS, AC/PC, STAT, 1-0-1 notation, ½ tab, "x 5d", Tab./Cap./Syp., Hindi "खाने के बाद", etc.
   - **Drug-name resolution** against the brand+generic DB. Scoring combines `pg_trgm` similarity, dmetaphone, an OCR confusion matrix (`l/1`, `rn/m`), and a strength/form plausibility check ("Pan 40" is valid; "Pan 650" is not).
   - **Clinical NER:** scispaCy/medspaCy → SNOMED/ICD-10. Test names (CBC, LFT, KFT, HbA1c, TSH, USG, 2D Echo) → LOINC.
6. **Validation:** max dose, CDSCO banned-FDC list, duplicate therapy, DDInter interactions, allergy cross-check against the profile, and LASA (look-alike/sound-alike) warnings.
7. **Mandatory human confirmation:** a side-by-side view of image boxes and editable fields, with low-confidence fields in amber. User corrections are stored with consent as training data in an active-learning loop.

**Targets:** printed field accuracy ≥95%. Handwritten drug-name top-3 ≥90% after fine-tuning. Laptop parse time ≤30 s, run as an async job with progress streamed over WebSocket.

---

## 8. RAG Architecture

- **Store:** pgvector (HNSW) + Postgres full-text (`tsvector`, `simple` config for Indic) for hybrid search, fused with Reciprocal Rank Fusion. Rerank with bge-reranker-v2-m3 on CPU.
- **Collections** (one table with a `collection` column):
  - `guidelines_in`: ICMR STW / NHM / NTEP / NCVBDC; section-aware chunks of 400–800 tokens with breadcrumbs.
  - `drug_monographs`: openFDA/DailyMed sections keyed by generic + ATC.
  - `patient_ed`: MedlinePlus + WHO, pre-translated into 10 languages with IndicTrans2 and spot-checked by clinicians.
  - `nutrition_in`: ICMR-NIN 2024 + IFCT rows.
  - `literature`: PMC OA commercial subset (small, curated).
  - `user_history`: per-user, filtered by `user_id` under RLS, with encrypted payloads.
- **Structured facts are never vectorized:** prices, compositions, dose tables and interactions are SQL **tools**, to prevent numeric hallucination.
- **Retrieval:** rewrite the query (English + original language) → hybrid top 40 → rerank to top 6 → MedGemma must cite `[S#]` → grounding check.
- **Ingestion:** Python scripts in `pipelines/` with source YAML; content hashes and versions; scheduled locally with Windows Task Scheduler or an APScheduler job. Guidelines refresh monthly, prices weekly.

---

## 9. Feature Specifications

**F1 Voice-first:**
- Push-to-talk and hands-free (VAD) modes, a live transcript with tap-to-correct, and a language chip (auto-detected, user can override).
- Read-aloud for all answers, a large-text mode, and an "Offline mode" toggle.

**F2 Prescription parsing:** a structured Rx card (pipeline in §7), "Add to my medicines", reminders via Web Push, and an "explain my prescription" voice summary.

**F3 Patient profile:**
- Signup is optional. Guest data lives only in the browser, in IndexedDB encrypted with WebCrypto.
- Login by phone OTP (Firebase) or email OTP.
- Fields:
  - **Demographics:** DOB/age, sex at birth, gender (optional), state/district.
  - **Clinical:** blood group, allergies with reaction and severity, chronic conditions (SNOMED), current medications, surgeries, family history.
  - **Women's health:** pregnancy/lactation.
  - **Lifestyle:** smoking (pack-years), smokeless tobacco (gutka/khaini), alcohol, diet (veg/non-veg/vegan/Jain), activity.
  - **Other:** vaccinations, emergency contact.
  - **Vitals time series:** BP, HR, SpO₂, temperature, glucose (F/PP/R), weight, height, **BMI with Asian cut-offs** (≥23 overweight, ≥25 obese), waist.

**F4 Health assistant (chat + vision):**
- Accepts text, voice, images and lab-report PDFs.
- Output, in order:
  - Red-flag banner (108/112) when triggered.
  - Ranked possible conditions, each with a likelihood band, urgency, supporting and opposing findings, and citations.
  - Suggested tests, with CGHS rate-list price bands.
  - Specialist referral.
  - Indian-diet nutrition and lifestyle advice.
  - Disclaimer.
- Images go through MedGemma-4B: the modality is classified first, unsupported types are declined, and findings are given as plain-language descriptions with uncertainty plus "get a radiologist's review".
- Everything is logged to the patient timeline (consent-gated).

**F5 Generic alternatives:**
- Resolve brand → composition (molecule + strength + form + release type).
- Match **exact composition, strength, form and release** and rank by per-unit price.
- Always show the Jan Aushadhi equivalent, the NPPA ceiling price, and the source and date of each price, plus a nearest-Kendra locator (OSM).
- Hard-coded warning list for narrow-therapeutic-index drugs (levothyroxine, warfarin, phenytoin, tacrolimus…): "switch only on a doctor's advice".

**F6 Adaptive identification:**
- **State:** a `TriageState` holding the profile, positive and negative findings, candidate posteriors and red flags.
- **Candidates:** MedGemma proposes up to 15. These are reweighted by a **symptom–disease graph** (built from HPO, SNOMED and ICMR STW features) and by age/sex/region/season priors (NFHS-5, ICMR-INDIAB, NCVBDC endemicity).
- **Next question:** chosen by **expected information gain**.
  - Red-flag questions always come first.
  - Personalization filters: no pregnancy questions for males; weighting by age and chronic conditions.
  - Each question is phrased in the user's language with answer chips.
- **Stopping rule:** stop when top posterior >0.6 with a margin above 0.25, after 12 questions, or when a red flag fires.

**F7 Dosage & usage:**
- An SQL lookup first (openFDA/DailyMed sections + NLEM + ICMR STW dose tables), then an LLM explanation.
- Covers: timing, food relation, administration (don't crush SR), missed dose, duration, common and serious ADRs, contraindications, pregnancy/lactation, interactions with the user's current meds (DDInter), alcohol and food interactions, storage.
- Personalized checks: allergy cross-reactivity, Beers criteria for ages ≥65, renal/hepatic flags.
- The assistant never advises starting, stopping or changing doses.

---

## 10. API Endpoint Design (`/api/v1`, OpenAPI 3.1)

- **Auth & consent:**
  - `POST /auth/otp/request|verify` (phone via Firebase token exchange; email OTP) · `POST /auth/guest` · `POST /auth/refresh` · `POST /auth/logout`
  - `GET/POST /consents` · `DELETE /consents/{purpose}` · `GET /privacy/notice?lang=`
- **Profile:**
  - `GET/PUT /me/profile` · `DELETE /me` (erasure + crypto-shred) · `GET /me/export` (JSON + FHIR bundle)
  - `GET/POST /me/allergies` · `/me/conditions` · `GET/POST/PATCH/DELETE /me/medications/{id}` · `GET/POST /me/vitals`
- **Documents/Rx:**
  - `POST /documents` · `GET /documents/{id}`
  - `POST /prescriptions/parse` → `job_id` · `GET /jobs/{id}` · `WS /jobs/{id}/events`
  - `GET/PATCH /prescriptions/{id}` · `POST /prescriptions/{id}/confirm` · `POST /reports/parse`
- **Assistant:**
  - `POST /chat/sessions` · `GET /chat/sessions[/{id}]` · `POST /chat/sessions/{id}/messages` (SSE stream)
  - `POST /images/analyze`
  - `POST /triage/sessions` · `POST /triage/sessions/{id}/answer` · `GET /triage/sessions/{id}`
- **Medicines:**
  - `GET /medicines/search?q=` · `GET /medicines/{id}` · `GET /medicines/{id}/alternatives`
  - `GET /medicines/{id}/guidance` · `POST /medicines/interactions` · `GET /janaushadhi/stores?lat=&lng=`
- **Voice:** `WS /voice/stream` (PCM/Opus in, transcript + tokens + TTS audio out) · `POST /stt` · `POST /tts`
- **System:** `GET /system/providers` (which local models and free APIs are available, plus quota status) · `/healthz` · `/readyz`

---

## 11. Database Schema (PostgreSQL; PHI columns `*_enc` = AES-256-GCM with a per-user DEK)

```sql
users(id uuid pk, phone_hash, email_hash, phone_enc, email_enc, preferred_lang, is_guest, dek_wrapped, created_at, last_login_at)
consents(id, user_id, purpose enum('core_service','history_storage','model_improvement','cloud_speech','cloud_llm_deidentified'),
         notice_version, granted_at, withdrawn_at)   -- + consent_events append-only
guardians(id, minor_user_id, guardian_user_id, verified_at)

patient_profiles(user_id pk, dob_enc, sex, gender_enc, blood_group, height_cm, state_code, district_code, diet,
                 smoking_status, pack_years, tobacco_chewing, alcohol_units_wk, activity_level, pregnancy_status,
                 lactating, emergency_contact_enc, updated_at)
allergies(id, user_id, substance_code, substance_text_enc, type, reaction_enc, severity)
conditions(id, user_id, snomed_code, icd10, display_enc, status, onset_date, source, source_ref)
medications(id, user_id, product_id, generic_id, dose, frequency jsonb, timing, route, start_date, end_date,
            prescription_item_id, status, reminders jsonb)
vitals(id, user_id, type, value numeric, unit, measured_at, source)   -- index (user_id,type,measured_at)

documents(id, user_id, kind, object_key, sha256, mime, pages, uploaded_at, deleted_at)
ocr_runs(id, document_id, pipeline_version, status, raw_ocr jsonb, vlm_json jsonb, timings jsonb, error)
prescriptions(id, user_id, document_id, doctor_name_enc, doctor_reg_no_enc, clinic_enc, rx_date, diagnosis jsonb,
              advice_enc, follow_up, status enum('draft','confirmed'))
prescription_items(id, prescription_id, raw_text, product_id, generic_id, strength, form, dose, frequency jsonb,
                   timing, duration_days, confidence, user_corrected)
prescription_tests(id, prescription_id, raw_text, loinc_code)
lab_results(id, user_id, document_id, loinc_code, value, unit, ref_low, ref_high, flag, observed_at)
image_analyses(id, document_id, modality, model_version, findings jsonb, created_at)

chat_sessions(id, user_id, lang, channel, started_at, summary_enc)
messages(id, session_id, role, content_enc, lang, attachments uuid[], structured jsonb, citations jsonb,
         safety jsonb, provider text, model text, privacy_tier text, latency_ms, created_at)
triage_sessions(id, user_id, chat_session_id, state jsonb, status, red_flag, outcome jsonb)
assistant_findings(id, user_id, session_id, kind, code, display, likelihood_band, urgency)
feedback(id, message_id, rating, reason, comment_enc)

-- drug schema (public data)
generics(id, inn_name, atc_code, rxnorm_cui, nlem bool, nti bool)
compositions(id, hash uniq) ; composition_items(composition_id, generic_id, strength_value, strength_unit)
products(id, brand_name, manufacturer, composition_id, form, release_type, pack_size, schedule, is_janaushadhi, source, verified bool)
prices(product_id, mrp_inr, per_unit_inr, ceiling_price_inr, source, source_url, observed_on)
monographs(generic_id, section, content, source, version, lang)
interactions(generic_a, generic_b, severity, mechanism, management, source)
banned_fdcs(composition_id, notification_ref, date) ; abbreviations(token, expansion, category, lang)
janaushadhi_stores(id, code, name, address, state, district, lat, lng)

-- knowledge
kb_chunks(id, collection, source_id, lang, title, breadcrumb, content, tsv tsvector, embedding vector(1024), version, valid_from)

-- governance
audit_log(id, at, actor, action, resource, resource_id, purpose, result)   -- append-only
data_requests(id, user_id, type enum('access','correction','erasure','nominate','grievance'), status, opened_at, closed_at)
```

Row-Level Security on every user-owned table: `user_id = current_setting('app.user_id')::uuid`.

---

## 12. Voice Pipeline (local-first)

```
Browser mic → AudioWorklet (16 kHz PCM) → WS /voice/stream
  → Silero VAD (ONNX, CPU): endpointing ~600 ms, barge-in
  → Language ID (chip / IndicLID)
  → STT router:
       Indic: ai4bharat/indic-conformer-600m-multilingual (ONNX/CPU, chunked)
              └─ fallback: Bhashini ASR (free) → Sarvam Saarika (credits)   [consent: cloud_speech]
       English / Hinglish: faster-whisper large-v3-turbo int8 (CPU 16 threads; GPU if idle),
              hotwords = drug lexicon └─ fallback: Groq whisper-large-v3 (free tier)
  → Drug-term post-correction (n-best vs pg_trgm lexicon)
  → Orchestrator (token stream)
  → Sentence chunker ("।" aware) + Indic number/unit verbalizer ("650 mg" → "छह सौ पचास मिलीग्राम")
  → TTS router:
       Local: ai4bharat/indic-parler-tts (CPU; quality) | AI4Bharat IndicTTS FastPitch+HiFiGAN (faster) | Piper (English)
       Fallback: Bhashini TTS (free) → Sarvam Bulbul (credits) → browser speechSynthesis
  → audio chunks back over WS; stop on barge-in
```

- **Realistic laptop latency:** about 2–4 s to first audio when local. That drops to ~1.5 s with the Bhashini/Sarvam fallback. The UI shows a live transcript and streaming text, so it feels responsive.
- Audio is processed in memory and never stored unless the user grants `model_improvement` consent.

---

## 13. Security, Privacy & Compliance

- **DPDP Act 2023 + Rules 2025:**
  - Itemized, purpose-specific notice in 10 languages + English, with granular consent toggles (including the separate cloud-speech and cloud-LLM consents above). Withdrawal is as easy as granting.
  - Verifiable parental consent for users under 18.
  - Data principal rights: access, correction, erasure (hard delete + crypto-shredding of the per-user DEK), grievance and nomination.
  - Retention limits and a breach runbook (Data Protection Board + users, and **CERT-In within 6 h**).
- **Local-first is the main privacy control.** PHI never leaves the device or server in Tier L. Free cloud APIs receive only de-identified, consented data (Tier IN/C), and never Rx images or medical images.
- **Cross-border:** Groq, Gemini and OpenRouter are hosted outside India. This is allowed under DPDP s.16 unless the destination is notified as restricted, but we still treat them as public-data-only by default and disclose them in the notice. Bhashini and Sarvam are preferred because they are India-hosted.
- **Also follow:** the ICMR Ethical Guidelines for AI in Healthcare (2023) and the Telemedicine Practice Guidelines 2020 (the AI never prescribes). CDSCO SaMD risk is managed through the information-only positioning.
- **Encryption:**
  - In transit: TLS everywhere (local HTTPS via mkcert; Cloudflare Tunnel for public access).
  - At rest: AES-256-GCM envelope encryption per user; the master key comes from Windows DPAPI locally (OpenBao/KMS in prod). Encrypted MinIO objects; EXIF stripping on uploads.
- **App security:**
  - Short-lived JWTs, OTP rate limits, RLS, upload MIME checks, CSP.
  - Prompt-injection defence: document text is treated as data, and the tool allow-list has no side effects.
  - PII-scrubbed logs (Presidio).
- **Medical disclaimers:**
  - An onboarding acknowledgement, recorded as a consent record.
  - A footer chip on every answer.
  - A spoken disclaimer at the start of voice sessions.
  - A deterministic red-flag engine (chest pain, stroke FAST signs, breathlessness, bleeding, poisoning, infant fever, pregnancy bleeding, suicidal ideation) that overrides output with a 108/112 call button. Mental health turns show Tele-MANAS 14416.
  - Hard bans: no prescribing, no dose changes, no definitive imaging reads.

---

## 14. Deployment

**Stage A: this laptop (development and the MVP)**
- **Docker Desktop:** WSL2 backend with the disk image on D:, running Postgres+pgvector, Redis, MinIO, Langfuse (optional), Label Studio and the `api`/`worker`/`speech` containers. The speech and OCR containers run CPU-only.
- **Ollama for Windows:** runs natively for the best CUDA performance; models live in `D:\ollama\models`.
- **Next.js:** `pnpm dev` (or containerized).
- **Remote access:** Cloudflare Tunnel (free) gives HTTPS so you can test on a phone.

**Stage B: free public beta**
- **Hosting:** Oracle Cloud Always Free in Mumbai/Hyderabad (ARM, 24 GB) for api + Postgres + CPU speech/OCR/translation, and Vercel Hobby for the frontend.
- **LLM:** either (a) Cloudflare Tunnel to this laptop's Ollama, or (b) a free API for de-identified traffic. The data stays in India for Tier L and IN.

**Stage C: scale (paid, later)**
- The same containers on K8s in an Indian region, with vLLM serving MedGemma-27B / Sarvam-M on rented GPUs (E2E Networks, Yotta, IndiaAI compute portal).
- No code changes are needed, because LiteLLM routing and the S3-compatible storage are configuration only.

---

## 15. Repository Layout (scaffold after approval, at `D:\Project\Scaler Health\sushrutai`)

```
apps/web/                    Next.js PWA (messages/{hi,en,bn,ta,te,mr,gu,kn,ml,or}.json)
services/api/                FastAPI: routers/, db/, schemas/, security/, consent/, orchestrator/, safety/
services/worker/             arq tasks: ocr pipeline, kb ingest, embeddings
services/speech/             STT/TTS/VAD/translate servers + Bhashini/Sarvam/Groq adapters
services/medicine/           (module inside api initially) alternatives, guidance, interactions
pipelines/                   drug_ingest (Jan Aushadhi, NPPA, NLEM, CDSCO, openFDA, DDInter), kb_ingest
ml/finetune/                 Kaggle/Colab notebooks: TrOCR, Qwen2.5-VL-3B Rx-LoRA (Unsloth)
ollama/Modelfiles/           medgemma-4b, qwen2.5vl-3b, llama-guard3-1b
infra/docker-compose.yml     + litellm.config.yaml, .env.example
eval/                        MedMCQA, Rx extraction, OCR CER, STT WER (Vistaar), red-team, triage vignettes
docs/                        ADRs, DPIA, licence register
```

---

## 16. Phased Roadmap

**Phase 0: Foundations (weeks 0–3)**
- Monorepo, docker-compose, Ollama + models pulled to D:, LiteLLM routing, DB schema + RLS, encryption utils.
- Drug-master ingestion (Jan Aushadhi, NPPA, NLEM, banned FDCs, openFDA, DDInter).
- KB ingestion (ICMR STW, MedlinePlus).
- Eval harness v1 and the local model bake-off.
- Register for the Bhashini, Sarvam, Groq and Firebase free tiers.

**Phase 1: MVP on laptop (weeks 4–10).** Languages: Hindi + English/Hinglish.
- Profile + email/phone OTP + guest mode, consent and disclaimers, red-flag engine.
- Text chat with RAG on MedGemma-4B.
- Rx upload (printed + English handwriting) with confirmation UI.
- Generic alternatives and dosage guidance.
- Push-to-talk voice (local Whisper + Indic Parler-TTS, with Bhashini fallback).
- **Exit gate:** ≥95% safe on a 300-prompt red-team set; printed-Rx drug-name top-3 ≥90%; works end to end in Offline mode.

**Phase 2: Full voice + triage + vision (weeks 11–20)**
- All 10 languages with IndicConformer + IndicTrans2; streaming voice with barge-in.
- Adaptive triage (F6).
- Medical image analysis (MedGemma-4B vision); lab-report parsing.
- Indic handwriting OCR; Rx-LoRA fine-tuned on Kaggle.
- Timeline, reminders, FHIR export.
- **Exit gate:** per-language STT WER targets on Vistaar; triage top-3 inclusion ≥75% on clinician vignettes.

**Phase 3: Beta & scale (months 6–12)**
- Oracle Free beta, consented Rx data collection.
- ABDM sandbox (ABHA linking, PHR).
- Clinical validation with partner doctors (IEC).
- CDSCO opinion; move to GPU hosting with 27B models when funded.

---

## 17. Key Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **4B local model is weaker at clinical reasoning** | RAG with citations, SQL tools for all numbers, constrained JSON, grounding check, the triage KG doing the heavy lifting on question selection, an optional gpt-oss-20b local deep mode, and consented de-identified escalation to Groq 70B/120B. The eval gate decides. |
| **6 GB VRAM contention / slowness** | One GPU model at a time (`OLLAMA_MAX_LOADED_MODELS=1`), everything else on CPU, async jobs for OCR, streaming UI, cloud speech fallbacks |
| **Free-tier quota limits / ToS changes** | Every adapter has a local fallback and a quota tracker (`/system/providers`); no feature depends on a single free API |
| **PHI leaking to free cloud APIs** | Privacy tiers enforced in LiteLLM middleware, Presidio scrubbing, explicit consents, images never sent, Offline mode |
| **Hallucination / unsafe advice** | Red-flag rules ahead of the LLM, Llama-Guard-3-1B, a clinician red-team set in CI, "possible conditions" language, mandatory Rx confirmation |
| **Wrong drug from handwriting OCR** | Top-k candidates, strength/form plausibility, LASA warnings, user confirmation, "verify with your pharmacist" |
| **No free Indian brand→composition DB** | Build from government sources + moderated user/pharmacist contributions; scraped data only for validation |
| **C: drive only 20 GB free** | Everything (Docker WSL disk, Ollama models, HF cache via `HF_HOME=D:\hf`, datasets) goes on D: |
| **Regulatory (CDSCO SaMD, DPDP)** | Information-only positioning, DPIA, consent records, India-hosted defaults, counsel review before a public beta |
| **Data scarcity (Indic medical speech, Indian Rx)** | Consented collection, active learning from corrections, IndicTrans2 + clinician-verified eval sets |

---

## 18. Verification

1. **Environment check:** `nvidia-smi` and `ollama run medgemma-4b "test"` both work; `docker compose up` brings all services up healthy; `GET /api/v1/system/providers` lists the local models and the free APIs with valid keys.
2. **End to end on the laptop** at `https://localhost:3000`:
   - Guest mode → create a profile (email OTP).
   - Upload a real Rx from the consented set → confirm the fields → see generics with Jan Aushadhi prices → open dosage guidance.
   - Hindi voice question ("मुझे तीन दिन से बुखार और सिरदर्द है") → triage questions → possible conditions with citations and a red-flag check.
   - Toggle Offline mode with Wi-Fi off and repeat.
3. **Automated:** `pytest` (API, RLS isolation, crypto-shred on `DELETE /me`, privacy-tier enforcement, which must block PHI to Tier C), Playwright e2e across locales, and schemathesis against OpenAPI.
4. **Eval:** `python -m eval.run --suite all` reports MedMCQA accuracy, Rx field F1 / drug top-k, OCR CER, STT WER per language (Vistaar subset), red-team pass rate, triage top-3 and p50 latency. A regression blocks the merge.
