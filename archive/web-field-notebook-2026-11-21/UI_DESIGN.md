# SushrutAI — UI/UX Redesign & Expansion Plan

## Field Notebook (current visual design, Nov 2026)

The calm-teal look described in §2 below was replaced by the **Field Notebook** design. Information
architecture, journeys, components and accessibility rules in this document still apply; colours, type and
motion are now as follows. Tokens live in `apps/web/app/globals.css`.

**Idea.** A health notebook kept by the family: paper, ink, a turmeric highlighter. One memorable thing — the
breathing ink-blot voice orb — and everything around it quiet.

### Colour (light "paper" / dark "night notebook")

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F4EFE4` paper | `#121926` navy paper | page, with a faint dot grid (`--grid`) |
| `--surface` | `#FBF8F1` | `#19212F` | pinned notes (cards), sheets |
| `--surface-2` | `#ECE5D5` | `#222C3D` | shaded panels, sidebar |
| `--text` / `--muted` | `#1A2436` / `#565A62` | `#EEE7D7` / `#B3AC9C` | ink / pencil |
| `--brand` (+`-ink`) | `#1D2F4F` ink-blue (+ `#FBF8F1`) | `#E8B13A` turmeric (+ `#1A1300`) | primary actions, the orb |
| `--accent` | `#E0A526` turmeric | `#E0A526` | marks only (underlines, tape, rules); never text on paper |
| `--accent-soft` | `#F5E2B0` | `#3A3120` | highlighter behind current nav item, selected chips; text on it is `--text` |
| `--on-tone` | `#FFFFFF` | `#10161F` | text on solid `ok` / `danger` |
| `--emergency` | `#B01E16` | `#C0261C` | SOS: background with white text, or text on white only |
| `--series-1/2` | `#2a78d6` / `#d95926` | `#3987e5` / `#d95926` | charts (series-2 deepened from `#eb6834` for 3:1 on paper; CVD separation re-checked) |

Contrast was computed for 49 text/UI pairs per theme: all light pairs pass (lowest text pair 5.47:1). In dark,
every pair in use passes; `accent-ink` on `accent-soft` and `emergency` as text on dark surfaces fail, so neither
is used (SOS-sheet text uses `--danger`). High-contrast mode resets to black on white with no paper texture.

### Type
- **Martel** (700/800 headings, 400 for quoted tips) — a Latin + Devanagari serif, so Hindi and Marathi headings
  match English; other scripts fall back to their Noto Sans family.
- **Noto Sans** + the nine Noto script families for body text (unchanged).
- **Courier Prime** for readings, doses, times and dates (typewritten-slip numerals, tabular).

### Shape and surfaces
Cards are pinned notes: 6px corners, a hairline border and a soft paper shadow. Buttons 10px, chips round.
Banners are margin notes: a 5px coloured rule on the left. Sheets slide up with a turmeric (or emergency-red)
top edge. Desktop sidebar is the notebook's cover board with a stitched edge.

### Motion (all CSS; transform/opacity/SVG stroke only)
| Moment | What moves |
|---|---|
| Page/card appear | `.note` "settle": a 0.46s drop with a slight tilt; groups stagger via `--i` (max 6 steps) |
| Page titles | `Scribble`: hand-drawn turmeric underline inks itself in (`.ink-draw`, 0.7s, once) |
| Current nav item | highlighter swipe (`.highlight`) |
| Voice orb idle | ink blot morphs (border-radius) and breathes; a fainter offset "bleed" spreads under it |
| Listening | blot swells with the live mic level (`--lvl`), ink droplets spray; waveform is a mirrored ink ribbon |
| Transcribing | dashed ink ring spins around the blot |
| Thinking | pen trace writes and rewrites a line |
| Mood chosen | stamp |

`prefers-reduced-motion` and the app's own Reduce motion setting (`data-motion="reduce"`) stop all of it; content
is visible from the first frame regardless. The floating mic hides while a full voice orb is on screen.

## Context

The current web app (`sushrutai/apps/web`, Next.js 16 + Tailwind 4) is a functional MVP. It has six flat tabs (Assistant, Symptom check, Prescriptions, Medicines, Reports, Profile), an onboarding consent modal, and Settings inside Profile. It works, but:
- There is no landing/home or dashboard.
- Information architecture is flat.
- The colour tokens are ad hoc, and contrast was never verified.
- Emergency access is buried in chat answers.
- Low-literacy support is limited to read-aloud.
- Profile is one long form.
- The 8 newer languages only have draft strings for the main screens.

