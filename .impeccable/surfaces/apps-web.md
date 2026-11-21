---
version: 1
slug: "apps-web"
primary_target: "apps/web"
related_targets: []
---

## Scope
The whole SushrutAI web app (`apps/web`): Home, Ask chat, symptom check, medicines, prescription reading, records, profile, family, care schedule, visit summary, settings (Operate), and the welcome/onboarding flow (closer to Persuade). Phone-first (360px+, mid-range Android) and desktop.

## Audience and job
Patients, family caregivers, elderly users and ASHA/ANM workers in India, often anxious, often in an Indian language, sometimes low-literacy. Job: know what to do next about a symptom, a medicine, a report or a family member, safely, by voice or touch. Constraints: SOS always visible; urgency styling unmistakable; "information, not a diagnosis"; 10 Indian scripts; WCAG 2.2 AA, 48px targets; reduced motion, large text, high contrast, Simple mode; dark mode; print styles for the visit summary.

## Direction contract
THESIS: The household's health kept the way an Indian home keeps its steel: one stacked tiffin carrier with a tier per person, and a dabba of round katoris for each day's medicines. It refuses the white-and-teal card stack of every health app and the cream notebook it replaces.

OWN-WORLD: Cool brushed-steel greys (light: pale steel ground, polished rim surfaces with a 1px bright top highlight; dark: gunmetal), ink-steel text. Spice colours are identity codes, one per family member (haldi, dhania, kesar, jeera, elaichi, kokum…). Jamun purple is reserved for "your next step": the primary action, the next dose, the current nav place. Red only for emergency/urgent. Round katori wells (inset shadow) for slots; rounded vessel radii (16–20px); Mukta headings, Noto Sans body, tabular numerals.

STORY: The visitor sees whose record is open (an engraved name tier), what to do now (one jamun-marked next step), and can speak at once. They believe it is calm, theirs, and honest that it is not a doctor. They take a dose, ask a question, or reach 108 in one tap.

FIRST VIEWPORT: Phone: gunmetal header with logo (dabba seen from above), search, language, SOS; under it the tiffin tiers (one per person, active tier engraved and joined to the page). Greeting, then today's dabba: morning/afternoon/night katoris with the next one jamun and a "Taken" action; then the voice ask well; then needs-attention; bottom tab bar with a filled katori marking the current place. Desktop: gunmetal side rail with the same nav, tiers across the top of the work area, dabba and ask in the main column, attention/vitals/tip in a side column.

FORM: Steel & Spice (household steel tiffin and masala dabba), position 5 on my ordered list, seed: seed:0415d7112389dd60. Raises kept: orienteering (single reserved next-step colour), Saville (spice code per person everywhere), Emigre (katori form at every scale), Crouwel (4px module, compartments as real cells), centre-rail (keyed sources), Ikeda (one tabular numeral scale, two-value high contrast).

Signature interaction: the dose katori fills (steel well → spice/ok fill) when marked taken; the active family tier lifts and engraves when switched. Motion grammar: short vertical "lid" movements (lift/settle, 160–220ms, no tilt, no bounce); nothing decorative loops except the listening voice well.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved
Native-speaker review of 8 languages is out of scope; family member colour assignment is derived from member id order.
