"""India's published care schedules, encoded as data.

Child vaccines: National Immunization Schedule (NIS), NHM / MoHFW (document dated 2020-05-29):
  https://nhm.gov.in/New_Updates_2018/NHM_Components/Immunization/report/National_%20Immunization_Schedule.pdf
  Encoded from the document's own table (verified 2026-11-21). The document does not list Vitamin A, so it is
  not included here. PCV was listed for selected states and JE for endemic districts only — shown as notes.
Pregnancy check-ups: ICMR Standard Treatment Workflow "Ante-natal management of normal pregnancy":
  https://www.icmr.gov.in/icmrobject/uploads/STWs/1771568619_ante-natalmanagementofnormalpregnancy-7.pdf
  First visit preferably in the first trimester; second visit in the second trimester; third 28–34 weeks;
  fourth 36–40 weeks; Td-1 at the first visit, Td-2 at least four weeks later (single booster if the first dose
  was within the last 3 years); Level II ultrasound 18–20 weeks (desirable test).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

NIS_URL = "https://nhm.gov.in/New_Updates_2018/NHM_Components/Immunization/report/National_%20Immunization_Schedule.pdf"
ANC_URL = "https://www.icmr.gov.in/icmrobject/uploads/STWs/1771568619_ante-natalmanagementofnormalpregnancy-7.pdf"
NIS_SOURCE = "National Immunization Schedule, NHM / MoHFW"
ANC_SOURCE = "ICMR Standard Treatment Workflow: antenatal care"

W, M, Y = 7, 30.4375, 365.25  # days per week / month / year


@dataclass(frozen=True)
class Item:
    code: str
    group: str            # child age group or pregnancy stage (shown as the timeline heading)
    title: str
    title_hi: str
    start: float          # days from birth (child) or from the first day of the last period (pregnancy)
    end: float            # after this it is "overdue": ask the health worker
    note: str = ""


def _vaccines(group: str, start: float, end: float, names: list[tuple[str, str, str, str]]) -> list[Item]:
    return [Item(code, group, en, hi, start, end, note) for code, en, hi, note in names]


PCV_NOTE = "Given in selected states (Bihar, HP, MP, UP, Rajasthan, Haryana) when this schedule was issued — ask your health centre."
JE_NOTE = "Only in districts where Japanese Encephalitis is common."

CHILD: list[Item] = [
    Item("bcg", "Birth", "BCG", "बीसीजी", 0, Y, "At birth or as early as possible, up to 1 year of age."),
    Item("hepb0", "Birth", "Hepatitis B — birth dose", "हेपेटाइटिस बी — जन्म खुराक", 0, 1, "Within 24 hours of birth."),
    Item("opv0", "Birth", "OPV-0 (polio drops)", "ओपीवी-0 (पोलियो ड्रॉप)", 0, 15, "Within the first 15 days."),
    *_vaccines("6 weeks", 6 * W, 10 * W, [
        ("opv1", "OPV-1 (polio drops)", "ओपीवी-1 (पोलियो ड्रॉप)", ""), ("penta1", "Pentavalent-1", "पेंटावेलेंट-1", ""),
        ("rvv1", "Rotavirus-1", "रोटावायरस-1", ""), ("fipv1", "fIPV-1 (polio injection)", "एफआईपीवी-1 (पोलियो इंजेक्शन)", ""),
        ("pcv1", "PCV-1 (pneumococcal)", "पीसीवी-1 (न्यूमोकोकल)", PCV_NOTE)]),
    *_vaccines("10 weeks", 10 * W, 14 * W, [
        ("opv2", "OPV-2 (polio drops)", "ओपीवी-2 (पोलियो ड्रॉप)", ""), ("penta2", "Pentavalent-2", "पेंटावेलेंट-2", ""),
        ("rvv2", "Rotavirus-2", "रोटावायरस-2", "")]),
    *_vaccines("14 weeks", 14 * W, 18 * W, [
        ("opv3", "OPV-3 (polio drops)", "ओपीवी-3 (पोलियो ड्रॉप)", ""), ("penta3", "Pentavalent-3", "पेंटावेलेंट-3", ""),
        ("fipv2", "fIPV-2 (polio injection)", "एफआईपीवी-2 (पोलियो इंजेक्शन)", ""), ("rvv3", "Rotavirus-3", "रोटावायरस-3", ""),
        ("pcv2", "PCV-2 (pneumococcal)", "पीसीवी-2 (न्यूमोकोकल)", PCV_NOTE)]),
    *_vaccines("9–12 months", 9 * M, 12 * M, [
        ("mr1", "MR-1 (measles & rubella)", "एमआर-1 (खसरा और रूबेला)", ""), ("je1", "JE-1 (Japanese encephalitis)", "जेई-1", JE_NOTE),
        ("pcvb", "PCV booster", "पीसीवी बूस्टर", PCV_NOTE)]),
    *_vaccines("16–24 months", 16 * M, 24 * M, [
        ("mr2", "MR-2 (measles & rubella)", "एमआर-2 (खसरा और रूबेला)", ""), ("je2", "JE-2 (Japanese encephalitis)", "जेई-2", JE_NOTE),
        ("dptb1", "DPT booster-1", "डीपीटी बूस्टर-1", ""), ("opvb", "OPV booster (polio drops)", "ओपीवी बूस्टर", "")]),
    Item("dptb2", "5–6 years", "DPT booster-2", "डीपीटी बूस्टर-2", 5 * Y, 7 * Y),
    Item("td10", "10 years", "Td (tetanus & diphtheria)", "टीडी (टिटनेस और डिप्थीरिया)", 10 * Y, 11 * Y),
    Item("td16", "16 years", "Td (tetanus & diphtheria)", "टीडी (टिटनेस और डिप्थीरिया)", 16 * Y, 17 * Y),
]

PREGNANCY: list[Item] = [
    Item("anc1", "First trimester", "1st check-up (register pregnancy, MCP card)", "पहली जाँच (गर्भावस्था पंजीकरण, एमसीपी कार्ड)",
         0, 13 * W, "As early as possible, preferably in the first 3 months."),
    Item("td1", "First trimester", "Td-1 (tetanus & diphtheria)", "टीडी-1", 0, 13 * W,
         "At the first visit. A single booster instead, if you had Td in a pregnancy within the last 3 years."),
    Item("anc2", "Second trimester", "2nd check-up", "दूसरी जाँच", 14 * W, 27 * W),
    Item("td2", "Second trimester", "Td-2 (tetanus & diphtheria)", "टीडी-2", 4 * W, 27 * W, "At least 4 weeks after Td-1."),
    Item("usg2", "Second trimester", "Level II ultrasound", "लेवल II अल्ट्रासाउंड", 18 * W, 20 * W + 6,
         "Recommended (desirable) test, at 18–20 weeks."),
    Item("anc3", "Third trimester", "3rd check-up", "तीसरी जाँच", 28 * W, 34 * W + 6),
    Item("anc4", "Third trimester", "4th check-up", "चौथी जाँच", 36 * W, 40 * W + 6, "Plan where you will deliver; keep the MCP card ready."),
]


def status(item: Item, origin: date, today: date, done: dict[str, date], soon_days: int = 30) -> dict:
    start = origin + timedelta(days=round(item.start))
    end = origin + timedelta(days=round(item.end))
    if item.code == "td2" and "td1" in done:  # Td-2: at least four weeks after the actual Td-1
        start = max(start, done["td1"] + timedelta(days=28))
    if item.code in done:
        st = "done"
    elif today > end:
        st = "overdue"
    elif today >= start:
        st = "due"
    elif (start - today).days <= soon_days:
        st = "soon"
    else:
        st = "upcoming"
    return {"code": item.code, "group": item.group, "title": item.title, "title_hi": item.title_hi, "note": item.note,
            "due_from": start, "due_by": end, "status": st, "done_on": done.get(item.code)}


def child_schedule(dob: date, today: date, done: dict[str, date]) -> list[dict]:
    return [status(i, dob, today, done) for i in CHILD]


def pregnancy_schedule(lmp: date, today: date, done: dict[str, date]) -> list[dict]:
    return [status(i, lmp, today, done) for i in PREGNANCY]