This plan redesigns the information architecture, the visual system and every page. It adds high-value UX features (family profiles, an always-visible SOS button, symptom chips, a dose tracker, confidence and source indicators, offline basics). The goal is a production-grade, WCAG 2.2 AA, mobile-first experience that is calm and trustworthy for unwell or anxious Indian users, including first-time and low-literacy users.

Scope:
- **Frontend only**, plus a few small backend additions where a feature needs data. Each is listed under "Backend touch-points".
- The backend APIs already exist and are reused unchanged unless noted.

---

## 1. Information architecture

### 1.1 Navigation (5 destinations max, icon + label always)

Mobile uses a bottom tab bar; desktop uses a left sidebar.

| Tab | Route | Contains |
|---|---|---|
| **Home** | `/` | Greeting, "How are you feeling?" voice orb, quick symptom chips, today's doses, alerts, recent activity |
| **Ask** | `/ask` | AI assistant chat (voice and text). Symptom check opens from here as a guided mode (`/ask/check`) |
| **Medicines** | `/medicines` | My medicines and dose tracker, prescription upload/review (`/medicines/rx/*`), search, alternatives, guidance (`/medicines/[id]`) |
| **Records** | `/records` | Lab reports, images, timeline, vitals charts, FHIR/PDF export (`/records/*`) |
| **Me** | `/me` | Profile (sectioned), family members, settings, privacy and consent, help |

- **Persistent header:**
  - Profile switcher (whose health: self or a family member)
  - Language button (globe + current language name in its own script)
  - **SOS** button (red, always visible)
- **Global floating mic** on mobile, bottom-right above the tab bar, on Home, Ask, Medicines and Records. One tap starts voice from anywhere.
- **First visit:** the landing page `/welcome` is shown before onboarding. Returning users go straight to Home.

### 1.2 Key user journeys

1. **First-time user**
   1. `/welcome`: one sentence of value, three illustrated benefits, and "Choose your language" as the first action. The language grid shows each name in its own script, large tiles.
   2. Short 3-step onboarding:
      - (a) disclaimer, read aloud automatically if the user taps 🔊;
      - (b) consent with plain-language toggles (required consent pre-checked and locked; each optional one has a "What does this mean?" expander);
      - (c) "Who is this for?": Myself / A family member / Just exploring (guest).
   3. Home, with a dismissible 3-card coach-mark tour: "Tap to speak", "Upload prescription", "SOS".
2. **Returning patient**
   1. Home shows the next dose due ("8:00 — Pan 40, before food" with Taken/Skip), any new alerts (abnormal lab, interaction), and "Continue last conversation".
   2. Login (phone OTP) is offered only when they try to save history.
3. **Prescription upload**
   1. Medicines → "Add prescription": camera first, gallery second, live framing guide.
   2. Processing screen with plain-language steps (Reading… Checking medicines… Checking safety…).
   3. Review screen: the image is pinch-zoomable side by side with the cards, one card per medicine. Low-confidence fields are amber with "Please check" and the transcribed line is shown.
   4. Safety alerts sit at the top. "Confirm all" requires each amber card to be ticked.
   5. Success: "Added 4 medicines. Reminders set for 8:00 and 21:00" plus "Add to phone calendar".
4. **Symptom check**
   1. Ask → "Check my symptoms", or tap a symptom chip on Home.
   2. One question per screen with large Yes / No / Not sure buttons (each with an icon). The question is read aloud. A progress bar ("Question 3 of about 8") and "Back" are always available.
   3. Result screen: an urgency card first (colour + icon + words), then possible conditions as a likelihood bar list, then what to do next (specialist, tests, self-care), then "Share with doctor" (PDF/WhatsApp).
5. **Medicine lookup**
   1. Medicines → search (typed or voice, by brand or composition) → result cards.
   2. Medicine detail tabs: Overview, How to take, Side effects, Cheaper options, Warnings.
   3. A Jan Aushadhi badge and savings % appear on alternatives, with a "Find nearest Kendra" map link.
6. **Emergency (any screen)**
   1. SOS opens a full-screen sheet with three big buttons: Call 108 (ambulance), Call 112, Call emergency contact.
   2. Below them: Tele-MANAS 14416, the patient's critical info card (blood group, allergies, conditions, current medicines; shareable), and "Nearest hospital" (opens maps).
   3. Red-flag detection in chat or check auto-opens the same sheet.

### 1.3 Voice-first interaction flow

