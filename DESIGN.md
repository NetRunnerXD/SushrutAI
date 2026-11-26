---
name: SushrutAI
description: Health help in your language, kept the way an Indian home keeps its steel.
colors:
  jamun: "#5B2A7A"
  jamun-deep: "#46205F"
  jamun-ink: "#FFFFFF"
  jamun-soft: "#EEE5F4"
  steel-ground: "#E4E8EB"
  polished-lid: "#F3F5F7"
  pressed-steel: "#DCE1E5"
  steel-ink: "#182028"
  steel-muted: "#4C5762"
  steel-rule: "#C3CBD2"
  steel-input-rule: "#6F7B86"
  gunmetal-rail: "#232B33"
  gunmetal-rail-raised: "#313A43"
  rail-text: "#EEF1F3"
  rail-muted: "#B1BBC4"
  rail-current: "#D7BCEB"
  rail-current-ink: "#24102F"
  link-indigo: "#1C4F86"
  link-indigo-soft: "#DDE7F2"
  haldi-mark: "#D18B06"
  haldi-soft: "#F5E5BE"
  dhania-ok: "#2B6634"
  dhania-ok-soft: "#DAEAD6"
  warn-amber: "#874800"
  warn-amber-soft: "#F5E2C0"
  danger-red: "#A5221A"
  danger-red-soft: "#F6DDD9"
  emergency-red: "#B3121B"
  spice-haldi: "#C98A0A"
  spice-dhania: "#4F7A3A"
  spice-kesar: "#C45A16"
  spice-neel: "#3C5A9A"
  spice-jeera: "#7D5230"
  spice-kokum: "#8E2C6B"
  gunmetal-ground: "#22292E"
  gunmetal-lid: "#2B3338"
  gunmetal-pressed: "#343D43"
  gunmetal-ink: "#ECEFF1"
  gunmetal-jamun: "#CBA6E3"
  gunmetal-rail-night: "#111518"
typography:
  display:
    fontFamily: "Mukta, Noto Sans (per-script), sans-serif"
    fontSize: "1.875rem"
    fontWeight: 800
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  headline:
    fontFamily: "Mukta, Noto Sans (per-script), sans-serif"
    fontSize: "1.3125rem"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Mukta, Noto Sans (per-script), sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.375
  body:
    fontFamily: "Noto Sans, Noto Sans Devanagari, Bengali, Tamil, Telugu, Gujarati, Kannada, Malayalam, Oriya, Gurmukhi, Nastaliq Urdu, system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Noto Sans, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.4
  numeral:
    fontFamily: "Noto Sans, system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1
    fontFeature: "tnum, lnum"
rounded:
  input: "12px"
  button: "14px"
  tile: "16px"
  lid: "18px"
  sheet: "22px"
  tray: "24px"
  katori: "9999px"
spacing:
  gutter-phone: "16px"
  gutter-tablet: "24px"
  gutter-desktop: "32px"
  card-pad: "16px"
  card-pad-wide: "20px"
  stack: "16px"
  target: "48px"
