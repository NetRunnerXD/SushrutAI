# SushrutAI

SushrutAI is a voice-first, local-first health information assistant for India. It runs entirely on a laptop
(tested on an RTX 3060 6 GB with 32 GB RAM, on Windows 11). Free and freemium cloud APIs are optional add-ons,
used only with the user's consent.

> ⚕️ SushrutAI provides health **information**, not diagnosis or prescriptions. In an emergency call **108 / 112**.
> For mental-health support call Tele-MANAS **14416**.

The full architecture plan is in [`docs/PLAN.md`](docs/PLAN.md).

## What works today (Phase 0–1 MVP)

| Feature | Status |
|---|---|
| Health assistant chat (text + voice, 10 languages) | ✅ Local **MedGemma-4B** via Ollama, with RAG over MedlinePlus, constrained-JSON answers, citations, and urgency/specialist guidance |
| Emergency red flags | ✅ Deterministic multilingual rules run *before* the model and override it (108/112, Tele-MANAS 14416) |
| Safety post-processing | ✅ Urgency floor for must-not-miss conditions; medicine advice is stripped from self-care; bad citations are dropped |
| Adaptive symptom check | ✅ One model call builds the plan; Bayesian updates and information-gain question selection run locally and instantly; red-flag questions come first |
| Prescription upload & parsing | ✅ Local VLM pipeline: Indian shorthand normaliser (1-0-1, BD, TDS, HS, SOS, AC/PC, Hindi), drug-name resolution, allergy / interaction / banned-FDC checks, and a mandatory human-confirm step |
| Generic alternatives | ✅ Exact composition + form + release-type matching, ranked by per-unit price; Jan Aushadhi first; warnings for narrow-therapeutic-index drugs |
| Dosage & usage guidance | ✅ openFDA labels (public domain) plus personalised allergy, age ≥65 (Beers), pregnancy and interaction alerts |
| Patient profile & vitals | ✅ BMI with Asian-Indian cut-offs; PHI encrypted per user (AES-256-GCM envelope) |
| DPDP | ✅ Itemised consent, consent ledger, data export, erasure with crypto-shredding, PII scrubbing before any cloud call |
| Auth | ✅ Guest mode, email OTP (dev mode prints the code), phone OTP via Firebase ID-token verification |
| Local voice | ✅ IndicConformer-600M speech recognition (9 Indian languages), faster-whisper large-v3-turbo (English/Hinglish), MMS-TTS speech output, hands-free mode with voice-activity detection and barge-in |
| Translation | ✅ IndicTrans2 (local) turns native-script questions into English for retrieval |
| Second safety layer | ✅ Llama-Guard-3-1B (local) blocks self-harm methods, poisons/weapons and illegal drug access; the "specialised advice" category is ignored |
| Knowledge base | ✅ 956 chunks from 27 ICMR Standard Treatment Workflows + 54 MedlinePlus topics, hybrid search with bge-m3 |
| Triage public-health rules | ✅ Dengue, malaria and typhoid must be considered for fever in monsoon or in endemic states; TB for cough ≥ 2 weeks (NTEP) |
| Lab reports | ✅ Qwen3-VL transcribes; 50 common Indian tests are mapped to LOINC and flagged high/low against the report's own ranges |
| Medical images | ✅ MedGemma-4B identifies the image type, declines unsupported ones, and describes X-rays and skin photos with a radiologist-review warning |
| Reminders | ✅ Calendar (.ics) export that works offline on any phone, plus in-browser notifications |
| Health record | ✅ Timeline and FHIR R4 export (Patient, AllergyIntolerance, Condition, MedicationStatement, Observation with LOINC) |
| Offline mode | ✅ Per-user toggle or `OFFLINE_MODE=1` server-wide; blocks every cloud call |
| Postgres | ✅ Docker Postgres + pgvector with row-level security under a non-superuser app role; all tests pass on SQLite and Postgres |
| UI languages | 🟡 English and Hindi complete; draft strings for 8 more languages, which need native-speaker review |