```
Idle orb ("Tap and speak in any language")
  → tap (or hands-free VAD) → LISTENING: orb pulses, live waveform, interim transcript in the chosen script,
      "Tap to stop"; silence 0.9 s auto-stops
  → UNDERSTANDING: transcript shown in an editable bubble ("Did I hear this right? ✎") for 2 s, auto-sends
  → THINKING: calm progress text cycling every 3 s ("Checking trusted sources…", "Thinking carefully…")
  → ANSWER: urgency/summary card first; auto read-aloud of the short "summary" sentence only
      (long answers: "🔊 Listen to full answer"); barge-in (speaking) stops playback
  → FOLLOW-UP: 2–3 suggested reply chips ("Yes, 3 days", "No fever") + mic
Errors: no mic permission → illustrated help + typing fallback; no speech heard → "I didn't catch that — try again";
offline → local STT continues; cloud fallbacks hidden.
```

- **Captions are always on.** Every spoken output is shown as text: deaf/hard-of-hearing support and noisy environments.
- **Voice commands** (keyword match, all 10 languages): "help / madad", "emergency", "repeat / phir se", "stop / ruko", "go home".

---

## 2. Visual design system

### 2.1 Colour tokens

All pairs were computed against WCAG 2.2. Text needs 4.5:1, large text and UI components 3:1.

**Light mode**

| Token | Hex | Use | Contrast |
|---|---|---|---|
| `bg` | `#F6F8F7` | App background | — |
| `surface` | `#FFFFFF` | Cards, sheets | — |
| `surface-2` | `#EDF3F1` | Subtle sections, chips | — |
| `text` | `#132320` | Body text | 15.3 on bg |
| `text-muted` | `#4A5D57` | Secondary text | 6.6 on bg, 6.2 on surface-2 |
| `border` | `#D5DFDB` | Decorative dividers only | — |
| `border-input` | `#74877F` | Input and control outlines | 3.8 (≥ 3:1 UI) |
| **Primary (calm teal)** `primary` | `#0A6B66` | Buttons, links, active nav | 6.35 on white; white-on-primary 6.35 |
| `primary-strong` | `#07504C` | Hover/pressed, headings accent | 9.3 |
| `primary-soft` | `#DDF1EE` | Selected chips, info cards | primary text on it 5.4 |
| **Secondary (trust blue)** `info` | `#1F5AA6` | Info, links in text, **focus ring** | 6.8 |
| `info-soft` | `#E3EDFA` | Info banners | 5.8 |
| **Accent (marigold)** `accent` | `#F2B33D` | Sparingly: highlights, savings badge, onboarding art. Always with dark text `#3A2600` | 7.75 |
| `success` | `#17703F` / soft `#E1F3E8` | Normal results, "taken", confirmed | 6.1 / 5.3 |
| `warning` | `#8A4B00` / soft `#FDF0D8` | Needs review, see doctor this week, amber fields | 6.8 / 6.0 |
| `danger` | `#B3261E` / soft `#FCE8E6` | Errors, abnormal-high, interactions | 6.5 / 5.6 |
| `emergency` | `#9F1D17` (white text) | SOS button, emergency sheet | 7.9 |

**Dark mode** (via `prefers-color-scheme`, plus a manual override in Settings)

| Token | Hex |
|---|---|
| `bg` | `#0E1614` |
| `surface` | `#16211E` |
| `surface-2` | `#1E2B27` |
| `text` | `#E6EFEC` (15.7) |
| `text-muted` | `#A7B8B2` (8.9) |
| `primary` | `#4CC3B5` (8.6; on-primary text `#06201D`) |
| `info` | `#8AB4F8` (8.7) |
| `success` | `#74D39C` |
| `warning` | `#F5B85A` |
| `danger` | `#FF8A80` |
| `border-input` | `#6E837C` (4.1) |
| `emergency` | keeps `#9F1D17` with white text (recognisability) |

**Rules**
- Never rely on colour alone. Every status carries an icon and a word:
  - ⚠️ "See a doctor today"
  - ✓ "Normal"
  - ↑ "High"
  - ↓ "Low"
- Severity scale used everywhere (urgency, lab flags, interactions):
  - Emergency: `emergency`, solid fill, siren icon
  - See doctor today: `danger-soft` with `danger` text
  - This week: `warning-soft`
  - Self-care: `success-soft`
  - Info: `info-soft`

### 2.2 Typography

- **Families:** Noto Sans plus the per-script Noto families already loaded:
  - Devanagari, Bengali, Tamil, Telugu, Gujarati, Kannada, Malayalam, Oriya;
  - Noto Sans Mono for numbers in vitals and doses.
  - The fallback stack ends with `system-ui`.
- **Loading:** self-host via `next/font/google` (no layout shift, works offline as a PWA) and subset per script. Load only the active language's script plus Latin. Today all scripts load up front from Google Fonts.
- **Scale** (rem, base 17px for readability; Indic scripts need extra line height):