components:
  button-primary:
    backgroundColor: "{colors.jamun}"
    textColor: "{colors.jamun-ink}"
    rounded: "{rounded.button}"
    padding: "0.6rem 1.15rem"
    height: "48px"
  button-primary-hover:
    backgroundColor: "{colors.jamun-deep}"
  button-outline:
    backgroundColor: "{colors.polished-lid}"
    textColor: "{colors.steel-ink}"
    rounded: "{rounded.button}"
    padding: "0.6rem 1.15rem"
    height: "48px"
  button-outline-hover:
    backgroundColor: "{colors.pressed-steel}"
  button-ghost:
    backgroundColor: "{colors.pressed-steel}"
    textColor: "{colors.steel-ink}"
    rounded: "{rounded.button}"
    height: "48px"
  button-ghost-hover:
    backgroundColor: "{colors.steel-rule}"
  button-danger:
    backgroundColor: "{colors.danger-red-soft}"
    textColor: "{colors.danger-red}"
    rounded: "{rounded.button}"
    height: "48px"
  button-emergency:
    backgroundColor: "{colors.emergency-red}"
    textColor: "#FFFFFF"
    rounded: "{rounded.button}"
    height: "48px"
  input:
    backgroundColor: "{colors.polished-lid}"
    textColor: "{colors.steel-ink}"
    rounded: "{rounded.input}"
    padding: "0.65rem 0.85rem"
    height: "48px"
  card:
    backgroundColor: "{colors.polished-lid}"
    rounded: "{rounded.lid}"
    padding: "{spacing.card-pad}"
  compartments:
    backgroundColor: "{colors.polished-lid}"
    rounded: "{rounded.tray}"
  katori-well:
    backgroundColor: "{colors.pressed-steel}"
    textColor: "{colors.steel-ink}"
    rounded: "{rounded.katori}"
    size: "40px"
  dabba-katori-next:
    backgroundColor: "{colors.jamun}"
    textColor: "{colors.jamun-ink}"
    rounded: "{rounded.katori}"
    width: "6.5rem"
  dabba-katori-done:
    backgroundColor: "{colors.dhania-ok}"
    textColor: "#FFFFFF"
    rounded: "{rounded.katori}"
    width: "6.5rem"
  chip:
    backgroundColor: "{colors.polished-lid}"
    textColor: "{colors.steel-ink}"
    rounded: "{rounded.katori}"
    padding: "0 1rem"
    height: "48px"
  chip-selected:
    backgroundColor: "{colors.jamun-soft}"
    textColor: "{colors.steel-ink}"
  family-tier:
    backgroundColor: "{colors.gunmetal-rail-raised}"
    textColor: "{colors.rail-muted}"
    rounded: "14px 14px 0 0"
    padding: "0 0.875rem"
    height: "48px"
  family-tier-active:
    backgroundColor: "{colors.steel-ground}"
    textColor: "{colors.steel-ink}"
  rail:
    backgroundColor: "{colors.gunmetal-rail}"
    textColor: "{colors.rail-text}"
    width: "248px"
  rail-link-current:
    backgroundColor: "{colors.rail-current}"
    textColor: "{colors.rail-current-ink}"
    rounded: "{rounded.katori}"
    size: "44px"
  sheet:
    backgroundColor: "{colors.polished-lid}"
    rounded: "{rounded.sheet}"
    padding: "20px"
---

# Design System: SushrutAI

## Overview

**Creative North Star: "The Steel Tiffin"**

The household's health is kept the way an Indian home keeps its steel: one stacked tiffin carrier with a tier per person, and a dabba of round katoris for each day's medicines. Surfaces are brushed and polished steel, cool and calm, with a bright rolled rim along the top edge and round pressed wells for every slot. Colour does not decorate; it codes. Each person in the family owns one spice colour, jamun purple means "your next step", and red means only emergency or urgent.

Density is low and the hand is generous: 17px root type, 48px minimum targets, one clear next step per view. The shell is gunmetal (a side rail on tablet and desktop, a header on phone) with the family tiers hanging from it, so whose record is open is never in doubt. Motion is a lid lifting and settling: short vertical moves, no tilt and no bounce.

The system rejects the white-and-teal card stack of generic health apps and the cream paper notebook it replaced.

**Key Characteristics:**
- Cool brushed-steel ground, polished lids with a white top highlight, round inset katori wells.
- Spice colours as identity codes, one per family member; haldi is always the account holder.
- Jamun purple reserved for the primary action, the next dose and the current place.
- Red only for emergency and urgent states; SOS always visible.
- Mukta headings over a ten-script Noto Sans body, tabular numerals everywhere numbers matter.
- Rounded vessel radii (12px to 24px) and full circles for slots and marks.

## Colors

A cool steel neutral field carrying three narrow, meaning-bound colour families: jamun for the next step, spices for people, and a clinical tone scale for status.

### Primary
- **Jamun** (`jamun`): the next step and nothing else. Primary buttons, the next-dose katori, the current bottom-tab katori, the current step in a stepper, the listening voice well, the focus ring, checkbox and radio accents. Hover deepens to **Jamun Deep** (`jamun-deep`). **Jamun Soft** (`jamun-soft`) is its tint: the next-dose row, a selected chip, the input focus halo. On gunmetal it lifts to **Rail Jamun** (`rail-current`) so it stays legible on dark steel.

