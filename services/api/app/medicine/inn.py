"""Name normalisation between Indian (INN/BP-style) names and US (USAN) names used by openFDA.

Indian labels follow INN / Indian Pharmacopoeia naming; openFDA uses USAN. Only well-established
synonyms are listed (WHO INN vs USAN). Spelling variants common on Indian strips are folded in.
"""
INN_TO_USAN = {
    "paracetamol": "acetaminophen",
    "salbutamol": "albuterol",
    "levosalbutamol": "levalbuterol",
    "glibenclamide": "glyburide",
    "adrenaline": "epinephrine",
    "noradrenaline": "norepinephrine",
    "lignocaine": "lidocaine",
    "rifampicin": "rifampin",
    "mesalazine": "mesalamine",
    "pethidine": "meperidine",
    "isoprenaline": "isoproterenol",
    "ciclosporin": "cyclosporine",
    "frusemide": "furosemide",
    "bendroflumethiazide": "bendroflumethiazide",
    "thyroxine": "levothyroxine",
    "orciprenaline": "metaproterenol",
    "dicycloverine": "dicyclomine",
    "chlorphenamine": "chlorpheniramine",
    "colecalciferol": "cholecalciferol",
    "aciclovir": "acyclovir",
    "valaciclovir": "valacyclovir",
    "amoxicillin": "amoxicillin",
    "cefalexin": "cephalexin",
    "sulfamethoxazole": "sulfamethoxazole",
    "co-trimoxazole": "sulfamethoxazole and trimethoprim",
    "beclometasone": "beclomethasone",
    "dexamethasone": "dexamethasone",
    "betamethasone": "betamethasone",
    "oxetacaine": "oxethazaine",
    "hyoscine butylbromide": "scopolamine butylbromide",
    "methylthioninium": "methylene blue",
    "phytomenadione": "phytonadione",
    "pentazocine": "pentazocine",
    "dothiepin": "dosulepin",
}

# Spelling variants on Indian labels -> INN
VARIANTS = {
    # Indian-label / BAN names -> the names DDInter and INN use (so interactions match)
    "acetylsalicylic acid": "aspirin",
    "levocetrizine": "levocetirizine",  # misspelling used in the CDSCO gazette list
    "cetrizine": "cetirizine",
    "paracetamole": "paracetamol",
    "lignocaine": "lidocaine",
    "adrenaline": "epinephrine",
    "noradrenaline": "norepinephrine",
    "sulphamethoxazole": "sulfamethoxazole",
    "sulphasalazine": "sulfasalazine",
    "sulphadiazine": "sulfadiazine",
    "diethyl carbamazine": "diethylcarbamazine",
    "oestradiol": "estradiol",
    "cephalexin monohydrate": "cefalexin",
    "amoxycillin": "amoxicillin",
    "cephalexin": "cefalexin",
    "levothyroxine": "levothyroxine",
    "thyroxine sodium": "levothyroxine",
    "frusemide": "furosemide",
    "clavulanic acid": "clavulanic acid",
    "clavulanate": "clavulanic acid",
    "acetaminophen": "paracetamol",
    "albuterol": "salbutamol",
    "glyburide": "glibenclamide",
    "vitamin d3": "colecalciferol",
    "cholecalciferol": "colecalciferol",
    "metformin": "metformin",
    "pantoprazole": "pantoprazole",
    "esomeprazole magnesium": "esomeprazole",
    "rabeprazole": "rabeprazole",
    "domperidone": "domperidone",
    "cetirizine": "cetirizine",
    "levocetirizine": "levocetirizine",
    "ors": "oral rehydration salts",
}

# Narrow-therapeutic-index drugs: never suggest switching brands without a doctor.
NTI = {
    "levothyroxine", "warfarin", "acenocoumarol", "phenytoin", "carbamazepine", "valproic acid",
    "sodium valproate", "lithium", "digoxin", "tacrolimus", "ciclosporin", "cyclosporine", "theophylline",
    "aminophylline", "mycophenolate", "sirolimus", "everolimus", "methotrexate", "flecainide", "amiodarone",
    "clozapine", "lamotrigine", "phenobarbital",
}


def canonical_generic(name: str) -> str:
    n = name.strip().lower()
    return VARIANTS.get(n, n)


def to_usan(inn: str) -> str:
    return INN_TO_USAN.get(inn.lower(), inn.lower())


def is_nti(generic: str) -> bool:
    return generic.lower() in NTI