| Role | Size / line-height | Weight |
|---|---|---|
| Display (home greeting) | 1.75 / 1.3 | 700 |
| H1 page title | 1.5 / 1.35 | 700 |
| H2 section | 1.25 / 1.4 | 600 |
| Body | 1.0625 (17px) / 1.6; Indic 1.7 | 400 |
| Body-strong (doses, names) | 1.0625 / 1.6 | 600 |
| Small / caption | 0.9375 (15px) / 1.5 | 400 (never smaller than 14px) |
| Button | 1.0625 / 1 | 600 |

- **Large text mode** (Settings) multiplies everything by 1.25 via the root font-size. Layouts must reflow at 200% zoom without horizontal scroll.
- **Writing style:**
  - Sentence case.
  - Grade-5 reading level in English.
  - Short sentences and active voice.
  - Numbers as digits plus words for doses ("1 tablet, morning").

### 2.3 Spacing, radius, elevation

- **Spacing** on a 4px base. Tokens: `1=4, 2=8, 3=12, 4=16, 5=20, 6=24, 8=32, 10=40, 12=48`. Page gutter is 16px on mobile and 24–32px on desktop. Card padding is 16 on mobile and 20 on desktop. Vertical rhythm between sections is 24.
- **Radius:** `sm 8` (chips, inputs), `md 12` (buttons), `lg 16` (cards), `xl 24` (sheets, orb container), `full` (pills, avatar, mic).
- **Elevation** (soft and low; no harsh shadows in a medical context):
  - `e0` flat with a border;
  - `e1` `0 1px 2px rgb(19 35 32 / 6%)` for cards;
  - `e2` `0 4px 12px rgb(19 35 32 / 8%)` for sticky composer and FAB;
  - `e3` `0 12px 32px rgb(19 35 32 / 14%)` for sheets and modals.
  - Dark mode uses border plus lighter surface instead of shadows.
- **Motion:** 150–250 ms ease-out. Respect `prefers-reduced-motion`: disable the orb pulse and replace it with a static ring plus "Listening" text.

### 2.4 Iconography

- **Library:** Lucide icons, 1.75px stroke, rounded joins, 24px (20 inline). They replace the emoji used today, which render inconsistently across Android OEMs and aren't announced well by screen readers.
- **Medical meaning set:** stethoscope (consult), pill, file-text (prescription), flask (lab), heart-pulse (vitals), shield-check (verified source), siren (emergency), mic, volume-2, calendar-clock (reminders), users (family), languages (globe).
- **Icons never stand alone:** they always have a visible label in the nav and buttons, and an `aria-label` when inline.
- **Illustrations:** flat, warm, diverse Indian people (saree, kurta, elderly, children); 2-tone primary-soft + accent. Use them for empty states and onboarding only.

---

## 3. Page-by-page layout

All pages:
- Max content width 720px for reading pages; 1200px for the desktop dashboard grid.
- Skip-link "Skip to main content".
- A single H1.
- Page-level disclaimer chip where medical content appears.

1. **Landing `/welcome`** (first visit, not logged in)
   - **Hero:** "Health help in your language — just speak." A big mic illustration and a primary CTA, "Start" (the language grid comes first).
   - **Three benefit cards:**
     - Ask by voice
     - Understand your prescription
     - Find cheaper medicines
   - **Trust strip:**
     - "Runs privately on this device"
     - "Uses ICMR & government sources"
     - "Not a doctor — always consult one"
   - **Footer:** SOS numbers (108 / 112 / 14416) and the privacy notice link.
2. **Home / Dashboard `/`**
   - **Mobile, single column, in this order:**
     1. Greeting with profile name and date.
     2. **Voice orb card**: "How are you feeling today?" with a large mic (min 96px) and "or type".
     3. **Quick symptom chips** (horizontally scrollable, icon + label): Fever, Cough, Headache, Stomach pain, Diabetes check, BP, Child unwell, Women's health. A tap starts a symptom check pre-filled.
     4. **Today's medicines**: timeline of doses with Taken/Skip, a progress ring ("2 of 4 taken").
     5. **Alerts**: abnormal lab values, interactions, prescriptions needing review.
     6. **Recent**: last chat, last report.
     7. **Health snapshot**: latest BP / sugar / weight vital cards with trend arrows.
   - **Desktop:** two columns. Left (8/12): orb card, chips, alerts, recent. Right (4/12): today's medicines, vitals snapshot, emergency info card.