### Secondary
- **The Spice Codes** (`spice-haldi`, `spice-dhania`, `spice-kesar`, `spice-neel`, `spice-jeera`, `spice-kokum`): one per person, in the order they were added and remembered on the device so colours never shuffle. Haldi is always the account holder. A spice appears as a dot on the family tier, a 3px rim on the avatar katori and on the "viewing for" strip. Spices mark people; they never carry text on steel.
- **Haldi Mark** (`haldi-mark`, `haldi-soft`): highlights and selection (text selection is `haldi-soft`), the centre katori of the logo. Never text on steel.

### Tertiary (status scale)
- **Dhania OK** (`dhania-ok`, `dhania-ok-soft`): taken, normal, self care, sources present. A filled dose katori is solid dhania.
- **Warn Amber** (`warn-amber`, `warn-amber-soft`): see a doctor this week, low readings, offline strip, missing sources.
- **Danger Red** (`danger-red`, `danger-red-soft`): see a doctor within 24 hours, high readings, destructive actions (as a tint, never solid).
- **Emergency Red** (`emergency-red`): SOS and emergency urgency only, always solid with white text.
- **Link Indigo** (`link-indigo`, `link-indigo-soft`): underlined links and info banners.

### Neutral
- **Steel Ground** (`steel-ground`): the page.
- **Polished Lid** (`polished-lid`): cards, trays, sheets, the bottom bar, inputs.
- **Pressed Steel** (`pressed-steel`): the inside of a katori; wells, ghost buttons, skeletons.
- **Steel Ink** (`steel-ink`) and **Steel Muted** (`steel-muted`): text and secondary text.
- **Steel Rule** (`steel-rule`) for dividers and card borders; **Input Rule** (`steel-input-rule`) for 1.5px field and outline-button borders.
- **Gunmetal Rail** (`gunmetal-rail`, `gunmetal-rail-raised`, `rail-text`, `rail-muted`): the side rail and header; inactive family tiers sit in the raised gunmetal.

### Themes
- **Gunmetal (dark)** is the same steel at night: `gunmetal-ground`, `gunmetal-lid`, `gunmetal-pressed`, `gunmetal-ink`, with jamun lifted to `gunmetal-jamun` and the rail dropped to `gunmetal-rail-night`. Spices and status colours are lightened per theme (full set in the sidecar). It follows the OS unless `data-theme` forces light or dark.
- **High contrast** (`data-contrast="high"`): two values, white and black. Jamun becomes black, all rims and elevation collapse to 2px black rings, wells become 2px black inset rings.
- **Print**: always light and ink-friendly; white surfaces, black text, a deeper jamun (`#3D1257`), no rims, wells or shadows, no app chrome.

### Named Rules
**The One Next Step Rule.** Jamun marks the user's next step: the primary action, the next dose, the current place. If two things on a screen are jamun, one of them is wrong.

**The Spice Is a Person Rule.** A spice colour always means a household member and is assigned by the family order, never chosen for decoration. Haldi is the account holder.

**The Red Means Now Rule.** Emergency Red appears only on SOS and emergency urgency. Danger Red appears only as a tint with a full border for urgent states and destructive actions.

## Typography

**Display Font:** Mukta (Ek Type), Latin and Devanagari, weights 600/700/800, with each script falling through to its Noto Sans family.
**Body Font:** Noto Sans plus Noto Sans Devanagari, Bengali, Tamil, Telugu, Gujarati, Kannada, Malayalam, Oriya and Gurmukhi, and Noto Nastaliq Urdu (400/600/700), self-hosted and subset per script. Urdu gets a taller line height (2.1) because Nastaliq slants and stacks, and the whole layout mirrors right to left (logical `ps-`/`ms-`/`start-` classes; direction arrows flip).
**Numerals:** the body face with tabular, lining figures (`font-mono` utility), so doses, times and readings align on one numeral scale.

**Character:** an Indian humanist sans for headings, warm and sturdy, over a neutral, very legible body that reads the same in ten scripts.

### Hierarchy
- **Display** (800, 1.875rem, 2.25rem from 1024px, line-height 1.15, -0.01em, balanced wrap): the page title (`h1`).
- **Headline** (700, 1.3125rem, line-height 1.3): card and section titles (`h2`), sheet titles, the next-dose medicine name.
- **Title** (700, 1.125rem, Mukta): sub-card titles, banner titles, the "did I hear this right" label.
- **Body** (400, 1rem on a 17px root, line-height 1.6; 1.7 for Indic scripts): reading text. Secondary text sits at 0.9375rem in Steel Muted. Prose is held to `max-w-prose`.
- **Label** (600, 0.875rem): chip, pill, slot and meta labels; 0.75rem only beneath katoris.
- **Numeral** (700, 1.375rem to 1.75rem, tabular): katori counts, vitals, dose times; numeric inputs render at 1.1875rem, 600.

