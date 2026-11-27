"""MedicineKnowledgeService: Verified educational medicine lookup, brand/generic mapping & comparison."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.medicine import openfda
from app.medicine.inn import is_nti
from app.models import BannedFdc, Generic, Interaction, Product


@dataclass(frozen=True)
class CuratedMonograph:
    generic_name: str
    brand_names_india: tuple[str, ...]
    drug_class: str
    pharmacological_class: str
    indications: tuple[str, ...]
    mechanism_of_action: str
    dosage_forms: tuple[str, ...]
    route: str
    common_adverse_effects: tuple[str, ...]
    serious_adverse_effects: tuple[str, ...]
    contraindications: tuple[str, ...]
    drug_interactions: tuple[str, ...]
    food_interactions: str
    pregnancy_considerations: str
    breastfeeding_considerations: str
    storage: str
    monitoring: str
    overdose_info: str
    prescription_status_india: str
    warnings: tuple[str, ...]
    source: str = "CDSCO / NLEM India / openFDA Reference"
    last_verified: str = "2026-11-01"


_CURATED_DB: dict[str, CuratedMonograph] = {
    "paracetamol": CuratedMonograph(
        generic_name="Paracetamol (Acetaminophen)",
        brand_names_india=("Crocin", "Dolo 650", "Calpol", "P-500", "Jan Aushadhi Paracetamol"),
        drug_class="Analgesic & Antipyretic",
        pharmacological_class="Para-aminophenol derivative (central COX inhibition)",
        indications=("Fever reduction", "Mild-to-moderate headache, body ache, or toothache"),
        mechanism_of_action="Acts centrally in the brain to inhibit prostaglandin synthesis and regulate the hypothalamic heat-regulating center.",
        dosage_forms=("Tablet", "Oral suspension / Syrup", "Drops", "IV infusion (hospital)"),
        route="Oral (or Intravenous in hospital)",
        common_adverse_effects=("Generally well tolerated at recommended doses", "Mild nausea in rare cases"),
        serious_adverse_effects=("Severe liver injury (hepatotoxicity) with overdose or chronic alcohol use", "Rare severe skin reactions"),
        contraindications=("Severe active liver disease", "Known hypersensitivity to paracetamol"),
        drug_interactions=("Warfarin (excessive prolonged use may increase bleeding risk)", "Other paracetamol-containing combination cold/pain medicines (risk of accidental double-dosing)"),
        food_interactions="Can be taken with or without food; avoid alcohol as it raises liver toxicity risk.",
        pregnancy_considerations="Generally considered the preferred fever/pain option in pregnancy when clinically needed — confirm dose and duration with your obstetrician.",
        breastfeeding_considerations="Excreted in small amounts in breast milk; generally compatible at standard short-term doses after clinician advice.",
        storage="Store below 25–30°C in a dry place away from moisture and children.",
        monitoring="Check liver function if used long-term or in patients with liver disease.",
        overdose_info="Overdose can cause life-threatening liver failure even before symptoms appear. Seek emergency care (108 / 112) immediately.",
        prescription_status_india="OTC for standard low-strength oral forms (higher strengths/combinations require clinician guidance).",
        warnings=("Do not combine multiple fever/cold tablets that already contain paracetamol.", "Never give adult tablets to children."),
    ),
    "ibuprofen": CuratedMonograph(
        generic_name="Ibuprofen",
        brand_names_india=("Brufen", "Ibugesic", "Combiflam (with Paracetamol)", "Jan Aushadhi Ibuprofen"),
        drug_class="Painkiller / Anti-inflammatory (NSAID)",
        pharmacological_class="Non-steroidal anti-inflammatory drug (non-selective COX-1/COX-2 inhibitor)",
        indications=("Inflammatory pain", "Joint/muscle pain", "Fever (when clinically appropriate)"),
        mechanism_of_action="Inhibits cyclooxygenase (COX-1 and COX-2) enzymes, reducing pain- and fever-causing prostaglandins.",
        dosage_forms=("Tablet", "Oral suspension", "Topical gel"),
        route="Oral (or Topical)",
        common_adverse_effects=("Stomach irritation, acidity, heartburn", "Nausea"),
        serious_adverse_effects=("Stomach ulcer or GI bleeding", "Kidney injury (especially in dehydration)", "Avoid in suspected dengue due to bleeding risk"),
        contraindications=("Active peptic ulcer or GI bleeding", "Severe kidney or heart failure", "Suspected dengue fever", "NSAID/aspirin allergy"),
        drug_interactions=("Aspirin, warfarin/anticoagulants (bleeding risk)", "ACE inhibitors/ARBs/diuretics (kidney risk)", "Lithium, methotrexate"),
        food_interactions="Take with food or milk to reduce stomach upset; avoid alcohol.",
        pregnancy_considerations="Avoid especially in the third trimester (risk to fetal heart/kidneys); consult your obstetrician before use at any stage.",
        breastfeeding_considerations="Small amounts pass into breast milk; consult a doctor before use.",
        storage="Store in a cool, dry place below 30°C.",
        monitoring="Monitor kidney function, blood pressure, and stomach symptoms if used regularly.",
        overdose_info="Can cause severe abdominal pain, vomiting, drowsiness, or kidney injury. Call 108 / 112.",
        prescription_status_india="Schedule H / Pharmacist-guided depending on strength; avoid self-medication in dengue or kidney disease.",
        warnings=("Avoid in suspected dengue fever.", "Older adults (≥65) face higher GI bleeding and kidney risk (AGS Beers Criteria)."),
    ),
    "metformin": CuratedMonograph(
        generic_name="Metformin",
        brand_names_india=("Glycomet", "Gluconorm", "Obimet", "Jan Aushadhi Metformin"),
        drug_class="Antidiabetic",
        pharmacological_class="Biguanide",
        indications=("Type 2 diabetes mellitus blood sugar management",),
        mechanism_of_action="Decreases liver glucose production, reduces intestinal glucose absorption, and improves insulin sensitivity.",
        dosage_forms=("Immediate-release tablet", "Extended-release (SR/XR) tablet"),
        route="Oral",
        common_adverse_effects=("Nausea, loose motions, abdominal bloating (often improves when taken with meals)"),
        serious_adverse_effects=("Lactic acidosis (very rare, higher risk in severe kidney impairment or severe dehydration)", "Vitamin B12 deficiency with long-term use"),
        contraindications=("Severe kidney impairment (low eGFR)", "Acute metabolic acidosis", "Severe dehydration or hypoxia"),
        drug_interactions=("Iodinated contrast dyes (temporary clinician-directed pause around scans)", "Excess alcohol", "Drugs affecting kidney function"),
        food_interactions="Take with or immediately after meals to minimize stomach upset; avoid heavy alcohol.",
        pregnancy_considerations="Requires specialist obstetric/endocrinology supervision in pregnancy or gestational diabetes.",
        breastfeeding_considerations="Small amounts enter breast milk; consult your doctor.",
        storage="Store at room temperature away from moisture.",
        monitoring="Periodic HbA1c, fasting/PP glucose, kidney function (creatinine/eGFR), and Vitamin B12.",
        overdose_info="Can cause severe lactic acidosis or low blood sugar when combined with other agents; seek emergency care (108 / 112).",
        prescription_status_india="Schedule H (Prescription-only in India).",
        warnings=("Never stop or adjust diabetes medicine without consulting your doctor.",),
    ),
    "amlodipine": CuratedMonograph(
        generic_name="Amlodipine",
        brand_names_india=("Amlong", "Amlokind", "Stamlo", "Jan Aushadhi Amlodipine"),
        drug_class="Antihypertensive / Anti-anginal",
        pharmacological_class="Dihydropyridine Calcium Channel Blocker",
        indications=("High blood pressure (hypertension)", "Chronic stable angina"),
        mechanism_of_action="Relaxes vascular smooth muscle and dilates peripheral and coronary arteries, lowering blood pressure.",
        dosage_forms=("Tablet",),
        route="Oral",
        common_adverse_effects=("Ankle/foot swelling (edema)", "Flushing", "Mild dizziness or headache"),
        serious_adverse_effects=("Severe hypotension", "Worsening chest pain on initiation in rare obstructive coronary disease"),
        contraindications=("Severe hypotension", "Cardiogenic shock", "Known allergy to dihydropyridines"),
        drug_interactions=("Other blood-pressure-lowering drugs (additive BP drop)", "Strong CYP3A4 inhibitors", "Simvastatin (dose limit)"),
        food_interactions="May be taken with or without food; avoid excess alcohol which increases dizziness.",
        pregnancy_considerations="Use only under obstetrician guidance; other agents are often preferred first-line in pregnancy.",
        breastfeeding_considerations="Excreted in small amounts; discuss with your doctor.",
        storage="Store below 30°C protected from light and moisture.",
        monitoring="Regular blood pressure and heart rate checks.",
        overdose_info="Causes dangerous drop in blood pressure and rapid heart rate; call 108 / 112 immediately.",
        prescription_status_india="Schedule H (Prescription-only in India).",
        warnings=("Do not stop blood pressure tablets abruptly without medical advice.",),
    ),
    "azithromycin": CuratedMonograph(
        generic_name="Azithromycin",
        brand_names_india=("Azithral", "Azee", "Zathrin", "Jan Aushadhi Azithromycin"),
        drug_class="Antibiotic",
        pharmacological_class="Macrolide antibiotic",
        indications=("Bacterial respiratory, ENT, or skin infections when prescribed by a doctor"),
        mechanism_of_action="Binds to the 50S bacterial ribosomal subunit, inhibiting bacterial protein synthesis.",
        dosage_forms=("Tablet", "Oral suspension", "IV infusion"),
        route="Oral (or Intravenous)",
        common_adverse_effects=("Nausea, loose motions, abdominal cramps"),
        serious_adverse_effects=("Heart rhythm prolongation (QT prolongation)", "Severe allergic reaction", "Liver injury (rare)"),
        contraindications=("Prior jaundice/liver dysfunction with azithromycin", "Macrolide hypersensitivity"),
        drug_interactions=("QT-prolonging medicines (amiodarone, certain antipsychotics/fluoroquinolones)", "Warfarin", "Digoxin"),
        food_interactions="Tablets can generally be taken with or without food; antacids containing aluminium/magnesium may reduce absorption if taken together.",
        pregnancy_considerations="Use only when clearly prescribed by a doctor.",
        breastfeeding_considerations="Passes into breast milk in small amounts; consult your doctor.",
        storage="Store below 30°C; keep reconstituted suspension as directed on the label.",
        monitoring="Monitor for allergic rash, persistent diarrhea, or palpitations.",
        overdose_info="Severe nausea, vomiting, diarrhea, or temporary hearing changes; seek urgent medical care.",
        prescription_status_india="Schedule H1 (Strict Prescription-only antibiotic in India).",
        warnings=("Never self-medicate with antibiotics for viral colds or fever — misuse drives antibiotic resistance.",),
    ),
    "pantoprazole": CuratedMonograph(
        generic_name="Pantoprazole",
        brand_names_india=("Pan 40", "Pantocid", "Pantop", "Jan Aushadhi Pantoprazole"),
        drug_class="Antacid / Acid Suppressant",
        pharmacological_class="Proton Pump Inhibitor (PPI)",
        indications=("Acid reflux (GERD)", "Gastritis / peptic ulcer", "Clinician-prescribed gastroprotection"),
        mechanism_of_action="Irreversibly blocks the H+/K+ ATPase proton pump in stomach parietal cells, reducing acid secretion.",
        dosage_forms=("Gastro-resistant tablet", "Capsule", "IV injection"),
        route="Oral (typically before breakfast) or IV",
        common_adverse_effects=("Headache", "Mild nausea or diarrhea"),
        serious_adverse_effects=("Long-term unsupervised use linked to low magnesium, low Vitamin B12, bone fracture risk, or C. difficile diarrhea"),
        contraindications=("Hypersensitivity to pantoprazole or other PPIs"),
        drug_interactions=("Clopidogrel (discuss with cardiologist)", "Methotrexate", "Drugs requiring stomach acid for absorption (e.g., ketoconazole, iron)"),
        food_interactions="Usually taken 30–60 minutes before a meal as advised by a doctor.",
        pregnancy_considerations="Consult your obstetrician before using during pregnancy.",
        breastfeeding_considerations="Consult your doctor before use while breastfeeding.",
        storage="Store in a cool, dry place below 25–30°C.",
        monitoring="If used long-term (>8 weeks), review need and magnesium/B12 levels with your doctor.",
        overdose_info="Generally low acute toxicity, but seek medical evaluation if large amounts are ingested.",
        prescription_status_india="Schedule H (Prescription-only in India).",
        warnings=("Persistent upper abdominal pain, black stools, or trouble swallowing require prompt medical evaluation rather than masking with acid blockers.",),
    ),
    "cetirizine": CuratedMonograph(
        generic_name="Cetirizine",
        brand_names_india=("Cetzine", "Okacet", "Zyrtec", "Alerid", "Jan Aushadhi Cetirizine"),
        drug_class="Antihistamine",
        pharmacological_class="Second-generation H1-receptor antagonist",
        indications=("Allergic rhinitis (sneezing, runny nose)", "Urticaria (hives/itching)"),
        mechanism_of_action="Selectively blocks peripheral H1 histamine receptors, reducing allergic itching, sneezing, and hives.",
        dosage_forms=("Tablet", "Oral syrup"),
        route="Oral",
        common_adverse_effects=("Mild drowsiness", "Dry mouth", "Fatigue"),
        serious_adverse_effects=("Rare hypersensitivity", "Excessive sedation in kidney impairment or with alcohol"),
        contraindications=("Severe kidney failure (requires dose adjustment/clinician advice)", "Hypersensitivity to cetirizine or hydroxyzine"),
        drug_interactions=("Alcohol, sedatives, sleeping pills (increased drowsiness)"),
        food_interactions="Can be taken with or without food; avoid alcohol.",
        pregnancy_considerations="Consult your doctor before use in pregnancy.",
        breastfeeding_considerations="Excreted in breast milk; consult your doctor.",
        storage="Store below 25–30°C away from moisture.",
        monitoring="Avoid driving or operating machinery if drowsy.",
        overdose_info="Can cause marked drowsiness or restlessness in children; seek medical care (108 / 112).",
        prescription_status_india="Widely used pharmacist-guided / Schedule H depending on formulation.",
        warnings=("If swelling of lips/tongue/throat or difficulty breathing occurs, call 108 / 112 immediately — antihistamines alone cannot treat anaphylaxis.",),
    ),
}

_BRAND_TO_GENERIC: dict[str, str] = {
    "crocin": "paracetamol", "dolo": "paracetamol", "dolo 650": "paracetamol", "calpol": "paracetamol", "p-500": "paracetamol",
    "ডোলো": "paracetamol", "ক্রোসিন": "paracetamol", "প্যারাসিটামল": "paracetamol", "डोलो": "paracetamol", "क्रोसिन": "paracetamol", "पैरासिटामोल": "paracetamol",
    "brufen": "ibuprofen", "ibugesic": "ibuprofen", "combiflam": "ibuprofen",
    "ব্রুফেন": "ibuprofen", "ইবুপ্রোফেন": "ibuprofen", "ब्रुफेन": "ibuprofen", "इबुप्रोफेन": "ibuprofen",
    "glycomet": "metformin", "gluconorm": "metformin", "obimet": "metformin",
    "মেটফরমিন": "metformin", "मेटफॉर्मिन": "metformin",
    "amlong": "amlodipine", "amlokind": "amlodipine", "stamlo": "amlodipine",
    "অ্যামলোডিপিন": "amlodipine", "अम्लोडिपिन": "amlodipine",
    "azithral": "azithromycin", "azee": "azithromycin", "zathrin": "azithromycin",
    "অ্যাজিথ্রাল": "azithromycin", "অ্যাজিথ্রোমাইসিন": "azithromycin", "अज़िथ्रल": "azithromycin", "एज़िथ्रोमाइसिन": "azithromycin",
    "pan 40": "pantoprazole", "pan-40": "pantoprazole", "pantocid": "pantoprazole", "pantop": "pantoprazole",
    "প্যান ৪০": "pantoprazole", "প্যান্টোপ্রাজল": "pantoprazole", "पैन 40": "pantoprazole", "पैंटोप्राजोल": "pantoprazole",
    "cetzine": "cetirizine", "okacet": "cetirizine", "zyrtec": "cetirizine", "alerid": "cetirizine",
    "সেটিরিজিন": "cetirizine", "सेटिरिज़िन": "cetirizine",
}


class MedicineKnowledgeService:
    """Provides verified educational medicine monographs, generic/brand lookup, and comparisons."""

    def resolve_canonical(self, query: str) -> str:
        q = (query or "").strip().lower()
        if q in _CURATED_DB:
            return q
        if q in _BRAND_TO_GENERIC:
            return _BRAND_TO_GENERIC[q]
        for brand, gen in _BRAND_TO_GENERIC.items():
            if brand in q:
                return gen
        for gen in _CURATED_DB:
            if gen in q:
                return gen
        return q

    async def lookup(self, db: AsyncSession | None, query: str) -> dict[str, Any]:
        canon = self.resolve_canonical(query)
        mono = _CURATED_DB.get(canon)

        db_products: list[dict[str, Any]] = []
        db_interactions: list[dict[str, Any]] = []
        banned_match = False
        if db is not None:
            prods = list(await db.scalars(select(Product).where(Product.name_norm.contains(canon[:20])).limit(5)))
            for p in prods:
                db_products.append({"id": p.id, "name": p.name, "composition": p.composition, "form": p.form})
            gen_row = (await db.scalars(select(Generic).where(Generic.name_norm == canon))).first()
            if gen_row:
                ints = list(await db.scalars(select(Interaction).where(
                    (Interaction.generic_a_id == gen_row.id) | (Interaction.generic_b_id == gen_row.id)
                ).limit(5)))
                db_interactions = [{"severity": i.severity, "description": i.description} for i in ints]
            banned_match = bool((await db.scalars(select(BannedFdc).where(BannedFdc.composition_key.contains(canon)))).first())

        if mono is not None:
            return {
                "found": True,
                "query": query,
                "generic_name": mono.generic_name,
                "brand_names_india": list(mono.brand_names_india),
                "drug_class": mono.drug_class,
                "pharmacological_class": mono.pharmacological_class,
                "indications": list(mono.indications),
                "mechanism_of_action": mono.mechanism_of_action,
                "dosage_forms": list(mono.dosage_forms),
                "route": mono.route,
                "common_adverse_effects": list(mono.common_adverse_effects),
                "serious_adverse_effects": list(mono.serious_adverse_effects),
                "contraindications": list(mono.contraindications),
                "drug_interactions": list(mono.drug_interactions),
                "food_interactions": mono.food_interactions,
                "pregnancy_considerations": mono.pregnancy_considerations,
                "breastfeeding_considerations": mono.breastfeeding_considerations,
                "storage": mono.storage,
                "monitoring": mono.monitoring,
                "overdose_info": mono.overdose_info,
                "prescription_status_india": mono.prescription_status_india,
                "warnings": list(mono.warnings),
                "narrow_therapeutic_index": is_nti(canon),
                "banned_fdc_alert": banned_match,
                "catalog_products": db_products,
                "catalog_interactions": db_interactions,
                "source": mono.source,
                "last_verified": mono.last_verified,
                "clinician_note": "Educational information only — consult a doctor or pharmacist before starting or changing any medicine; exact dosing depends on age, indication, kidney/liver function, and other medicines.",
            }

        # Fallback: try openFDA cache/API if DB session available
        fda_data = await openfda.label(db, canon) if db is not None else None
        if fda_data and not fda_data.get("_error"):
            return {
                "found": True,
                "query": query,
                "generic_name": canon.title(),
                "brand_names_india": [p["name"] for p in db_products],
                "indications": [fda_data.get("indications", "Not provided")],
                "common_adverse_effects": [fda_data.get("adverse_reactions", "Not provided")],
                "contraindications": [fda_data.get("contraindications", "Not provided")],
                "drug_interactions": [fda_data.get("drug_interactions", "Not provided")],
                "pregnancy_considerations": fda_data.get("pregnancy", "Consult a doctor."),
                "storage": fda_data.get("storage", "Store as directed on label."),
                "overdose_info": fda_data.get("overdosage", "Seek emergency medical care (108 / 112)."),
                "warnings": [fda_data.get("warnings", "Verify with a doctor or pharmacist.")],
                "source": "openFDA Drug Label",
                "last_verified": "2026-11-01",
                "clinician_note": "Educational information only — consult a qualified doctor or pharmacist.",
            }

        return {
            "found": False,
            "query": query,
            "message": f"Verified monograph data for '{query}' is not in the local medicine database. Please check the exact printed composition or consult a qualified doctor or pharmacist.",
            "catalog_products": db_products,
            "clinician_note": "Do not guess medicine names or doses; confirm with a doctor or pharmacist.",
        }

    async def compare(self, db: AsyncSession | None, med_a: str, med_b: str) -> dict[str, Any]:
        info_a = await self.lookup(db, med_a)
        info_b = await self.lookup(db, med_b)
        same_class = bool(
            info_a.get("found") and info_b.get("found")
            and info_a.get("drug_class") == info_b.get("drug_class")
        )
        same_generic = bool(
            info_a.get("found") and info_b.get("found")
            and info_a.get("generic_name") == info_b.get("generic_name")
        )
        return {
            "medicine_a": info_a,
            "medicine_b": info_b,
            "same_generic": same_generic,
            "same_drug_class": same_class,
            "safety_note": (
                "Both refer to the same active generic medicine — avoid taking them together to prevent double-dosing."
                if same_generic
                else "Always verify with a doctor or pharmacist before combining or switching medicines."
            ),
        }