3. **Prescription upload & review `/medicines/rx/new` → `/medicines/rx/[id]`**
   - **Step indicator:** Photo → Reading → Review → Done.
   - **Capture:**
     - Camera viewfinder with a document frame overlay and tips ("Flat, bright light, whole page").
     - Gallery/PDF option.
     - Blur/glare warning before upload (client-side Laplacian check).
   - **Review:**
     - *Desktop:* split view, image left (zoom/pan, bounding boxes highlighted on hover) and medicine cards right.
     - *Mobile:* image collapsible at top ("Show prescription"), then cards.
   - **Each medicine card shows:**
     - name (editable);
     - matched product (✓ Jan Aushadhi badge if applicable);
     - dose pictogram ("1-0-1" as ☀️ 1 · 🌤 0 · 🌙 1 icons);
     - timing ("after food" plate icon) and duration;
     - confidence state: amber "Please check" with the transcribed line, and a must-tick "I checked this" for low-confidence fields.
   - **Safety alerts** are pinned above the cards (allergy, interaction, banned FDC with status note).
   - **Sticky bottom bar:** "Confirm & add to my medicines" (disabled until all amber cards are ticked) and "Save draft".
4. **AI Health Assistant `/ask`**
   - Header: title, a "Check symptoms" mode switch, and the current profile.
   - **Message list:** user bubbles right-aligned (primary-soft); assistant answers left-aligned as **structured answer cards**, not bubbles (anatomy in §4).
   - **Composer** (sticky): mic (large, primary), text field (auto-grow), attach (prescription/report/photo), send. Suggested reply chips sit above the composer.
   - **Empty state:** the voice orb plus 4 example questions in the chosen language.
   - Image answers show a "Radiologist review needed" banner by default.
5. **Patient profile `/me`**
   - **Sectioned cards** instead of one long form, each with progress ("4 of 6 filled"):
     1. Basics (age, sex, state)
     2. Body (height, weight, BMI with Asian cut-offs explained)
     3. Allergies
     4. Conditions
     5. Lifestyle (diet, tobacco, alcohol, activity)
     6. Women's health (shown conditionally)
     7. Emergency contact
   - Each section opens in a **bottom sheet editor** on mobile and inline on desktop. Pickers use searchable chips (common allergies: Penicillin, Sulfa, NSAIDs, Peanuts…) rather than free text where possible.
   - **"Emergency card" preview**, shareable as image or PDF and shown on the SOS sheet.
   - **Family members:** an avatar row with "+ Add" (child, parent, spouse). Each member has their own profile, medicines and records.
6. **Medicine detail / alternatives `/medicines/[id]`**
   - **Header:** name, composition chips ("Paracetamol 650 mg"), form/release badges, price per unit, source and date.
   - **Tabs:** Overview, How to take, Side effects, **Cheaper options**, Warnings.
   - **How to take:** dose pictogram, food relation, "Don't crush" icons, missed-dose guidance. Each section has a 🔊 button.
   - **Side effects:** two lists, "Common (usually mild)" and "See a doctor if" (danger styling).
   - **Cheaper options:**
     - Ranked cards: name, maker, per-unit price, **savings % badge (accent)**, Jan Aushadhi badge, "Same composition ✓ same form ✓ same release ✓" checklist.
     - NTI warning banner when applicable.
     - "Find Jan Aushadhi Kendra" button.
   - **Warnings:** personal alerts first (your allergy, your age, pregnancy, interactions with your medicines), then the CDSCO status if listed.
7. **Reports & history `/records`**
   - Segmented control: **Timeline | Lab results | Images | Vitals**.
   - **Lab results:**
     - Grouped by panel (CBC, LFT…).
     - Rows show test, value (mono), unit, a reference-range bar with a marker, and an H/L/N flag with icon + word.
     - "Show only abnormal" toggle.
     - Trend sparkline when there are multiple results.
   - **Vitals:** per-vital chart cards (BP, sugar, weight) with target bands. "Add reading" offers a large number keypad or voice ("My BP is 130 over 85").
   - **Export:** PDF summary for the doctor, FHIR file, and share sheet (WhatsApp).
8. **Settings `/me/settings`**
   - Groups:
     - **Language & voice:** UI language, voice language, speech speed, auto read-aloud, voice gender.
     - **Display:** text size (A / A+ / A++), dark mode (Auto / Light / Dark), reduced motion, high-contrast mode.
     - **Reminders:** times, calendar export, notification permission state.
     - **Privacy:** offline mode, consents with plain-language explanations, download data, delete data.
     - **Help:** how to use, report a problem, emergency numbers, about AI limits.
   - Every toggle row has a one-line explanation in plain language.