### Named Rules
**The One Numeral Scale Rule.** Every number a person acts on (a dose, a time, a reading) uses tabular lining figures in the body face. No separate monospace family.

**The Root Scales Everything Rule.** Sizes are in rem from a 17px root; large text sets the root to 21px or 24px (`data-text="lg" | "xl"`) and the whole interface scales with it.

## Layout

Phone first. A single column with a 16px gutter (24px at 768px, 32px at 1024px), held to a 1200px work area. Vertical rhythm is 16px between blocks; cards pad 16px, 20px from 640px.

- **Phone (under 768px):** a sticky gunmetal header (logo, search, profile, language, SOS) with the family tiers hanging beneath it, then the content column, then a fixed bottom tab bar on a polished-lid surface with a soft upward shadow. The current tab is a filled jamun katori. The main column reserves bottom padding for the bar, the floating mic and the safe area. When the on-screen keyboard opens (`data-kb`), the bottom bar and floating mic step aside.
- **Tablet (768px):** the header loses its logo and a gunmetal icon rail (84px) takes the left column of the app grid.
- **Desktop (1024px):** the rail opens to 248px with labels unless `data-sidebar="rail"` keeps it as icons (toggle with `[`). Tiers run across the top of the work area; the dabba and ask well sit in the main column; attention, tip and snapshot sit in one compartmented tray in the side column.
- **Simple mode:** the navigation drops to Home, Ask and Medicines and the floating mic is hidden; SOS stays in the header on every screen.
- Nothing scrolls horizontally at 360px. Horizontal overflow is allowed only inside deliberate scrollers (the family tiers, chip rows) that hide their scrollbar on touch and show a thin one under a fine pointer.

## Elevation & Depth

Depth is steel light, not floating cards. Every lid carries a bright 1.5px white top highlight (the rim) plus a soft, low, cool ambient shadow; every slot is pressed in with an inset shadow (the well). Three ambient levels exist and are used sparingly: resting lids, raised confirmations, and sheets. In gunmetal the rim fades to a 9% white line and shadows deepen; in high contrast and print all of it collapses to rings or nothing.

### Shadow Vocabulary
- **Rim** (`box-shadow: inset 0 1.5px 0 #FFFFFF, inset 0 -1px 0 rgb(24 32 40 / 5%)`): every polished surface; cards, trays, outline buttons, unselected chips, the active family tier.
- **Well** (`box-shadow: inset 0 2px 5px rgb(24 32 40 / 17%), inset 0 -1px 0 rgb(255 255 255 / 75%)`): katoris, vital tiles, source numbers, range-bar tracks, progress tracks, the sheet grabber.
- **E1** (`box-shadow: 0 1px 2px rgb(24 32 40 / 7%), 0 6px 16px -10px rgb(24 32 40 / 26%)`): resting cards, trays and the primary button.
- **E2** (`box-shadow: 0 2px 4px rgb(24 32 40 / 8%), 0 12px 28px -14px rgb(24 32 40 / 36%)`): the voice well and the voice-confirm panel.
- **E3** (`box-shadow: 0 4px 10px rgb(24 32 40 / 10%), 0 28px 56px -20px rgb(24 32 40 / 48%)`): sheets and dialogs.

### Named Rules
**The Lid and Well Rule.** Things you read sit on a lid (rim plus E1). Things that hold a value or a slot are pressed into a well. Do not invert them.

**The Steel Light Rule.** Shadows are cool (steel-ink based, never pure black on light), soft and low. No hard offset shadows.

## Shapes

Rounded vessel forms. Corners grow with the size of the vessel: fields 12px, buttons and tiers 14px, tiles and banners 16px, cards 18px, sheets 22px, trays 24px. Every slot, mark, avatar, chip, pill and icon holder is a full circle: a katori. Borders are hairline Steel Rule on lids and 1.5px Input Rule on fields and outline buttons. Family tiers are rounded only on top (14px 14px 0 0) so the active one joins the page below it. The logo is a health cross in blue-to-green with growing leaves and a molecule (`apps/web/public/logo.png`; app sizes in `public/icons/`).

