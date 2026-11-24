"""Lab-report line parsing + LOINC mapping for common Indian lab panels (CBC, LFT, KFT/RFT, lipid, thyroid,
diabetes, vitamins, iron studies, electrolytes, inflammation).

The VLM transcribes the report as plain text, one test per line; this module turns lines like
  "Haemoglobin   10.2   g/dL   12.0 - 15.0"   or   "HbA1c : 7.8 % (4.0-5.6)"
into structured results and flags them against the reference range printed on the report itself.
LOINC codes are from loinc.org (free licence).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (regex for the test name as printed on Indian reports, LOINC code, canonical display)
LOINC: list[tuple[str, str, str]] = [
    (r"h(a)?emoglobin|\bhb\b|\bhgb\b", "718-7", "Hemoglobin"),
    (r"total (wbc|leu[ck]ocyte)|\btlc\b|\bwbc\b|white blood cell", "6690-2", "Leukocytes"),
    (r"platelet|\bplt\b", "777-3", "Platelets"),
    (r"\brbc\b|red blood cell|erythrocyte count", "789-8", "Erythrocytes"),
    (r"\bpcv\b|h(a)?ematocrit|\bhct\b", "4544-3", "Hematocrit"),
    (r"\bmcv\b", "787-2", "MCV"),
    (r"\bmch\b(?!c)", "785-6", "MCH"),
    (r"\bmchc\b", "786-4", "MCHC"),
    (r"absolute neutrophil|\banc\b", "751-8", "Neutrophils (absolute)"),
    (r"absolute lymphocyte|\balc\b", "731-0", "Lymphocytes (absolute)"),
    (r"absolute monocyte|\bamc\b", "742-7", "Monocytes (absolute)"),
    (r"absolute eosinophil|\baec\b", "711-2", "Eosinophils (absolute)"),
    (r"neutrophil|polymorph", "770-8", "Neutrophils %"),
    (r"lymphocyte", "736-9", "Lymphocytes %"),
    (r"eosinophil", "713-8", "Eosinophils %"),
    (r"monocyte", "5905-5", "Monocytes %"),
    (r"basophil", "706-2", "Basophils %"),
    (r"\brdw\b|red (cell|blood cell) distribution", "788-0", "RDW"),
    (r"\bmpv\b|mean platelet volume", "32623-1", "Mean platelet volume"),
    (r"\besr\b|erythrocyte sedimentation", "4537-7", "ESR"),
    (r"fasting (blood |plasma )?(glucose|sugar)|\bfbs\b|\bfpg\b", "1558-6", "Fasting glucose"),
    (r"(post ?prandial|\bpp\b|\bppbs\b).*(glucose|sugar)|\bppbs\b", "1521-4", "Glucose 2h post meal"),
    (r"random (blood )?(glucose|sugar)|\brbs\b", "2345-7", "Glucose"),
    (r"hba1c|glycated h(a)?emoglobin|glycosylated", "4548-4", "HbA1c"),
    (r"(serum )?creatinine", "2160-0", "Creatinine"),
    (r"\bbun\b|blood urea nitrogen", "3094-0", "Urea nitrogen"),
    (r"(blood |serum )?urea\b", "3091-6", "Urea"),
    (r"uric acid", "3084-1", "Uric acid"),
    (r"\begfr\b|estimated gfr", "62238-1", "eGFR (CKD-EPI)"),
    (r"sodium|\bna\+?\b", "2951-2", "Sodium"),
    (r"potassium|\bk\+?\b", "2823-3", "Potassium"),
    (r"chloride|\bcl-?\b", "2075-0", "Chloride"),
    (r"calcium", "17861-6", "Calcium"),
    (r"total bilirubin|bilirubin,? total|bilirubin total", "1975-2", "Bilirubin total"),
    (r"direct bilirubin|bilirubin,? direct|conjugated", "1968-7", "Bilirubin direct"),
    (r"\bsgpt\b|\balt\b|alanine", "1742-6", "ALT"),
    (r"\bsgot\b|\bast\b|aspartate", "1920-8", "AST"),
    (r"alkaline phosphatase|\balp\b", "6768-6", "Alkaline phosphatase"),
    (r"\bggt\b|gamma", "2324-2", "GGT"),
    (r"albumin", "1751-7", "Albumin"),
    (r"total protein", "2885-2", "Total protein"),
    (r"total cholesterol|cholesterol,? total|^cholesterol", "2093-3", "Cholesterol"),
    (r"\bhdl\b", "2085-9", "HDL cholesterol"),
    (r"\bldl\b", "13457-7", "LDL cholesterol (calc)"),
    (r"triglyceride", "2571-8", "Triglycerides"),
    (r"\btsh\b|thyroid stimulating", "3016-3", "TSH"),
    (r"free t4|\bft4\b", "3024-7", "Free T4"),
    (r"free t3|\bft3\b", "3051-0", "Free T3"),
    (r"\bt4\b|thyroxine", "3026-2", "T4"),
    (r"\bt3\b|triiodothyronine", "3053-6", "T3"),
    (r"vitamin d|25.?oh|25.?hydroxy", "1989-3", "Vitamin D 25-OH"),
    (r"vitamin b.?12|cobalamin", "2132-9", "Vitamin B12"),
    (r"ferritin", "2276-4", "Ferritin"),
    (r"serum iron|\biron\b", "2498-4", "Iron"),
    (r"\btibc\b", "2500-7", "TIBC"),
    (r"\bcrp\b|c.reactive", "1988-5", "CRP"),
    (r"\bpsa\b", "2857-1", "PSA"),
]
_COMPILED = [(re.compile(p, re.I), c, d) for p, c, d in LOINC]

_NUM = r"[<>]?\s*\d+(?:[.,]\d+)?"
_LINE = re.compile(
    rf"^(?P<name>[A-Za-z][A-Za-z0-9 ()/.,%+\-]*?)\s*[:\-]?\s+(?P<value>{_NUM})\s*(?P<flag>[HL]\b|\*)?\s*"
    rf"(?P<unit>[a-zA-Zµμ%/^0-9.\[\]]+(?:/[a-zA-Zµμ0-9.^]+)?)?\s*(?:\b[HLN]\b\s*)?"  # optional flag letter
    rf"(?:[\[(]?\s*(?P<lo>{_NUM})?\s*(?:-|–|to)\s*(?P<hi>{_NUM})?\s*[\])]?)?",
    re.I)
_LT = re.compile(rf"(?:<|less than|upto|up to)\s*({_NUM})", re.I)
_GT = re.compile(rf"(?:>|more than|above)\s*({_NUM})", re.I)


@dataclass
class LabLine:
    test_name: str
    loinc_code: str | None
    display: str
    value: float | None
    value_text: str
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    flag: str | None
    range_source: str | None = None  # "report" (printed on it) | "default" (REFERENCE_RANGES) | None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _f(x: str | None) -> float | None:
    if not x:
        return None
    try:
        return float(x.replace(",", ".").replace("<", "").replace(">", "").strip())
    except ValueError:
        return None


def loinc_for(name: str) -> tuple[str | None, str]:
    # "M C V", "M.C.H.C." -> "MCV", "MCHC" so abbreviations match
    squashed = re.sub(r"\b([A-Za-z])[.\s]+(?=[A-Za-z]\b)", r"\1", name)
    squashed = re.sub(r"(?<=\b[A-Z])\.(?=\s|$)", "", squashed)
    for pat, code, display in _COMPILED:
        if pat.search(squashed) or pat.search(name):
            return code, display
    return None, name.strip()


def parse_lab_line(line: str) -> LabLine | None:
    line = re.sub(r"\s+", " ", line.replace("\t", " ")).strip(" |")
    # "9.10 [L] g/dl", "87.7 (H) %": a bracketed flag after the value, not the unit
    line = re.sub(r"(\d)\s*[\[(]\s*([HLhl])\s*[\])]", r"\1 \2", line)
    m = _LINE.match(line)
    if not m:
        return None
    name = m.group("name").strip(" :-")
    if len(re.sub(r"[^A-Za-z]", "", name)) < 2:
        return None
    value = _f(m.group("value"))
    lo, hi = _f(m.group("lo")), _f(m.group("hi"))
    if lo is None and hi is None:  # "< 5.7" or "> 40" style reference
        rest = line[m.end("value"):]
        if (lt := _LT.search(rest)):
            hi = _f(lt.group(1))
        elif (gt := _GT.search(rest)):
            lo = _f(gt.group(1))
    flag = None
    if value is not None and (lo is not None or hi is not None):
        flag = "L" if lo is not None and value < lo else "H" if hi is not None and value > hi else "N"
    elif (m.group("flag") or "").upper() in ("H", "L"):
        flag = m.group("flag").upper()
    code, display = loinc_for(name)
    unit = (m.group("unit") or "").strip() or None
    if unit and re.fullmatch(r"[HL]", unit):
        unit = None
    return LabLine(name, code, display, value, m.group("value").strip(), unit, lo, hi, flag)


# Fallback reference intervals for reports that print none. LOINC -> (unit, {group: (low, high)}).
# Groups: "M"/"F" adult male/female, "child" (1-12 y), "preg" (pregnancy). None = no bound.
# Sources: AIIMS/ICMR lab manuals and common Indian (NABL) lab reference intervals; RSSDI / ICMR diabetes
# cut-offs; WHO haemoglobin thresholds for anaemia (pregnancy < 11 g/dL); trimester TSH per Indian Thyroid
# Society. Labs vary by method, so these are used only when the report prints no range.
REFERENCE_RANGES: dict[str, tuple[str, dict[str, tuple[float | None, float | None]]]] = {
    "718-7": ("g/dL", {"M": (13.0, 17.0), "F": (12.0, 15.0), "child": (11.5, 15.5), "preg": (11.0, None)}),
    "6690-2": ("10^3/uL", {"M": (4.0, 11.0), "F": (4.0, 11.0), "child": (4.5, 13.5), "preg": (5.7, 13.6)}),
    "777-3": ("10^3/uL", {"M": (150, 410), "F": (150, 410), "child": (150, 450), "preg": (150, 410)}),
    "789-8": ("10^6/uL", {"M": (4.5, 5.5), "F": (3.8, 4.8), "child": (4.0, 5.2)}),
    "4544-3": ("%", {"M": (40, 50), "F": (36, 46), "child": (35, 45), "preg": (33, 44)}),
    "787-2": ("fL", {"M": (83, 101), "F": (83, 101), "child": (77, 95)}),
    "785-6": ("pg", {"M": (27, 32), "F": (27, 32), "child": (25, 33)}),
    "786-4": ("g/dL", {"M": (31.5, 34.5), "F": (31.5, 34.5), "child": (31, 37)}),
    "788-0": ("%", {"M": (11.6, 14.0), "F": (11.6, 14.0)}),
    "770-8": ("%", {"M": (40, 80), "F": (40, 80)}),
    "736-9": ("%", {"M": (20, 40), "F": (20, 40)}),
    "713-8": ("%", {"M": (1, 6), "F": (1, 6), "child": (1, 6)}),
    "5905-5": ("%", {"M": (2, 10), "F": (2, 10)}),
    "706-2": ("%", {"M": (0, 2), "F": (0, 2), "child": (0, 2)}),
    "4537-7": ("mm/h", {"M": (0, 15), "F": (0, 20), "child": (0, 10)}),
    "1558-6": ("mg/dL", {"M": (70, 99), "F": (70, 99), "child": (70, 99), "preg": (None, 92)}),
    "1521-4": ("mg/dL", {"M": (None, 140), "F": (None, 140), "preg": (None, 153)}),
    "2345-7": ("mg/dL", {"M": (70, 140), "F": (70, 140), "child": (70, 140)}),
    "4548-4": ("%", {"M": (4.0, 5.6), "F": (4.0, 5.6), "child": (4.0, 5.6), "preg": (None, 6.0)}),
    "2160-0": ("mg/dL", {"M": (0.7, 1.3), "F": (0.6, 1.1), "child": (0.3, 0.7), "preg": (0.4, 0.9)}),
    "3094-0": ("mg/dL", {"M": (7, 20), "F": (7, 20), "child": (5, 18)}),
    "3091-6": ("mg/dL", {"M": (15, 40), "F": (15, 40), "child": (10, 38)}),
    "3084-1": ("mg/dL", {"M": (3.5, 7.2), "F": (2.6, 6.0), "child": (2.0, 5.5)}),
    "62238-1": ("mL/min/1.73m2", {"M": (90, None), "F": (90, None)}),
    "2951-2": ("mmol/L", {"M": (135, 145), "F": (135, 145), "child": (135, 145), "preg": (133, 145)}),
    "2823-3": ("mmol/L", {"M": (3.5, 5.1), "F": (3.5, 5.1), "child": (3.5, 5.1)}),
    "2075-0": ("mmol/L", {"M": (98, 107), "F": (98, 107), "child": (98, 107)}),
    "17861-6": ("mg/dL", {"M": (8.6, 10.2), "F": (8.6, 10.2), "child": (8.8, 10.8)}),
    "1975-2": ("mg/dL", {"M": (0.2, 1.2), "F": (0.2, 1.2), "child": (0.2, 1.0)}),
    "1968-7": ("mg/dL", {"M": (0.0, 0.3), "F": (0.0, 0.3), "child": (0.0, 0.3)}),
    "1742-6": ("U/L", {"M": (None, 45), "F": (None, 35), "child": (None, 40)}),
    "1920-8": ("U/L", {"M": (None, 40), "F": (None, 35), "child": (None, 50)}),
    "6768-6": ("U/L", {"M": (40, 130), "F": (35, 105), "child": (100, 400), "preg": (35, 230)}),
    "2324-2": ("U/L", {"M": (None, 60), "F": (None, 40)}),
    "1751-7": ("g/dL", {"M": (3.5, 5.2), "F": (3.5, 5.2), "child": (3.5, 5.5), "preg": (2.8, 4.5)}),
    "2885-2": ("g/dL", {"M": (6.4, 8.3), "F": (6.4, 8.3), "child": (6.0, 8.0)}),
    "2093-3": ("mg/dL", {"M": (None, 200), "F": (None, 200), "child": (None, 170)}),
    "2085-9": ("mg/dL", {"M": (40, None), "F": (50, None), "child": (45, None)}),
    "13457-7": ("mg/dL", {"M": (None, 100), "F": (None, 100), "child": (None, 110)}),
    "2571-8": ("mg/dL", {"M": (None, 150), "F": (None, 150), "child": (None, 90)}),
    "3016-3": ("uIU/mL", {"M": (0.4, 4.5), "F": (0.4, 4.5), "child": (0.7, 5.7), "preg": (0.1, 3.0)}),
    "3024-7": ("ng/dL", {"M": (0.8, 1.8), "F": (0.8, 1.8), "child": (0.9, 1.8)}),
    "3051-0": ("pg/mL", {"M": (2.3, 4.2), "F": (2.3, 4.2), "child": (2.4, 5.0)}),
    "3026-2": ("ug/dL", {"M": (5.0, 12.0), "F": (5.0, 12.0), "child": (6.0, 13.0)}),
    "3053-6": ("ng/dL", {"M": (80, 200), "F": (80, 200), "child": (100, 220)}),
    "1989-3": ("ng/mL", {"M": (30, 100), "F": (30, 100), "child": (30, 100), "preg": (30, 100)}),
    "2132-9": ("pg/mL", {"M": (200, 900), "F": (200, 900), "child": (200, 900)}),
    "2276-4": ("ng/mL", {"M": (30, 400), "F": (15, 150), "child": (15, 150), "preg": (15, None)}),
    "2498-4": ("ug/dL", {"M": (65, 175), "F": (50, 170), "child": (50, 120)}),
    "2500-7": ("ug/dL", {"M": (250, 450), "F": (250, 450)}),
    "1988-5": ("mg/L", {"M": (None, 5), "F": (None, 5), "child": (None, 5)}),
    "2857-1": ("ng/mL", {"M": (None, 4.0)}),
}


def _u(x: str) -> str:
    return x.lower().replace("µ", "u").replace("μ", "u").replace(" ", "")


def default_range(loinc: str | None, *, sex: str | None = None, age_years: float | None = None,
                  pregnant: bool = False) -> tuple[float | None, float | None] | None:
    """Interval to use when the report prints none. Unknown sex -> union of M and F (flag only if outside
    both). No default under 1 year: infant ranges differ too much."""
    if not loinc or loinc not in REFERENCE_RANGES:
        return None
    groups = REFERENCE_RANGES[loinc][1]
    if age_years is not None and age_years < 1:
        return None
    if pregnant and "preg" in groups:
        return groups["preg"]
    if age_years is not None and age_years < 12:
        return groups.get("child")
    s = (sex or "")[:1].upper()
    if s in groups:
        return groups[s]
    m, f = groups.get("M"), groups.get("F")
    if not (m and f):
        return m or f
    lo = None if m[0] is None or f[0] is None else min(m[0], f[0])
    hi = None if m[1] is None or f[1] is None else max(m[1], f[1])
    return lo, hi


def parse_lab_report(text: str, *, sex: str | None = None, age_years: float | None = None,
                     pregnant: bool = False) -> list[LabLine]:
    out, seen = [], set()
    for raw in (text or "").splitlines():
        r = parse_lab_line(raw.replace("TEST:", "").strip())
        if r and (r.ref_low is not None or r.ref_high is not None):
            r.range_source = "report"
        elif r and r.value is not None:
            rng = default_range(r.loinc_code, sex=sex, age_years=age_years, pregnant=pregnant)
            unit_ok = not r.unit or _u(REFERENCE_RANGES[r.loinc_code][0]) == _u(r.unit) if rng else False
            if rng and unit_ok:
                r.ref_low, r.ref_high, r.range_source = rng[0], rng[1], "default"
                r.flag = ("L" if rng[0] is not None and r.value < rng[0]
                          else "H" if rng[1] is not None and r.value > rng[1] else "N")
        if r and (r.loinc_code or r.ref_low is not None or r.ref_high is not None) and r.test_name.lower() not in seen:
            seen.add(r.test_name.lower())
            out.append(r)
    return out


LAB_EDUCATION: dict[str, dict[str, str]] = {
    "718-7": {
        "name": "Haemoglobin (Hb)",
        "measures": "Oxygen-carrying protein in red blood cells.",
        "low": "May indicate anaemia (iron, B12, or folate deficiency, or blood loss), causing tiredness or breathlessness.",
        "high": "Can occur with dehydration, smoking, or high-altitude adaptation.",
    },
    "6690-2": {
        "name": "WBC (Total Leukocyte Count)",
        "measures": "White blood cells that fight infection and inflammation.",
        "low": "Can be seen in some viral infections, bone marrow suppression, or medication effects.",
        "high": "Often reflects infection, inflammation, or physiological stress.",
    },
    "777-3": {
        "name": "Platelets",
        "measures": "Blood cells that help clotting and prevent bleeding.",
        "low": "Needs attention in fever (e.g. dengue/viral) or bleeding risk; very low counts need urgent review.",
        "high": "Often reactive to inflammation or iron deficiency.",
    },
    "1558-6": {
        "name": "Fasting Blood Glucose",
        "measures": "Blood sugar after 8+ hours of fasting.",
        "low": "Hypoglycaemia (<70 mg/dL) can cause shakiness, sweating, or dizziness.",
        "high": "100–125 mg/dL suggests prediabetes; >=126 mg/dL on repeat testing suggests diabetes.",
    },
    "4548-4": {
        "name": "HbA1c",
        "measures": "Average blood glucose over the past 2–3 months.",
        "low": "Rarely low unless anaemia or red-cell turnover affects the reading.",
        "high": "5.7–6.4% indicates prediabetes; >=6.5% indicates diabetes range.",
    },
    "3016-3": {
        "name": "TSH (Thyroid Stimulating Hormone)",
        "measures": "Pituitary signal regulating thyroid hormone production.",
        "low": "Often points toward an overactive thyroid (hyperthyroidism) or excess thyroid dose.",
        "high": "Often points toward an underactive thyroid (hypothyroidism).",
    },
    "2160-0": {
        "name": "Serum Creatinine",
        "measures": "Kidney filtration marker.",
        "low": "Usually reflects lower muscle mass and is rarely a concern.",
        "high": "May reflect dehydration, kidney strain, or reduced kidney filtration.",
    },
    "1742-6": {
        "name": "ALT / SGPT",
        "measures": "Liver enzyme sensitive to liver cell irritation.",
        "low": "Not clinically concerning.",
        "high": "Can rise with fatty liver, viral hepatitis, alcohol, or certain medicines.",
    },
    "2093-3": {
        "name": "Total Cholesterol",
        "measures": "Combined blood cholesterol (LDL + HDL + VLDL).",
        "low": "Usually not harmful unless severely low.",
        "high": "Elevated levels (>200 mg/dL) increase long-term cardiovascular risk.",
    },
    "13457-7": {
        "name": "LDL Cholesterol",
        "measures": "Low-density lipoprotein ('bad' cholesterol).",
        "low": "Generally protective.",
        "high": "Higher values (>100 mg/dL, or lower targets in diabetes/heart disease) raise plaque risk.",
    },
    "2571-8": {
        "name": "Triglycerides",
        "measures": "Blood fats influenced by diet, sugars, and metabolic health.",
        "low": "Usually not concerning.",
        "high": "Above 150 mg/dL increases metabolic and heart risk; very high levels (>500) risk pancreatitis.",
    },
    "1989-3": {
        "name": "Vitamin D (25-OH)",
        "measures": "Vitamin D stores supporting bone, muscle, and immune health.",
        "low": "Deficiency (<20 ng/mL) or insufficiency (20–29 ng/mL) is very common and can cause bone/muscle ache.",
        "high": "Excess (>100 ng/mL) usually comes from high-dose supplementation.",
    },
    "2132-9": {
        "name": "Vitamin B12",
        "measures": "Vitamin needed for nerve function and red blood cell production.",
        "low": "Can cause fatigue, tingling/numbness, memory fog, or anaemia (common in vegetarian diets or metformin use).",
        "high": "Usually due to supplements, occasionally liver/blood conditions.",
    },
}


def educate_lab_mentions(text: str, *, sex: str | None = None, age_years: float | None = None) -> str:
    """Build educational reference-range context for any lab tests or values mentioned in user text."""
    t = (text or "").strip()
    if not t:
        return ""
    notes: list[str] = []
    seen: set[str] = set()

    for row in parse_lab_report(t, sex=sex, age_years=age_years):
        loinc = row.loinc_code
        if not loinc or loinc in seen:
            continue
        seen.add(loinc)
        edu = LAB_EDUCATION.get(loinc)
        rng = (row.ref_low, row.ref_high) if (row.ref_low is not None or row.ref_high is not None) else default_range(loinc, sex=sex, age_years=age_years)
        unit = row.unit or (REFERENCE_RANGES.get(loinc, ("", {}))[0])
        val = row.value
        status = "Normal"
        if row.flag == "L" or (rng and val is not None and rng[0] is not None and val < rng[0]):
            status = "Low (below normal range)"
        elif row.flag == "H" or (rng and val is not None and rng[1] is not None and val > rng[1]):
            status = "High (above normal range)"
        rng_str = f"{rng[0] if rng and rng[0] is not None else '—'} to {rng[1] if rng and rng[1] is not None else '—'} {unit}".strip() if rng else unit
        line = f"- {row.test_name}: reported {val} {unit} [{status}; typical normal range: {rng_str}]."
        if edu:
            line += f" {edu['measures']} Low: {edu['low']} High: {edu['high']}"
        notes.append(line)

    low_t = t.lower()
    for loinc, edu in LAB_EDUCATION.items():
        if loinc in seen:
            continue
        key_words = [w.strip("()") for w in edu["name"].lower().split() if len(w.strip("()")) >= 3]
        if any(re.search(rf"\b{re.escape(kw)}\b", low_t) for kw in key_words):
            rng = default_range(loinc, sex=sex, age_years=age_years)
            unit = REFERENCE_RANGES.get(loinc, ("", {}))[0]
            rng_str = f"{rng[0] if rng and rng[0] is not None else '—'} to {rng[1] if rng and rng[1] is not None else '—'} {unit}".strip() if rng else unit
            notes.append(
                f"- {edu['name']} (typical normal range: {rng_str}): {edu['measures']} "
                f"If low: {edu['low']} If high: {edu['high']}"
            )
            seen.add(loinc)
            if len(notes) >= 4:
                break

    if not notes:
        return ""
    return "LAB REFERENCE RANGES & PATIENT EDUCATION:\n" + "\n".join(notes[:5])
