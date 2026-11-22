# SushrutAI — Desktop Mode & Dual-Mode Specification

Status: approved 2026-11-22 · Applies to: `apps/web` · Owner: frontend
Visual references: a dark navy glass calendar dashboard (floating pill icon rail, big greeting, translucent day tiles,
one glowing selected tile, a solid popover card) and a Spotify glass sidebar (collapsed icon rail vs expanded labelled
menu, glowing active notch).

Desktop is **not** the phone layout stretched. It is its own composition of the same data and actions, in its own
visual world ("Night Steel Glass"), sharing fonts, numerals, status meanings, spice colours and safety rules with
mobile ("Steel Tiffin", DESIGN.md).

---

## 1. Dual-mode switching

### 1.1 Preference
| | |
|---|---|
| Key | `prefs.ui` in `sushrut.prefs` (localStorage, per device) |
| Values | `"auto"` (default) · `"desktop"` · `"mobile"` |
| Where | Settings → Display & layout → **Layout**: Automatic / Desktop / Mobile (with thumbnails) |
| Quick toggle | Desktop: profile menu in the top bar ("Use mobile layout"). Mobile: Me page header button ("Desktop layout"). |
| Shortcut | `Ctrl+Shift+D` cycles desktop ↔ mobile (sets a forced value) |
| Feedback | A 3 s toast "Switched to desktop layout · Undo" |

### 1.2 Resolution
```
mode(pref, viewport) =
  pref == "desktop" → desktop
  pref == "mobile"  → mobile
  pref == "auto"    → desktop if width ≥ 1024 and (pointer: fine or width ≥ 1280) else mobile
```
`auto` is re-evaluated on `resize` (debounced 200 ms) and on `(pointer: fine)` changes. Forced modes never change by
themselves.

### 1.3 Before first paint
`lib/prefs-boot.ts` (inline script in `<head>`) computes the mode and writes:
- `html[data-ui="desktop" | "mobile"]`
- `html[data-ui-forced]` when the user forced a mode
- `html[data-ui-squeeze]` when desktop is forced on a viewport < 1024 px
React reads the attribute through `useUiMode()` (`lib/ui-mode.ts`), which also listens for changes, so there is no
layout flash and no hydration mismatch (shell renders after `PrefsProvider` mounts, as today).

### 1.4 Overrides vs the real viewport
- **Desktop forced on a narrow screen (< 1024 px):** set `<meta name="viewport" content="width=1280">` (pinch-zoom
  allowed), exactly like a browser's "desktop site", so the real desktop layout renders at 1280 and is scaled by the
  browser. Show a dismissible notice under the top bar: "Desktop layout on a small screen · Switch to mobile".
- **Mobile forced on a wide screen (≥ 640 px):** the mobile app runs in a centred **440 px frame** over the ambient
  background (24 px radius, E3): a same-origin `<iframe>` of the app itself (`components/desktop/MobileFrame.tsx`).
  Inside it every breakpoint sees a phone-width viewport, so the bottom bar, sheets and floating mic behave exactly as
  on a phone (a CSS column could not stop `md:`/`lg:` media queries from firing). Session and prefs are shared (same
  origin); the frame reports its path with `postMessage` and the address bar follows. A "Use the desktop layout"
  button sits outside the frame.
- Simple mode, large text (`data-text`), high contrast, reduced motion and print work in both modes. Print is always
  the light, chrome-free document. SOS is visible in both modes on every screen.

### 1.5 Code structure
- Tailwind v4 custom variants in `globals.css`:
  `@custom-variant desk (&:where([data-ui=desktop], [data-ui=desktop] *));`
  `@custom-variant mob (&:where([data-ui=mobile], [data-ui=mobile] *));`
  Shell-level layout uses `desk:` / `mob:` instead of `md:` / `lg:`; breakpoints inside desktop use `desk:` plus
  container widths (§6).
- `components/Shell.tsx` renders `<DesktopShell>` or the existing mobile shell by `useUiMode()`.
- Pages: `<ByMode desktop={<DesktopHome/>} mobile={<MobileHome/>}/>`; both compositions use the same data hooks.

---

## 2. Visual world: Night Steel Glass (desktop)

