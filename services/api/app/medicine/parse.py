"""Parse Indian formulation names into a canonical composition.

Handles Jan Aushadhi / IP-style names, e.g.
  "Ibuprofen 400mg and Paracetamol 325mg Tablets IP"
  "Etoricoxib Tablets IP 120 mg"
  "Aspirin Gastro-resistant Tablets IP 150 mg"
  "Metformin Hydrochloride Sustained Release Tablets IP 500 mg"
  "Diclofenac Gel IP 1.16%w/w (Diclofenac Diethylamine)"
  "Amoxycillin 500mg + Clavulanic Acid 125mg Tablets"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.medicine.inn import canonical_generic

FORMS = [
    ("oral suspension", "SUSP"), ("suspension", "SUSP"), ("syrup", "SYP"), ("oral solution", "SOL"),
    ("dry syrup", "DRY_SYP"), ("oral drops", "DROPS"), ("eye drops", "EYE_DROPS"), ("ear drops", "EAR_DROPS"),
    ("eye ointment", "EYE_OINT"), ("nasal spray", "NASAL_SPRAY"), ("inhaler", "INHALER"),
    ("rotacap", "ROTACAP"), ("respule", "RESPULE"), ("inhalation", "INHALER"), ("injection", "INJ"),
    ("infusion", "INJ"), ("vaccine", "VACCINE"), ("mouthwash", "MOUTHWASH"), ("mouth wash", "MOUTHWASH"),
    ("granules", "GRANULES"), ("tabelts", "TAB"), ("tablest", "TAB"),
    ("capsules", "CAP"), ("capsule", "CAP"), ("tablets", "TAB"), ("tablet", "TAB"), ("chewable", "TAB"),
    ("ointment", "OINT"), ("cream", "CREAM"), ("gel", "GEL"), ("lotion", "LOTION"), ("powder", "POWDER"),
    ("sachet", "SACHET"), ("suppositor", "SUPP"), ("drops", "DROPS"), ("solution", "SOL"), ("spray", "SPRAY"),
    ("patch", "PATCH"), ("soap", "SOAP"), ("shampoo", "SHAMPOO"),
]
# Abbreviations as written on Indian prescriptions / strips
FORM_ABBR = {"tab": "TAB", "tabs": "TAB", "cap": "CAP", "caps": "CAP", "syp": "SYP", "syr": "SYP",
             "inj": "INJ", "oint": "OINT", "susp": "SUSP", "gtt": "DROPS", "drops": "DROPS", "crm": "CREAM"}

RELEASE = [
    (r"\b(sustained|extended|prolonged|controlled)[ -]release\b|\b(sr|er|xr|cr|xl|pr)\b", "SR"),
    (r"\bmodified[ -]release\b|\bmr\b", "MR"),
    (r"\b(gastro[ -]?resistant|enteric[ -]coated|delayed[ -]release)\b|\b(dr|ec)\b", "DR"),
    (r"\bdispersible\b|\bdt\b", "DT"),
    (r"\b(orally disintegrating|mouth dissolving)\b|\b(odt|md)\b", "ODT"),
]

_UNIT = r"(mg|mcg|µg|g|gm|iu|i\.u\.|units?|ml|%\s?w/w|%\s?w/v|%\s?v/v|%)"
_STRENGTH = re.compile(
    rf"(\d+(?:\.\d+)?)\s*{_UNIT}(?:\s*(?:/|\bper\b|\bin\b)\s*(\d+(?:\.\d+)?)?\s*(ml|g|gm|dose|puff)\b)?", re.I)
_SALTS = re.compile(
    r"\b(hydrochloride|hcl|hydrobromide|sodium|potassium|calcium|magnesium|maleate|mesylate|mesilate|besylate|"
    r"besilate|succinate|tartrate|citrate|sulphate|sulfate|phosphate|acetate|fumarate|bromide|dihydrate|"
    r"monohydrate|trihydrate|anhydrous|propionate|dipropionate|furoate|valerate|diethylamine|"
    r"tromethamine|disodium|bitartrate|gluconate|lactate|nitrate|oxalate|pamoate|stearate|ip|bp|usp)\b", re.I)
_NOISE = re.compile(r"\b(tablets?|capsules?|ip|bp|usp|film[- ]coated|uncoated|oral|for|and|with|each|"
                    r"contains?|pack|of|strip|bottle|vial|ampoule|kit|combi|new)\b", re.I)


@dataclass
class Ingredient:
    generic: str
    strength: float | None = None
    unit: str | None = None
    per: str | None = None  # e.g. "5ml" for 250 mg/5 ml

    def key(self) -> str:
        if self.strength is None:
            return self.generic
        s = f"{self.strength:g}{self.unit}"
        return f"{self.generic}:{s}/{self.per}" if self.per else f"{self.generic}:{s}"


@dataclass
class Composition:
    ingredients: list[Ingredient] = field(default_factory=list)
    form: str | None = None
    release: str = "IR"

    @property
    def key(self) -> str:
        return "+".join(sorted(i.key() for i in self.ingredients))

    @property
    def generics_key(self) -> str:
        return "+".join(sorted({i.generic for i in self.ingredients}))

    def as_list(self) -> list[dict]:
        return [{"generic": i.generic, "strength": i.strength, "unit": i.unit, "per": i.per}
                for i in self.ingredients]


def _norm_unit(u: str) -> str:
    u = u.lower().replace(" ", "").replace("i.u.", "iu").replace("µg", "mcg").replace("gm", "g")
    if u.startswith("unit"):
        return "iu"
    return u


def _to_mg(value: float, unit: str) -> tuple[float, str]:
    if unit == "g":
        return value * 1000, "mg"
    if unit == "mcg" and value >= 1000:
        return value / 1000, "mg"
    return value, unit


def detect_form(text: str) -> str | None:
    t = text.lower()
    for word, code in FORMS:
        if word in t:
            return code
    m = re.match(r"^\s*(tab|tabs|cap|caps|syp|syr|inj|oint|susp|gtt|drops|crm)\.?\s", t)
    return FORM_ABBR.get(m.group(1)) if m else None


def detect_release(text: str) -> str:
    t = text.lower()
    for pat, code in RELEASE:
        if re.search(pat, t):
            return code
    return "IR"


def _clean(name: str, strip_salts: bool) -> str:
    if strip_salts:
        name = _SALTS.sub(" ", name)
    name = _NOISE.sub(" ", name)
    for word, _ in FORMS:
        name = re.sub(rf"\b{word}\b", " ", name, flags=re.I)
    for pat, _ in RELEASE:
        name = re.sub(pat, " ", name, flags=re.I)
    name = re.sub(r"[^a-zA-Z\- ]", " ", name)
    name = re.sub(r"\b(ip|bp|usp)\b", " ", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip(" -").lower()


def clean_generic(name: str) -> str:
    name = re.sub(r"\(.*?\)", " ", name)
    # When the salt IS the drug ("Calcium Acetate", "Potassium Citrate"), stripping salts leaves nothing — keep it.
    out = _clean(name, True) or _clean(name, False)
    return canonical_generic(out) if out else ""


def parse_composition(text: str) -> Composition:
    """Parse a formulation/brand-label string into ingredients + form + release type."""
    raw = re.sub(r"\s+", " ", text.replace(" ", " ")).strip()
    comp = Composition(form=detect_form(raw), release=detect_release(raw))
    work = raw
    sep = r"\s*\+\s*|\s+and\s+|\s*&\s*|,\s*"
    # 1) "Lopinavir and Ritonavir Tablets IP (200mg + 50mg)": a bracket holding only strengths, one per name
    m = re.search(r"\(([^()]*)\)", work)
    if m:
        items = [x for x in re.split(sep, m.group(1)) if x.strip()]
        outer = re.sub(r"\(.*?\)", " ", work)
        names = [x for x in re.split(sep, outer, flags=re.I) if clean_generic(_STRENGTH.sub(" ", x))]
        if len(items) > 1 and all(_STRENGTH.fullmatch(x.strip()) for x in items) and len(names) == len(items):
            work = " + ".join(f"{n.strip()} {s.strip()}" for n, s in zip(names, items))
    # 2) "Atorvastatin (10mg) + Aspirin (75mg)", "Ketorolac (0.5% W/V)": unwrap brackets that are just a strength
    #    (but not a pack volume like "(20ml)")
    work = re.sub(r"\(\s*((?:\d+(?:\.\d+)?)\s*(?!ml\b)[^()]*?)\s*\)",
                  lambda x: f" {x.group(1)} " if _STRENGTH.fullmatch(x.group(1).strip()) else x.group(0), work,
                  flags=re.I)
    body = re.sub(r"\(.*?\)", " ", work)
    # 3) "Co-trimoxazole (Sulphamethoxazole 800mg and Trimethoprim 160mg) Tablets" — strengths live in brackets
    bracket = " ".join(re.findall(r"\((.*?)\)", work))
    if not _STRENGTH.search(body) and _STRENGTH.search(bracket) and clean_generic(_STRENGTH.sub(" ", bracket)):
        body = bracket
    # Split on combination separators: "+", " and ", " & ", " with ", ","
    parts = [p for p in re.split(r"\s*\+\s*|\s+and\s+|\s*&\s*|\s+with\s+|,\s*", body, flags=re.I) if p.strip()]

    pending_names: list[str] = []
    for part in parts:
        strengths = list(_STRENGTH.finditer(part))
        name = clean_generic(_STRENGTH.sub(" ", part))
        if not strengths:
            if name:
                pending_names.append(name)
            continue
        m = strengths[0]
        val, unit = _to_mg(float(m.group(1)), _norm_unit(m.group(2)))
        per = f"{float(m.group(3) or 1):g}{m.group(4).lower().replace('gm', 'g')}" if m.group(4) else None
        if name:
            comp.ingredients.append(Ingredient(name, val, unit, per))
        elif pending_names:  # "Etoricoxib Tablets IP 120 mg" -> name came in an earlier fragment
            comp.ingredients.append(Ingredient(pending_names.pop(0), val, unit, per))
    for n in pending_names:
        if n and all(n != i.generic for i in comp.ingredients):
            comp.ingredients.append(Ingredient(n))
    # de-duplicate (same generic listed twice, e.g. salt in brackets)
    seen: dict[str, Ingredient] = {}
    for ing in comp.ingredients:
        if ing.generic not in seen or (seen[ing.generic].strength is None and ing.strength is not None):
            seen[ing.generic] = ing
    comp.ingredients = list(seen.values())
    return comp


_PACK = re.compile(r"(\d+(?:\.\d+)?)\s*(?:'s|s\b|tabs?|caps?|x)|(\d+(?:\.\d+)?)\s*(ml|g|gm)\b", re.I)


def parse_pack(unit_size: str) -> tuple[float | None, str | None]:
    """'10's' -> (10,'units'); '60 ml' -> (60,'ml'); '15 g' -> (15,'g'); '1 Vial' -> (1,'units')."""
    if not unit_size:
        return None, None
    m = _PACK.search(unit_size)
    if m:
        if m.group(1):
            return float(m.group(1)), "units"
        return float(m.group(2)), m.group(3).lower().replace("gm", "g")
    m = re.search(r"(\d+(?:\.\d+)?)", unit_size)
    return (float(m.group(1)), "units") if m else (None, None)