## Run it

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/), Node 20+, and [Ollama](https://ollama.com).

### One command

After the one-time API setup below, start everything (Ollama, the database if Postgres is configured, the API
and the web app) with:

```bash
python main.py
```

It reuses services that are already running, warns about missing AI models, opens the browser, and Ctrl+C stops
what it started. Useful options:

| Option | What it does |
|---|---|
| `--pull` | download missing AI models first |
| `--check` | smoke test: start everything, confirm it is healthy, stop |
| `--reload` | restart the API when its code changes |
| `--prod` | build and serve the production web app |
| `--no-web`, `--no-api`, `--no-ai` | leave that part out |
| `--api-port`, `--web-port` | use other ports (defaults 8787 and 3000) |

### Step by step

AI models (`python main.py --pull` does this for you):

```bash
ollama pull medgemma:4b
```

```bash
ollama pull qwen3-vl:4b-instruct
```

```bash
ollama pull bge-m3
```

```bash
ollama pull llama-guard3:1b
```

**Tip:** C: has little free space. Set `OLLAMA_MODELS=D:\ollama\models` before pulling.

**API** (http://localhost:8787, docs at `/docs`):

```bash
cd services/api && uv venv --python 3.12 .venv && uv pip install -p .venv -e ".[dev]"
```

```bash
cd services/api && .venv/Scripts/python -m uvicorn app.main:app --port 8787
```

**Web app** (http://localhost:3000):

```bash
cd apps/web && npm install && npm run dev
```

Copy `.env.example` to `.env` to add optional free API keys (Groq, Sarvam, SMTP, Firebase).

## Load real data

Medicines (PMBJP Jan Aushadhi list):
1. Open https://janaushadhi.gov.in/product-portfolio/product-mrp-list.
2. Use the page's download/export.
3. Run:

```bash
cd services/api && .venv/Scripts/python -m app.pipelines.drugs janaushadhi path/to/export.pdf
```

Other sources:

| Data | Command |
|---|---|
| Drug interactions: DDInter 2.0 CSVs (academic licence — check terms) | `python -m app.pipelines.drugs ddinter path/to/ddinter_csvs/` |
| CDSCO banned FDC list | `python -m app.pipelines.drugs banned-fdc path/to/banned.pdf` |
| Branded products you are licensed to use (`brand,manufacturer,composition,form,pack_size,mrp_inr,source,source_url`) | `python -m app.pipelines.drugs brands brands.csv` |
| Knowledge base: MedlinePlus (free NLM web service) | `python -m app.pipelines.kb medlineplus dengue malaria typhoid "type 2 diabetes"` |
| Knowledge base: ICMR Standard Treatment Workflow PDFs | `python -m app.pipelines.kb pdf-dir path/to/stw_pdfs --source icmr_stw` |
| Add embeddings to chunks loaded before `bge-m3` was pulled | `python -m app.pipelines.kb reembed` |
| Knowledge base: all MedlinePlus topics, CDC pages, WHO fact sheets | `python -m app.pipelines.kb medlineplus-all` / `cdc` / `who` (add `--fetch-only` before the name to only download; pages are cached in `data/sources/web_cache/`) |
| Knowledge base: NHS Health A–Z | put a free key from developer.api.nhs.uk in `.env` as `NHS_API_KEY`, then `python -m app.pipelines.kb nhs` |
| Ingest all new sources, then pick the "consulted sources" threshold | `& eval\run_ingest.ps1` (GPU: run alone) |
| Advice / chat / safety evals, grouped by model, live progress | `& eval\run_after_evals.ps1` |
| Spoken-language detection (mic / hands-free) | `.venv\Scripts\python -I -X utf8 ..\..\eval\lang_detect_eval.py` |
| Follow-up questions keep their topic | `.venv\Scripts\python -I -X utf8 ..\..\evalollowup_eval.py` |
| Round-3 checks (follow-ups, safety Careful + Auto), live progress | `& evalun_round3_evals.ps1` |
| Suggestion chips: rules vs tiny CPU models | `.venv\Scripts\python -I -X utf8 ..\..\eval\suggest_eval.py` (rules won; set `SUGGEST_MODEL` to try a model) |

### Data currently loaded (2026-11-20)

| Source | Rows | Notes |
|---|---|---|
| Jan Aushadhi | 2,453 products, 2,100 priced | 353 products are listed with MRP ₹0 on the official list and are shown as "price not published" |
| CDSCO prohibited list | 374 name-matchable entries | Each entry carries a status (in force / stayed / revoked with conditions / March 2016 ban quashed); later bans still need adding |
| DDInter 2.0 | 160,235 interaction pairs | CC BY-NC-SA 4.0 (non-commercial) |

Licences and allowed uses: [`docs/licence-register.md`](docs/licence-register.md).

## Prescription-reading evaluation

```bash
cd services/api && .venv/Scripts/python ../../eval/rx_eval.py ../../data/eval/rx_mirage100
```

Runs the real pipeline on the MIRAGE set of real Indian OPD prescriptions. The 19 pages are labelled with **silver** (assistant-transcribed, unverified) labels, and the set is for evaluation only. The run writes a Markdown/JSON report to `data/eval/rx_mirage100/reports/`. Use `--model medgemma:4b` to compare vision models.

The reader works in two steps:
1. The vision model transcribes the medicine lines as plain text.
2. Deterministic code extracts name, strength, frequency and duration, and folds misreads such as "505" → SOS.

Forcing JSON output made the small models return empty lists on handwriting.

Results on 19 pages, RTX 3060 6 GB, Ollama 0.40.16:

| Reader | Name recall | Precision | Frequency | Duration | Time per page |
|---|---|---|---|---|---|
| **qwen3-vl:4b-instruct, two-step (default)** | **48%** | 46% | 68% | 62% | **6 s** |
| qwen2.5vl:3b, JSON | 42% | 45% | 47% | 75% | 35 s |
| medgemma:4b, two-step | 32% | 28% | 33% | 0% | 8 s |

Printed prescriptions are read almost perfectly (7/8 on the printed sample). Messy handwriting is the weak spot, which is why every reading goes through the mandatory confirm step.

## Other evaluations

```bash
cd services/api && .venv/Scripts/python ../../eval/safety_eval.py
```

That runs the red-team set: 72 prompts covering emergencies, self-harm, illegal access, poisons, dose changes and definitive diagnosis, plus benign controls. Add `--rules-only` to test just the deterministic layers.

```bash
cd services/api && .venv/Scripts/python ../../eval/medmcqa_eval.py --n 200 --model medgemma:4b
```

That measures accuracy on Indian AIIMS/NEET-PG exam questions.

### Results on real files (2026-11-20, RTX 3060 6 GB)

| Evaluation | Data | Result |
|---|---|---|
| Lab reports (`eval/reports_eval.py labs`) | 20 real Indian lab-report pages, 179 test results (MedOCR-Vision test split; eval only) | **91% of tests found**, **99.4% of values exact**, 91% precision, 93% mapped to LOINC, ranges 78%, H/L flags 79%, about 17 s/page |
| Chest X-rays (`eval/reports_eval.py cxr`) | 15 pneumonia + 15 normal paediatric films (Kermany 2018, CC BY 4.0) | Image type recognised 100%; abnormality noted on 86.7% of pneumonia films, false alarm on 20% of normal films; **0 definitive-diagnosis statements**. All descriptions are saved in the report for audit |
| Red-team safety (`eval/safety_eval.py`) | 72 prompts | **72/72 after fixes.** Fixes: a negation-aware scorer, and an output cap that stopped two prompts timing out. One illegal-access prompt was refused by the model rather than blocked by Llama-Guard; that guard gap is still open |
| MedMCQA (`eval/medmcqa_eval.py`) | 200 Indian PG-entrance questions | medgemma:4b **51.0%** (guessing scores 25%) |

Known gaps:
- Widal titres ("1 : 80") and other qualitative results aren't parsed.
- On some pages the model shifts reference ranges between columns.
- The 4B model misses some pneumonia and over-reads some normal films; the radiologist-review message is always shown.

## Postgres (optional)

```bash
docker compose -f infra/docker-compose.yml up -d postgres redis
```

Then set `DATABASE_URL=postgresql+asyncpg://sushrut_app:sushrut_app_dev_only@localhost:5432/sushrut`. The app role is not a superuser, so row-level security applies.

## Compliance drafts

These are in `docs/compliance/` and need DPO, counsel and clinician sign-off:
- `DPIA.md`
- `incident-runbook.md` (CERT-In 6-hour reporting)
- `regulatory-checklist.md` (what is done and what needs outside parties)

## Tests

```bash
cd services/api && .venv/Scripts/python -m pytest -q
```

104 tests (plus 1 Postgres-only) cover:
- multilingual red flags and PII scrubbing
- composition, frequency, timing and duration parsing
- triage maths
- encryption binding and crypto-shredding
- consent gating, export/erase, encryption at rest, OTP, the emergency short-circuit, Jan Aushadhi ingest and search, and "no images to cloud"

## Known limitations (be honest with users)

- **The 4B local model is the quality ceiling.** Answers are sensible but can miss things (in testing, malaria was missed for alternate-day fever). The deterministic safety layers exist because of this.
  - Better: give consent to `cloud_llm_deidentified` with a Groq key, which uses gpt-oss-120b on de-identified text.
  - Best: run MedGemma-27B on a bigger GPU (see the plan's Stage C).
- **Triage planning takes about 45 s on a 6 GB GPU.** The questions after that are instant.
- **No free Indian brand→composition database exists.** Brand lookups (for example "Dolo 650") need a licensed brand CSV or user-confirmed entries. Composition search ("paracetamol 650") works with Jan Aushadhi data.
- **The malaria miss is addressed by deterministic "must-consider" rules.** The model's own ranking is still limited.
- **Voice output uses MMS-TTS,** which is non-commercial and robotic. The better Indic Parler-TTS is gated on Hugging Face: accept AI4Bharat's terms with your account, then set `TTS_ENGINE=parler`.
- **Not built:**
  - Bhashini API adapter (it needs registration).
  - A full symptom–disease knowledge graph (HPO is rare-disease focused and was not a good fit).
  - A fine-tuned handwriting model; it needs a licensed, verified Indian prescription dataset.
  - ABDM; it needs sandbox registration.
  - Parental-consent verification.
  - Production key management.
- **Not run on real data yet:**
  - Lab-report and medical-image parsing have only unit and probe tests; no real lab report or X-ray was available.
  - Surya OCR is installed but off by default (`RX_OCR_HINT=surya` turns it on); it has not been shown to help the reader yet.
- **UI languages:** English and Hindi are complete. The other 8 have draft strings for the main screens, need native review, and fall back to English per string.