### 2.1 Tokens (dark — default on desktop)
| Token | Value | Use |
|---|---|---|
| `--d-ground` | `#0B1118` | page behind the frame |
| ambient | radial `#12324D` at (12% 8%) 55%, radial `#0F2236` at (88% 92%) 40%, 3 diagonal bands `rgb(120 170 220 / 4%)` | fixed background (`DesktopBackdrop`) |
| `--d-frame` | `rgb(22 30 39 / 78%)` + `backdrop-filter: blur(24px) saturate(120%)` | the app frame — the one glass layer |
| `--d-frame-line` | `rgb(255 255 255 / 6%)` + inset `0 1px 0 rgb(255 255 255 / 6%)` | frame border + top highlight |
| `--d-tile` | `rgb(255 255 255 / 3.5%)`; hover 5.5% | tiles |
| `--d-tile-line` | `rgb(255 255 255 / 7%)`; hover 12% | tile border |
| `--d-pop` | `#1C2631` (solid) | popovers, dialogs, menus, drawers |
| `--d-ink` / `--d-muted` / `--d-faint` | `#E8EEF3` / `#97A6B3` / `#6B7885` | text (faint: large labels only) |
| `--d-accent-500` | `#3A8DFF` | glows, rail notch, focus ring, indicators |
| `--d-accent-600` | `#1E6FE6` | accent button fill (white text ≥ 4.5:1) |
| `--d-accent-soft` | `rgb(58 141 255 / 16%)` | selected rows, chips |
| `--d-glow` | `radial-gradient(120% 90% at 50% 0%, rgb(58 141 255 / 55%), transparent 70%)` | the one "next / selected" tile |
| `--d-ok` / `--d-warn` / `--d-danger` | `#3CCB7F` / `#F2B544` / `#FF6B6B` (14% tint + full border) | status |
| `--d-emergency` | `#E5383B` solid | SOS, emergency only |
| shadows | tile: none · raised `0 12px 32px -18px rgb(0 0 0 / 55%)` · pop `0 24px 60px -20px rgb(0 0 0 / 60%)` | depth |

### 2.2 Tokens (light — "Frosted Steel")
Ground `#E8EEF3` with bands `rgb(31 111 230 / 5%)`; frame `rgb(255 255 255 / 70%)` + blur 24; tiles
`rgb(255 255 255 / 80%)` with border `rgb(19 32 43 / 8%)`; pop `#FFFFFF`; ink `#13202B`, muted `#55636F`;
accent-600 `#1D6BDE` (white 4.8:1), accent-500 `#2F7FF0`; shadows steel-ink based (never pure black).

Theme: desktop opens dark unless the user chose Light in Settings (`data-theme`). High contrast collapses glass to
solid `#000`/`#FFF` with 2 px rings, as on mobile.

### 2.3 Rules
- **One accent action per view** (the next step): a single accent-600 button or one glowing tile. Everything else is
  glass, outline or text.
- **Glass only for the frame.** Tiles are translucent fills, popovers are solid. No blur on text containers.
- **Status keeps its meaning**, spices still mean people, red still means now.
- No side stripes on boxes, no gradient text, no hard offset shadows, no animated numbers on medical values.

### 2.4 Type (Mukta display · Noto Sans body · tabular numerals · 17 px root)
| Role | Size / weight | Notes |
|---|---|---|
| Greeting | 2.5rem / 800 (2.75rem ≥ 1600) | "Good morning, Asha" — dashboard only |
| Page title | 2rem / 800 | top bar left |
| Section | 1.25rem / 700 | |
| Tile title | 1.0625rem / 700 | |
| Body | 1rem / 1.6 (1.7 Indic) | prose ≤ 72ch |
| Meta | 0.8125rem / 600, muted | |
| Numerals | 1.5–2.25rem / 700 tabular | doses, readings |

### 2.5 Shape, spacing, density
Radii: frame 28 · tile 18 · control 12 · popover 20 · pill 999. Tile padding 24 px; gaps 16 px (20 px ≥ 1600).
Controls 44 px with a fine pointer; hit areas stay ≥ 44 px. Focus ring: 3 px `--d-accent-500`, 2 px offset.

---

## 3. Navigation: hybrid (floating icon rail + contextual top bar)

```
┌──────────────────────────────── viewport (ambient backdrop) ───────────────────────────────┐
│ ┌────────────────────────────────── app frame (glass, r28) ──────────────────────────────┐ │
│ │ ╭────╮  Good morning, Asha                    [⌘K Search…]   🎙  अ/A  (A)(R)(+)   SOS │ │
│ │ │ ◉  │  2 doses left today · 1 report to check                                        │ │
│ │ │    │ ┌───────────────────────── content (12-col grid) ─────────────────────────────┐ │ │
│ │ │ ⌂ ◀│ │                                                                              │ │ │
│ │ │ 💬 │ │                                                                              │ │ │
│ │ │ 💊 │ │                                                                              │ │ │
│ │ │ 📁 │ │                                                                              │ │ │
│ │ │ 👤 │ │                                                                              │ │ │
│ │ │    │ │                                                                              │ │ │
│ │ │ ⚙  │ │                                                                              │ │ │
│ │ │ (A)│ └──────────────────────────────────────────────────────────────────────────────┘ │ │
│ │ ╰────╯                                                                                  │ │
│ └─────────────────────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```
- **Rail:** floating pill 76 px wide, radius 38, inset 16 px; logo (dabba) top; 48 px circular buttons Home, Ask,
  Medicines, Records, Me; Settings + avatar at the bottom. Current: accent-600 circle + 3 px glowing notch
  (`--d-accent-500`, blur 6 px) just outside the rail's left edge. Tooltips after 250 ms.