---

## 4. Component library (`apps/web/components/ui/*`)

| Component | Spec |
|---|---|
| `Button` | Variants: primary, secondary (outline `border-input`), ghost, danger, emergency. Sizes: md 48px, lg 56px (primary actions on mobile). Icon + label; loading spinner keeps width; focus ring 3px `info` with 2px offset |
| `IconButton` | 48×48 minimum, required `aria-label`, tooltip on desktop |
| `Card` | `surface`, radius lg, `e1`; optional header icon, title, action. `CardSection` dividers |
| `Chip` / `SymptomChip` | 40px tall (48 on mobile with padding hit area), icon + label, toggle state `primary-soft` + check icon |
| `ChatBubble` (user) / `AnswerCard` (assistant) | AnswerCard anatomy (top → bottom): urgency banner, summary sentence (largest), possible-conditions list, "What to do next", sources (expandable, shield-check icon), confidence pill, disclaimer chip, actions (🔊 Listen, 👍/👎, Share, Ask follow-up) |
| `UrgencyBanner` | The 5-level scale (§2.1). Emergency variant has call buttons inline |
| `ConfidencePill` | "High confidence" / "Medium — please check" / "Low — talk to a doctor", with an info sheet explaining the meaning |
| `SourceBadge` | Shield-check + source name (ICMR STW, MedlinePlus, openFDA, Jan Aushadhi, CDSCO) + date; tap shows the excerpt |
| `UploadZone` | Camera-first on mobile (`capture="environment"`), drag-and-drop on desktop, framing overlay, quality warnings, progress with named steps |
| `MedicineCard` | Name, composition chips, `DosePictogram`, timing icon, duration, Jan Aushadhi / savings badges, confidence state |
| `DosePictogram` | Morning/afternoon/night slots (sun/sun-dim/moon icons) with counts; ½ supported. `aria-label` reads e.g. "1 tablet morning, none afternoon, 1 at night" |
| `VitalCard` | Label, big mono value, unit, status word + icon, trend arrow, mini sparkline, target band text |
| `RangeBar` | Lab value marker on a low/normal/high band. Text equivalent for screen readers ("9.1, below normal range 13 to 17") |
| `WarningBanner` | info / warning / danger / emergency; icon + title + body + optional action; `role="alert"` only for danger/emergency |
| `DisclaimerChip` | Small persistent "Information only — not a diagnosis" + "Learn more" sheet |
| `Sheet` (bottom sheet) / `Dialog` | Focus-trapped, escape/back closes, swipe-down on mobile, `aria-modal` |
| `VoiceOrb` | States: idle, listening (waveform from analyser), thinking, speaking (ripple), error. 96px on Home, 64px in composer. Text label below shows the state |
| `LiveTranscript` | Editable interim text in the active script; "Did I hear this right?" |
| `LanguagePicker` | Grid of language tiles (native name large, English small); current language ticked |
| `ProfileSwitcher` | Avatar + name; sheet lists self + family + "Add member" |
| `SOSButton` / `EmergencySheet` | Persistent header button; sheet with 108/112/contact/Tele-MANAS and the critical info card |
| `DoseTimeline` | Today's doses with Taken/Skip/Snooze, progress ring; offline-safe local state with sync |
| `QuestionCard` (symptom check) | One question, 3 large answer buttons (✓ Yes / ✗ No / ? Not sure), 🔊 auto-read, back, progress |
| `EmptyState` | Illustration + one sentence + one primary action |
| `Skeleton` | Calm shimmer (disabled with reduced motion) for loading |

---

## 5. Accessibility & inclusive design (WCAG 2.2 AA)

