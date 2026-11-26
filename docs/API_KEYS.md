# API keys

SushrutAI runs fully on this computer with **no keys at all**. Every key below is optional: it switches on one
extra choice in Settings. Put keys in the `.env` file next to `main.py` (copy `.env.example`), then restart
`python main.py`.

Choosing a cloud option in Settings records a consent for it, and **Offline mode** (Settings → Privacy) blocks
every cloud call at any time.

| Key (`.env`) | What it switches on | Where to get it | Cost | What is sent, and where |
|---|---|---|---|---|
| `GEMINI_API_KEY` | Answer models **Gemini** (`GEMINI_MODEL`, default `gemini-2.5-flash`) and **Gemini Flash-Lite** (`GEMINI_FAST_MODEL`, default `gemini-3.5-flash-lite`). The same key runs Live voice: **3.8 Live**, **3.5 Transcribe Live**, **3.5 Live Translate**, and legacy **3 Flash Live**. | Google AI Studio → "Get API key" (aistudio.google.com) | Free tier with daily limits; paid above | The question, the health context needed to answer it and attached photos go to Google (outside India). On the free tier Google may use them to improve its products. Consent: `cloud_llm_full`. |
| `SARVAM_API_KEY` | Voice engine **Sarvam AI**: listening (Saarika) and read-aloud (Bulbul) in Indian languages. Also the existing fallback when no local speech model is available. | dashboard.sarvam.ai | Free starter credits; paid above | Voice recordings and text to read aloud go to Sarvam (India). Consent: `cloud_voice` (or `cloud_speech` for the fallback). |
| `ELEVENLABS_API_KEY` | Voice engine **ElevenLabs**: listening (Scribe, `ELEVENLABS_STT_MODEL`) and read-aloud (`ELEVENLABS_TTS_MODEL`, voice `ELEVENLABS_VOICE_ID`). | elevenlabs.io → Profile → API keys | Free monthly character allowance; paid above | Voice recordings and text to read aloud go to ElevenLabs (outside India). Consent: `cloud_voice`. |
| `GOOGLE_PLACES_API_KEY` | Emergency sheet: fills in hospital phone numbers that OpenStreetMap does not have. | Google Cloud console → enable "Places API (New)" → Credentials | Monthly free credit; billing must be enabled | The hospital name and the rounded location (about 100 m). |
| `GROQ_API_KEY` | Answer model **Online** (de-identified text only, never photos) and a speech-to-text fallback. | console.groq.com | Free tier with rate limits | De-identified text (names, phone and ID numbers removed). Consent: `cloud_llm_deidentified`. |

Nearby hospitals use **OpenStreetMap** (Overpass API) with no key: only a location rounded to about 100 m is sent,
and only when the person taps "Find near me" or says "call the nearest hospital".

## Which key do I need?

- Nothing: everything works locally (answers, voice in 12 Indian languages, documents, reminders).
- Better answers to hard questions: `GEMINI_API_KEY`.
- More natural Indian-language voices: `SARVAM_API_KEY` (or `ELEVENLABS_API_KEY`).
- Hospital phone numbers in the emergency sheet: `GOOGLE_PLACES_API_KEY`.