- **Expanded rail:** hover-intent 400 ms (or `[` to pin) opens a 248 px labelled panel (Spotify "expanded" state)
  that overlays the content; pinned, it pushes the content. Footer: 108 · 112 · Tele-MANAS 14416, privacy badge.
- **Top bar** (72 px, no fill, inside the frame): left = page title (dashboard: greeting + summary); centre = ⌘K
  command search (pages, chats, records, medicines, actions like "log BP"); right = voice (Alt+M), language,
  family switcher (spice avatars, current one ringed), SOS (solid emergency, always last).
- **Why:** content (chat, documents, calendar) gets the width; global actions live in one predictable bar; icons
  match the references while labels stay one hover/pin away for older users; SOS is never hidden.
- Keyboard: `g h/a/m/r/p` go to pages, `[` pin rail, `⌘K`/`Ctrl+K` search, `Alt+M` voice, `?` shortcuts.

---

## 4. Screens

### 4.1 Dashboard `/`
```
Good morning, Asha                                           [⌘K]  🎙  अ  (A)(R)  SOS
2 doses left today · 1 report to check
┌──────── Voice (5) ────────┐ ┌─────────────── Today's medicines (7) ───────────────┐
│  ( 🎙 )  Ask anything…    │ │ Morning 8:00    Afternoon 14:00   Night 21:00        │
│  [chip] [chip] [chip]     │ │ ✓ Metformin     ▣ Telma 40 (glow) ○ Atorva 10         │
└───────────────────────────┘ │                  in 25 min [Taken]                   │
┌──────────── Care calendar (8) ───────────────┐└─────────────────────────────────────┘
│ November 2026 ▾          ‹ ›                 │ ┌──── Attention (4) ────┐
│ Su Mo Tu We Th Fr Sa                          │ │ HbA1c 8.1 high  [Ask] │
│ [1][2][3][4·][5][6][7]  tiles r14, events as  │ │ Td dose 2 due   [→]   │
│ ...         [22 glow]   dots + 2-line labels  │ └───────────────────────┘
└───────────────────────────────────────────────┘ ┌──── Vitals (4) ───────┐
┌─ Recent chats (4) ─┐┌─ Family (4) ─┐┌─ Tip (4) ─┐│ BP 150/95 ↑ trend     │
```
- **Today's medicines = the dose day-line** (`components/DoseLine.tsx`, same component on phone and desktop): a
  6 am–11 pm line with one station per dose time (taken: green tick · next: accent with a soft halo · passed and
  unmarked: amber · to come: empty well), a "now" needle with the passed part of the day filled, and one line below
  with the next dose, its time and "Taken". A station opens that time's medicines (Taken / Skip each). ~80 px tall on
  a phone, ~110 px on desktop, replacing the three-slot grid.
- Day tile click → popover card (solid, r20) listing that day: doses (taken/missed), vaccines, check-ups,
  refills, follow-ups, with actions ("Mark taken", "Ask about this"). Today's tile glows.
- Data: `/me/doses`, `/me/care-schedule`, `/me/follow-ups`, `/me/refills`, `/me/vitals`, `/reports/labs`,
  `/chat/sessions`, suggestions; month aggregation via `GET /me/calendar?month=YYYY-MM`.

### 4.2 Health assistant `/ask`
- Conversation column centred, max 860 px; answer sections are glass tiles; user bubbles right-aligned.
- Composer dock: floating glass bar at the bottom of the content area (r20): model switch · mic · hands-free ·
  attach · text · send (accent). Suggestion chips sit just above the dock after each answer.
- Chats: floating window anchored under the top bar (from the "Chats" button or ⌘K "chats").
- Inspector drawer (right, 360 px, closed by default, `i` toggles): profile strip, this answer's sources, outline of
  questions in the chat, "save for doctor" list. ≥ 1600 it can be docked.

### 4.3 Prescription upload & review `/medicines/rx`
- Steps header: Upload → Read → Check → Save (current step accent, done steps ok).
- Empty: full-panel drop zone (dashed hairline, r18) + recent uploads row.
- Review split view: left document viewer (zoom 100–400%, pan, page thumbnails); right table (medicine, strength,
  pattern 1-0-1, duration, confidence chip, warnings); inline edit; sticky footer Save (accent) · Discard · Delete.
  Same component (`SplitReview`) as the lab review.