- **Contrast:** use the verified tokens only (§2.1). Lint with `eslint-plugin-jsx-a11y`, and run axe in Playwright on every page in light and dark modes.
- **Targets:** minimum 48×48px (beyond WCAG 2.2's 24px minimum, because users may be unwell, elderly or in a moving vehicle). Keep 8px spacing between targets.
- **Focus:** a visible 3px `info` ring on every interactive element; logical order; focus is never obscured by the sticky composer, bottom nav or FAB (WCAG 2.4.11). Add `scroll-padding-bottom`.
- **Screen readers:**
  - Landmarks (`header`, `nav`, `main`).
  - One H1 per page.
  - `aria-live="polite"` for the transcript, thinking status and new answers; `assertive` only for emergency banners.
  - DosePictogram, RangeBar and charts have text equivalents.
  - Icons are `aria-hidden` when labelled.
  - `lang` attribute set per language and on mixed-language spans (medical English inside Hindi text).
- **Forms:**
  - Labels are always visible, never placeholder-only.
  - Errors are described in text next to the field, plus a summary at the top.
  - Inputs use `inputmode="numeric"` for vitals and `autocomplete` attributes.
  - Redundant entry is avoided (WCAG 3.3.7): profile data is reused across flows.
  - Accessible authentication: OTP autofill (`autocomplete="one-time-code"`) and no cognitive tests (3.3.8).
- **Dragging alternatives (2.5.7):** image zoom also has +/− buttons, and the bottom sheet has a close button.
- **Low literacy:**
  - Read aloud on every card.
  - Icons and pictograms for doses and food relation.
  - Voice input everywhere.
  - Short sentences.
  - Numbers in digits.
  - Pictorial onboarding.
  - "Explain simpler" action on answers.
  - Confirmations by voice ("Say yes to confirm").
- **Visual impairment:** large text mode (125%), reflow at 400% zoom with no loss, high-contrast mode (text `#000`, borders `#000`), and a fully keyboard/voice-operable orb.
- **Language switcher:**
  - A globe button in the header, always visible, showing the current language in its own script (e.g. "हिन्दी").
  - It opens the LanguagePicker sheet. First run shows it as step 1.
  - Changing language switches UI, voice and read-aloud together, with separate overrides in Settings. The choice persists and isn't hidden in a menu.
  - Within answers, medical terms show English in brackets.
- **Mixed scripts:** `lang` attributes, plus line-height 1.7 for Indic scripts so matras (vowel signs) aren't clipped. Never truncate Indic text mid-cluster; use CSS line-clamp only on Latin.

---

## 6. Additional UX features (prioritised)

| Priority | Feature | Why | Backend touch-point |
|---|---|---|---|
| P0 | **Always-visible SOS + emergency info card** | Seconds matter; the card helps paramedics | Reuse `/me/profile`, `/me/allergies`, `/me/medications` |
| P0 | **Quick symptom chips** on Home and Ask | Low-literacy entry without typing | None (pre-fills `/triage/sessions`) |
| P0 | **Dose tracker** (Taken/Skip, streaks, missed-dose guidance) | Adherence is the biggest practical win | New `dose_logs` table + `POST /me/doses` |
| P0 | **Confidence + source indicators** on every AI output | Trust and calibrated reliance | Expose `confidence` and citations (exists for chat and Rx) |
| P1 | **Family profiles** (children, elderly parents) | Indian households manage care for others | New `household_members` (each a sub-user with its own DEK); `X-Profile-Id` header |
| P1 | **Doctor visit pack** (PDF: symptoms, medicines, abnormal labs, questions to ask) | Turns the app into better doctor visits | `GET /me/visit-summary.pdf` |
| P1 | **Share via WhatsApp** (summary/emergency card) | Dominant channel in India | Web Share API only |
| P1 | **Offline basics**: PWA app shell, cached profile/emergency card/medicines, queued dose logs, offline banner | Patchy connectivity | Service worker (Serwist) |
| P1 | **Refill reminder** (days of supply left from duration) | Prevents gaps | Computed client-side from `end_date` |
| P2 | **Caregiver mode** (large-type simplified Home for elderly users, set up by family) | Elderly users | Settings flag |
| P2 | **Nearby help**: Jan Aushadhi Kendra, hospitals, ICTC (maps deep links) | Actionable next step | Existing store list |
| P2 | **Gentle check-ins** ("Feeling better after 2 days?") | Catches worsening | Local scheduled notification |

---

## 7. Mobile vs desktop

- **Breakpoints:** `sm 640`, `md 768`, `lg 1024`, `xl 1280`. Design mobile at 360×800 (the most common Indian Android viewport) first.
- **Mobile:**
  - Bottom tab bar, 64px with safe-area inset.
  - The primary action sits in the thumb zone: sticky bottom CTA bars and the floating mic at bottom-right.
  - Destructive actions are kept away from the thumb arc.
  - Sheets instead of dialogs; full-width cards; horizontal chip scrollers with visible overflow hints.
- **Tablet:** two-column Home; the tab bar becomes a left rail with icons and labels.
- **Desktop:**
  - Left sidebar (240px) with labels.
  - Content max 1200px.
  - The Rx review split view; chat with a right context panel (current medicines, profile summary, sources).
  - Keyboard shortcuts: `/` to focus ask, `M` for mic, `Esc` to stop speaking.
- **Performance:** at most 200 KB of JS for the first route on 3G; lazy-load charts and the camera; fonts subset per script; skeletons rather than spinners.

---

## 8. Trust & safety UI

- **Disclaimers in layers, so they aren't banner-blind:**
  1. Onboarding acknowledgement (consent record).
  2. A persistent `DisclaimerChip` under every AI answer.
  3. A spoken one-liner at the start of voice sessions.
  4. Contextual stronger warnings (imaging, dose questions, pregnancy).
- **AI limitations:**
  - **ConfidencePill**, with its level derived from model agreement, retrieval hits and the deterministic checks.
  - Prescription fields: amber "Please check".
  - Images: always "Radiologist review needed".
  - An "About AI limits" page in plain language, with today's eval numbers explained simply (e.g. "reads about 9 in 10 printed lab values correctly").
- **Sources:**
  - `SourceBadge` on answers.
  - "No trusted source found — general knowledge" in warning style when retrieval is empty.
- **When to see a doctor:**
  - The urgency banner is always the first element of an answer or triage result.
  - "See doctor today" includes "Find a doctor near me" and an eSanjeevani (free government teleconsultation) link.
  - Red flags auto-open the EmergencySheet.
- **Never** show raw model output, JSON or stack traces. Errors use calm plain language with a next step.
- **Privacy cues:** a "🔒 Processed on this device" badge when Tier L; a visible indicator whenever a cloud service is used (consented); and an offline-mode badge in the header.

---

## 9. Implementation plan (frontend)

**Phase A — Foundation**
1. Rewrite tokens in `apps/web/app/globals.css` with the §2.1 palette (light, dark, high-contrast), spacing, radius, elevation and motion.
2. Move fonts to `next/font` with per-script subsets.
3. Add `lucide-react` (pinned) and `eslint-plugin-jsx-a11y`.

**Phase B — Components:** build `components/ui/*` (§4) and refactor the existing `AnswerCard.tsx`, `Onboarding.tsx` and `SettingsPanel.tsx` onto them.

**Phase C — Information architecture and pages:**
1. Restructure routes to the target layout:
   - `/welcome`, `/`
   - `/ask`, `/ask/check`
   - `/medicines`, `/medicines/[id]`, `/medicines/rx/*`
   - `/records/*`
   - `/me`, `/me/settings`, `/me/family`
2. Rewrite `components/Shell.tsx`: header with SOS, language and profile switcher; bottom tabs / sidebar; floating mic.
3. Keep the old routes redirecting.

**Phase D — Voice UX:** a `VoiceOrb` state machine on top of the existing `lib/voice.ts` (`startHandsFree`, `speak`, `stopSpeaking`, `isSpeaking`), summary-first read-aloud, suggested reply chips and voice commands.

**Phase E — Features (§6, P0 then P1):**
1. Backend additions:
   - `dose_logs` table and endpoints in `services/api/app/routers/records.py`;
   - `household_members` and profile scoping;
   - visit-summary PDF (reportlab).
2. Service worker for offline.

**Phase F — i18n:**
1. Extract all new strings into `lib/messages.ts` (per-language files).
2. Complete all 10 languages for every string (draft → native review checklist).
3. Add `lang` attributes on mixed spans.

**Reuse, don't rebuild:**
- `lib/api.ts`: `api`, `postSSE`, `ensureSession`.
- `lib/voice.ts`.
- `lib/i18n.tsx`: per-key fallback.
- Existing pages' data logic in `app/*/page.tsx`.
- Backend endpoints: `/me/reminders`, `/me/reminders.ics`, `/me/export/fhir`, `/me/timeline`, `/reports/*`, `/images/analyze`, `/medicines/*`, `/triage/*`.

---

## 10. Verification

1. **Automated:**
   - `npx tsc --noEmit` and ESLint with jsx-a11y.
   - Playwright + `@axe-core/playwright`: every route in light, dark, large-text and Hindi/Tamil, with zero serious or critical violations.
   - Visual snapshots at 360, 768 and 1280 widths.
2. **Journeys in the browser pane:** first-time user (welcome → language → consent → home tour); Rx upload with a MIRAGE sample; symptom chip → triage result; medicine search → alternatives; SOS from every tab.
3. **Accessibility checks:**
   - keyboard-only pass;
   - NVDA (Windows) screen-reader pass on Home, Ask and Rx review;
   - 200% and 400% zoom reflow;
   - `prefers-reduced-motion`;
   - contrast re-check with the computed table.
4. **Performance:** Lighthouse mobile (Moto G profile) with performance ≥ 85, accessibility 100 and best practices ≥ 95.
5. **Usability:** 5 moderated sessions (2 elderly, 2 low-literacy, 1 regional-language-only) on a mid-range Android, with task success for upload, symptom check and SOS.