## Components

### Buttons
Solid, tactile steel controls that press down a pixel.
- **Shape:** gently rounded (14px), at least 48px tall and wide; large size 56px.
- **Primary:** jamun with white text, a faint inner top highlight and E1; bold label. Hover deepens to Jamun Deep.
- **Outline (secondary):** polished lid with a 1.5px Input Rule border and the rim; hover to pressed steel.
- **Ghost:** pressed steel, hover to Steel Rule.
- **Danger:** danger tint with danger text, never solid red.
- **Emergency:** solid Emergency Red with white text; used by SOS and emergency actions only.
- **States:** active presses down 1px; disabled is 50% opacity; focus is the global 3px jamun ring with a 2px offset. Icon buttons are 48px circles.

### Chips
- **Style:** full pill, 48px tall, 1.5px Input Rule border on a polished lid with the rim, semibold label, optional leading icon.
- **Selected:** jamun border on a jamun-soft fill with a check in place of the icon; `aria-pressed` carries the state.

### Cards / Containers
- **Card (a lid):** polished lid, hairline Steel Rule border, 18px radius, rim plus E1, 16px padding (20px from 640px).
- **Compartments (a tray):** one 24px lid divided by hairline rules; cards inside drop their own lid and become cells. Used for the desktop side-column tray.
- **Card header:** an optional katori icon (40px) beside a headline and muted sub-line, with an action at the end.
- **Empty state:** a pressed-steel panel (16px) with a 56px katori or illustration, muted text and one action.

### Inputs / Fields
- **Style:** polished lid, 1.5px Input Rule border, 12px radius, 48px tall; placeholder in Steel Muted; caret in jamun.
- **Focus:** border turns jamun with a 3px jamun-soft halo (replacing the outline ring for fields).
- **Numeric entry:** 1.1875rem, 600, tabular figures.

### Banners and Urgency
- **Banner:** 16px radius, a 1.5px full border and a tint of the tone, a 24px leading icon, Mukta title. Tones: info, success, warning, danger, emergency. Danger and emergency are `role="alert"`, the rest `role="status"`.
- **Urgency scale (the single severity scale used everywhere):** emergency is solid Emergency Red with white text and a white "open SOS" button; see a doctor in 24 hours is the danger tint with a full danger border; this week is the warn tint; self care is the dhania tint. Pills reuse the same tones at full radius.
- **Trust marks:** a confidence pill (ok/warn/danger tints, expandable help), a source list with numbered katori markers (or a warn line when there are no sources), a privacy badge (local in dhania, cloud in indigo), and the "Information, not a diagnosis" disclaimer chip with an expandable long form, present wherever health guidance is shown.

### Navigation
- **Rail:** gunmetal, 84px icons on tablet, 248px with labels on desktop. Each link is a 56px row with a 44px katori icon: inactive katoris are pressed into raised gunmetal, the current one is filled with Rail Jamun. Emergency numbers (108, 112, Tele-MANAS 14416) and the privacy badge sit at the foot.
- **Header:** gunmetal and sticky; SOS is always the last control.
- **Family tiers:** one tab per person hanging from the header, each with its spice dot. Inactive tiers sit back in raised gunmetal; the open record's tier is steel-ground, engraved (extrabold, uppercase, 0.06em tracking), carries the rim and lifts into place. An "add" tier ends the row. A "viewing for" strip with the person's spice dot shows whenever a family member's record is open.
- **Bottom tab bar (phone):** polished lid, 64px cells; the current place is a 36px filled jamun katori with a bold jamun label.

### Sheets
Bottom sheet on phone (22px top corners, a well grabber), centred dialog from 640px; polished lid with rim and E3 over a 60% gunmetal scrim. Focus is trapped and restored, Esc closes. The emergency tone swaps the hairline for a 2px Emergency Red border and a danger title.

### The Dabba (signature)
The day's medicines as three katoris (morning, afternoon, night), each up to 6.5rem across in a 24px tray. An empty or open katori is a steel well with a tabular "done/total" count; the katori holding the next dose is solid jamun with a 4px jamun-soft halo; a katori whose doses are all taken fills solid dhania with a check. Under it, one jamun-soft panel names the next dose with a single primary Taken button. The dose pictogram reuses 56px wells for the "1-0-1" pattern.