### 4.4 History / Reports `/records`
- Left facets (240 px): type, date range, needs checking, outside range, person.
- Centre: library grid of document tiles (thumbnail, date, title, flags; 3–5 columns) ⇄ month timeline toggle.
- Right preview pane (360 px; drawer < 1280): selected report's values with range bars and trends; Ask · Check
  values · Delete.
- Tabs across the top: Reports · Lab results · Timeline · Scans · Vitals (underline tabs with sliding indicator).
- Vitals: large charts, 7/30/90/All segmented control, table toggle, spoken summary.

### 4.5 Patient profile `/me`
Header tile: avatar with spice ring, name, age, sex, blood group, quick facts. Left column: conditions, allergies,
current medicines (inline-editable lists). Right column: care schedule (calendar), family members, emergency card
preview, visit summary shortcut.

### 4.6 Medicine details `/medicines/[id]`
Hero: name, composition, form, MRP vs Jan Aushadhi price and saving. Tabs: How to take · Side effects ·
Interactions with my medicines · Cheaper options (table). Right sticky panel: In my schedule (times, refill days
left), Ask about this, Add to my medicines.

### 4.7 Settings `/me/settings`
Left section list: Language & voice · Display & layout · Answer speed · Privacy · Notifications · Data & export.
Right content pane. The Layout switch (Automatic / Desktop / Mobile) with preview thumbnails lives in Display &
layout.

---

## 5. Component variants
| Component | Mobile | Desktop |
|---|---|---|
| Navigation | bottom tab bar | floating icon rail (+ expanded panel) |
| Header | gunmetal header with family tiers | transparent top bar, family switcher avatars |
| Container | lid card (rim + E1) | glass tile (fill + hairline) |
| Overlays | bottom sheet | centred dialog (forms), right drawer (inspector, filters), anchored popover (day, pickers) |
| Tabs | well segmented | underline tabs with sliding accent indicator |
| Chips | horizontal scroller | wrapping rows |
| Buttons | 48 px | 44 px (fine pointer), hover + press states |
| Voice | floating mic FAB | top-bar voice button + Alt+M; panel as popover |
| Tooltips / context menus / `?` overlay | — | yes |

## 6. Responsiveness inside desktop
| Tier | Width | Behaviour |
|---|---|---|
| D1 | 1024–1279 | rail icons; expanded rail overlays; bento 8 columns; inspector/preview are drawers; split views stack doc over values (resizable) |
| D2 | 1280–1599 | 12-col bento; one docked side pane 360 px; split 50/50 |
| D3 | 1600–1919 | rail may stay pinned 248 px; side pane 400 px; greeting 2.75rem |
| D4 | ≥ 1920 | content max 1680 px centred; chat 860 px + docked inspector; library ≤ 5 columns |

## 7. Motion
| Token | Value |
|---|---|
| `--t-micro` | 120 ms (hover, press) |
| `--t-small` | 200 ms (chips, tooltips) |
| `--t-panel` | 280 ms (drawers, popovers, dialogs) |
| `--t-page` | 360 ms (route) |
| `--ease-out` | `cubic-bezier(0.16, 1, 0.3, 1)` entries |
| `--ease-in-out` | `cubic-bezier(0.65, 0, 0.35, 1)` moves |
| `--ease-in` | `cubic-bezier(0.4, 0, 1, 1)` exits at 70 % duration |

Patterns: page = View Transitions cross-fade + 8 px rise of the content (frame and rail stay); dialog = fade + scale
0.98→1 + backdrop blur 0→8 px; drawer = translateX 24 px→0 + fade; popover = 6 px rise from the anchor side; tile
hover = −2 px + border brighten + glow (120 ms); press = scale 0.98 (80 ms); selected glow fades in 300 ms; lists
stagger 30 ms (≤ 6 items, first load only). Loops only for the listening voice well, the thinking indicator and
skeletons. No bounce, tilt, parallax or animated medical numbers.

Reduced motion (`prefers-reduced-motion` or `data-motion="reduce"`): no transforms, opacity fades ≤ 80 ms, no
stagger, static skeleton tint, View Transitions off.

Performance: animate only transform, opacity, filter, clip-path; `will-change` only during a transition; nothing on
scroll.

## 8. Acceptance
- Every screen at 1024 / 1280 / 1600 / 1920 in dark and light, English and Hindi, without overflow.
- Forced desktop at 390 px renders the desktop layout at 1280 with the notice; forced mobile at 1440 px renders the
  440 px column with nav and sheets confined.
- Contrast: body ≥ 4.5:1, accent button text ≥ 4.5:1, large/faint labels ≥ 3:1.
- Keyboard: every rail item, ⌘K, drawer, popover and dialog reachable, focus trapped in dialogs, Esc closes.