### The Voice Well (signature)
A round steel lid in Steel Ink with an inner ring and E2. Listening turns it jamun, scales it slightly with the voice level and sends out a single looping ripple; transcribing shows a spinning dashed ring. The "did I hear this right" panel follows with an editable transcript and a visible, pausable auto-send countdown in a well track.

### Medical readouts
- **Vital tile:** a 16px well with a muted label, a 1.625rem tabular value with its unit, and a status dot and word in the tone colour.
- **Range bar:** a well track tinted warn with a dhania normal band and a round marker (dhania or danger) ringed in the lid colour.
- **Flag tag and steps:** tinted pills for high/low/normal; stepper circles are dhania when done, jamun when current, wells when ahead.

### Motion
One grammar: a lid lifts and settles. Lift is 6px up over 200ms, the katori fill scales in over 220ms, sheets rise 24px over 240ms, all on an exponential ease-out (`cubic-bezier(0.16, 1, 0.3, 1)`); no tilt and no bounce. Only the listening voice well loops (plus the thinking "simmer" of three katoris and skeleton shimmer while loading). Under `prefers-reduced-motion` or `data-motion="reduce"` every animation and transition is off and the ripple and spinner are removed.

## Do's and Don'ts

### Do:
- **Do** keep SOS visible in the header on every screen, in simple mode, and with the keyboard open.
- **Do** use the one urgency scale everywhere: emergency (solid red), see a doctor in 24 hours (danger tint plus full border), this week (warn), self care (ok).
- **Do** give every banner a full 1.5px border and a tint of its tone.
- **Do** show the "Information, not a diagnosis" disclaimer wherever health guidance appears.
- **Do** make every target at least 48px and keep the 3px jamun focus ring with a 2px offset.
- **Do** drive preferences through attributes on `<html>`: `data-text`, `data-theme`, `data-contrast`, `data-motion`, `data-sidebar`, applied before first paint.
- **Do** honour simple mode: fewer destinations, bigger Home, everything read aloud.
- **Do** keep print styles: always light, ink-friendly, no app chrome.
- **Do** check every screen at 360px with no horizontal page scroll.
- **Do** put shared component classes in `@layer components` so utilities win.
- **Do** put slots and small marks in round katori wells and readable content on rimmed lids.

### Don't:
- **Don't** use jamun for anything but the next step; never two jamun primary actions on one view.
- **Don't** use a spice colour for anything but a household member, or as text on steel.
- **Don't** use red outside emergency, urgent and destructive states, and never solid danger red for a destructive button.
- **Don't** use a side stripe on banners or alerts.
- **Don't** add tilt, bounce or looping motion beyond the listening voice well and loading indicators.
- **Don't** fall back to the white-and-teal card stack or the cream paper notebook look.
- **Don't** use hard offset shadows or pure black shadows on the light theme.

## Desktop mode: Night Steel Glass

Desktop (forced, or automatic from 1024 px with a fine pointer) is its own composition, not the phone layout widened.
Full specification: `docs/DESKTOP_UI_SPEC.md`. In short:

- **World:** a dark navy ground with a soft ambient field and diagonal light bands; one floating glass app frame
  (r28, blur 24); translucent tiles (r18) with hairlines; solid popovers. A light "Frosted Steel" variant exists.
- **Accent:** azure (`#1E6FE6` buttons, `#3A8DFF` glow and indicators) replaces jamun on desktop and keeps the
  same rule: one next step per view (one accent button or one glowing tile). Status colours, spices and red-means-now
  are unchanged.
- **Navigation:** a floating pill icon rail (expand/pin to a 248 px labelled panel) plus a contextual top bar with
  ⌘K search, voice, language, family switcher and SOS.
- **Motion:** micro 120 ms, small 200 ms, panel 280 ms, page 360 ms on the exponential ease-out; page cross-fade with
  an 8 px rise, tile hover lift of 2 px, 30 ms stagger. Reduced motion keeps opacity-only fades ≤ 80 ms.
- **Mode switch:** `prefs.ui` = auto | desktop | mobile; `html[data-ui]` is set before paint; desktop forced on a
  narrow screen renders at 1280 px (desktop-site viewport); mobile forced on a wide screen renders in a 440 px column.
