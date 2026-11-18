"""Drug-master ingestion from real public sources.

    python -m app.pipelines.drugs janaushadhi <file>     # PMBJP product list export (.json/.csv/.xlsx/.pdf)
    python -m app.pipelines.drugs brands <file.csv>      # curated/licensed brand -> composition list
    python -m app.pipelines.drugs ddinter <dir-or-csv>   # DDInter 2.0 interaction CSVs
    python -m app.pipelines.drugs banned-fdc <file.pdf|txt>  # CDSCO banned FDC notification list
    python -m app.pipelines.drugs generics                  # build drug_generics from loaded products
    python -m app.pipelines.drugs monographs [--top 300]    # pre-cache openFDA labels for common generics

Jan Aushadhi: open https://janaushadhi.gov.in/product-portfolio/product-mrp-list and use the page's
download/export button (or save the list). Columns: Sr No, Drug Code, Generic Name, Unit Size, MRP,
Group Name. The ingester also accepts the site's JSON (`newProductResponsesList`) if saved from the page.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select

from app.db import SessionLocal, init_db
from app.medicine.parse import _STRENGTH as _STRENGTH_RE
from app.medicine.parse import clean_generic, parse_composition, parse_pack
from app.medicine.service import norm
from app.models import BannedFdc, Interaction, Price, Product

JA_URL = "https://janaushadhi.gov.in/product-portfolio/product-mrp-list"
_HEADER_ALIASES = {
    "drug_code": {"drug code", "drugcode", "drug_code", "code"},
    "generic_name": {"generic name", "genericname", "generic_name", "name of the product", "product name"},
    "unit_size": {"unit size", "unitsize", "unit_size", "pack size"},
    "mrp": {"mrp", "mrp (rs.)", "mrp(rs)", "mrp in rs", "mrp (in rs.)"},
    "group_name": {"group name", "groupname", "group_name", "therapeutic group", "category"},
}


def _canon(h: str) -> str | None:
    h = re.sub(r"\s+", " ", str(h).strip().lower())
    for k, aliases in _HEADER_ALIASES.items():
        if h in aliases:
            return k
    return None


def _rows_from_table(rows: list[list]) -> list[dict]:
    header_idx = next((i for i, r in enumerate(rows) if sum(_canon(c or "") is not None for c in r) >= 3), None)
    if header_idx is None:
        raise ValueError("Could not find a header row with Drug Code / Generic Name / MRP columns")
    cols = [_canon(c or "") for c in rows[header_idx]]
    out = []
    for r in rows[header_idx + 1:]:
        d = {cols[i]: (str(v).strip() if v is not None else "") for i, v in enumerate(r) if i < len(cols) and cols[i]}
        if d.get("generic_name") and d.get("mrp"):
            out.append(d)
    return out


def read_janaushadhi(path: Path) -> list[dict]:
    suf = path.suffix.lower()
    if suf == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("responseBody", data).get("newProductResponsesList", data) if isinstance(data, dict) else data
        return [{"drug_code": str(i.get("drugCode", "")), "generic_name": i.get("genericName", ""),
                 "unit_size": i.get("unitSize", ""), "mrp": str(i.get("mrp", "")),
                 "group_name": i.get("groupName", "")} for i in items if i.get("status", 1) == 1]
    if suf == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            return _rows_from_table(list(csv.reader(f)))
    if suf in (".xlsx", ".xls"):
        try:
            import openpyxl
        except ImportError:
            sys.exit("pip install openpyxl to read Excel exports")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        return _rows_from_table([list(r) for r in wb.active.iter_rows(values_only=True)])
    if suf == ".pdf":
        import fitz  # PyMuPDF

        rows: list[list] = []
        with fitz.open(path) as doc:
            for page in doc:
                for t in page.find_tables().tables:
                    rows += t.extract()
        # PDFs repeat headers per page; _rows_from_table skips non-data rows via the mrp check
        return [r for r in _rows_from_table(rows) if re.match(r"^\d", r.get("mrp", ""))]
    raise ValueError(f"Unsupported file type: {suf}")


async def ingest_janaushadhi(path: Path) -> None:
    rows = read_janaushadhi(path)
    observed = date.fromtimestamp(path.stat().st_mtime)
    added = updated = skipped = 0
    async with SessionLocal() as db:
        for r in rows:
            try:
                mrp = float(re.sub(r"[^\d.]", "", r["mrp"]))
            except ValueError:
                skipped += 1
                continue
            comp = parse_composition(r["generic_name"])
            if not comp.ingredients:
                skipped += 1
                continue
            pack, unit = parse_pack(r.get("unit_size", ""))
            ref = r.get("drug_code") or r["generic_name"]
            p = (await db.scalars(select(Product).where(Product.source == "janaushadhi",
                                                        Product.source_ref == ref))).first()
            if p is None:
                p = Product(source="janaushadhi", source_ref=ref, is_janaushadhi=True, verified=True,
                            manufacturer="PMBI (Jan Aushadhi)")
                db.add(p)
                added += 1
            else:
                updated += 1
            p.name, p.name_norm = r["generic_name"], norm(r["generic_name"])
            p.composition_key, p.composition = comp.key, comp.as_list()
            p.form, p.release_type = comp.form, comp.release
            p.pack_size, p.pack_unit, p.category = pack, unit, r.get("group_name") or None
            await db.flush()
            per_unit = round(mrp / pack, 2) if pack and unit == "units" else None
            await db.execute(delete(Price).where(Price.product_id == p.id, Price.source == "janaushadhi",
                                                 Price.observed_on == observed))
            if mrp <= 0:  # the official list carries a few ₹0 placeholder rows
                continue
            db.add(Price(product_id=p.id, mrp_inr=mrp, per_unit_inr=per_unit, source="janaushadhi",
                         source_url=JA_URL, observed_on=observed))
        await db.commit()
    print(f"Jan Aushadhi: {added} added, {updated} updated, {skipped} skipped (of {len(rows)} rows)")


async def ingest_brands(path: Path) -> None:
    """CSV columns: brand,manufacturer,composition,form,pack_size,mrp_inr,source,source_url[,observed_on]
    composition example: 'Paracetamol 650mg' or 'Amoxicillin 500mg + Clavulanic Acid 125mg'.
    Only load data you are licensed to use (see docs/licence-register.md)."""
    added = 0
    async with SessionLocal() as db:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                comp = parse_composition(f"{r['composition']} {r.get('form', '')}")
                if not comp.ingredients:
                    continue
                pack, unit = parse_pack(r.get("pack_size", ""))
                ref = f"{r['brand']}|{r.get('manufacturer', '')}|{r.get('pack_size', '')}"
                src = r.get("source") or "brand_csv"
                p = (await db.scalars(select(Product).where(Product.source == src, Product.source_ref == ref))).first()
                if p is None:
                    p = Product(source=src, source_ref=ref)
                    db.add(p)
                    added += 1
                p.name, p.name_norm, p.manufacturer = r["brand"], norm(r["brand"]), r.get("manufacturer")
                p.composition_key, p.composition, p.form, p.release_type = comp.key, comp.as_list(), comp.form, comp.release
                p.pack_size, p.pack_unit, p.verified = pack, unit, True
                await db.flush()
                if r.get("mrp_inr"):
                    mrp = float(r["mrp_inr"])
                    db.add(Price(product_id=p.id, mrp_inr=mrp,
                                 per_unit_inr=round(mrp / pack, 2) if pack and unit == "units" else None,
                                 source=src, source_url=r.get("source_url"),
                                 observed_on=date.fromisoformat(r["observed_on"]) if r.get("observed_on") else date.today()))
        await db.commit()
    print(f"Brands: {added} added")


async def ingest_ddinter(path: Path) -> None:
    """DDInter 2.0 downloads (ddinter.scbdd.com/download/): CSVs with Drug_A, Drug_B, Level columns."""
    files = sorted(path.glob("*.csv")) if path.is_dir() else [path]
    n = 0
    async with SessionLocal() as db:
        await db.execute(delete(Interaction).where(Interaction.source == "ddinter"))
        seen = set()
        for fp in files:
            with fp.open(encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    a, b = (r.get("Drug_A") or "").strip().lower(), (r.get("Drug_B") or "").strip().lower()
                    if not a or not b:
                        continue
                    from app.medicine.inn import canonical_generic
                    a, b = sorted((canonical_generic(a), canonical_generic(b)))
                    if (a, b) in seen:
                        continue
                    seen.add((a, b))
                    db.add(Interaction(generic_a=a, generic_b=b, severity=(r.get("Level") or "Unknown").strip(),
                                       source="ddinter"))
                    n += 1
        await db.commit()
    print(f"DDInter: {n} interaction pairs loaded from {len(files)} file(s)")


_NOTIF = re.compile(r"\b(G\.?\s?S\.?\s?R|S\.?\s?O)\b\.?", re.I)
# "183." alone on a line, or "440.   Fixed Dose …" — but not "7.5/7.5" or dates like "10.03.2016"
_ENTRY = re.compile(r"^\s*(\d{1,4})\s*\.(?!\d)[ \t]*", re.M)
_FDC_PREFIX = re.compile(r"(?i)^fixed\s+dose\s+combinations?\s+of\s+")

# Footnotes printed at the end of the CDSCO list (as uploaded in 2018). The status is what THAT document
# says; later gazette notifications (e.g. the 2018 and 2024 FDC bans) are not in it and must be added.
BANNED_STATUS = {
    "": ("in_force", "Prohibited under Section 26A of the Drugs & Cosmetics Act."),
    "*": ("stayed", "Prohibition stayed by the Hon'ble High Court of Madras (per CDSCO list) — check current status."),
    "**": ("revoked_conditional", "Prohibition revoked with conditions (G.S.R. 367(E), 13.04.2017): "
                                  "permitted only for cancer pain, max 300 mg/day."),
    "***": ("quashed_2016", "Part of the March 2016 FDC ban (S.O. 705–1048) that the Delhi High Court quashed on "
                            "01.12.2016; the matter went to the Supreme Court and many of these FDCs were later "
                            "re-examined. Check current status before relying on this."),
}


def parse_banned_list(text: str) -> list[dict]:
    """Parse CDSCO's 'Drugs prohibited for manufacture and sale … under Section 26A' list.

    Returns [{"sr", "text", "notification", "kind": combination|single|class, "status", "status_note"}].
    """
    foot = text.find("*Presently stayed")
    body = text[:foot] if foot > 0 else text
    marks = [m for m in _ENTRY.finditer(body)]
    # keep the strictly increasing serial-number sequence (drops stray numbers inside entries)
    seq, last = [], 0
    for m in marks:
        n = int(m.group(1))
        if n == last + 1:
            seq.append(m)
            last = n
    out = []
    for i, m in enumerate(seq):
        block = body[m.end(): seq[i + 1].start() if i + 1 < len(seq) else len(body)]
        nm = _NOTIF.search(block)
        raw_name = block[: nm.start()] if nm else block
        notif = re.sub(r"\s+", " ", block[nm.start():]).strip()[:120] if nm else None
        stars = max((len(s) for s in re.findall(r"\*+", raw_name)), default=0)
        name = re.sub(r"\s+", " ", raw_name.replace("*", " ")).strip(" .")
        if not name:
            continue
        name = re.sub(r"([a-z])([A-Z])", lambda x: f"{x.group(1)} {x.group(2)}", name)  # MefenamicAcid
        name = _FDC_PREFIX.sub("", name)
        whole = re.match(r"(?i)^(.+?)\s+and\s+(its\s+)?formulations\b|^formulations\s+containing\s+(\S+)", name)
        if whole and "+" not in name:  # "Phenylpropanolamine and its formulations for human use"
            name = (whole.group(1) or whole.group(3)).strip()
        if "+" in name:
            kind = "combination"
        elif re.search(r"(?i)combination|preparations?|\bwith\b|\bof\b|\band\b|agents?|\bfor\b", name) \
                or len(name.split()) > 4:
            kind = "class"  # e.g. "Fixed dose combinations of vitamins with anti-inflammatory agents"
        else:
            kind = "single"
        so = re.search(r"S\.?\s?O\.?\s*(\d{3,4})", notif or "")
        if so and 705 <= int(so.group(1)) <= 1048 and "2016" in (notif or ""):
            stars = 3  # the *** footnote covers the whole S.O. 705–1048 (10.03.2016) series
        status, note = BANNED_STATUS.get("*" * min(stars, 3), BANNED_STATUS[""])
        out.append({"sr": int(m.group(1)), "text": name, "notification": notif, "kind": kind,
                    "status": status, "status_note": note})
    return out


async def ingest_banned_fdc(path: Path) -> None:
    """CDSCO list of drugs prohibited under Section 26A (cdsco.gov.in > Banned Drugs, PDF)."""
    if path.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            text = "\n".join(page.get_text() for page in doc)
    else:
        text = path.read_text(encoding="utf-8")
    entries = parse_banned_list(text)
    counts = {"combination": 0, "single": 0, "class": 0}
    from app.db import engine

    async with engine.begin() as conn:  # public reference data: rebuilt on every load
        await conn.run_sync(BannedFdc.__table__.drop, checkfirst=True)
        await conn.run_sync(BannedFdc.__table__.create)
    async with SessionLocal() as db:
        for e in entries:
            if e["kind"] == "class":
                counts["class"] += 1
                continue
            parts = [p for p in re.split(r"\s*\+\s*", e["text"]) if p.strip()]
            gens = sorted({g for g in (clean_generic(_STRENGTH_RE.sub(" ", p)) for p in parts) if g})
            if not gens:
                continue
            db.add(BannedFdc(composition_key="+".join(gens), description=f"{e['sr']}. {e['text']}"[:500],
                             notification_ref=e["notification"], status=e["status"], status_note=e["status_note"],
                             source_url=CDSCO_BANNED_URL))
            counts[e["kind"]] += 1
        await db.commit()
    by_status: dict[str, int] = {}
    for e in entries:
        by_status[e["status"]] = by_status.get(e["status"], 0) + 1
    print(f"CDSCO list: {len(entries)} entries — {counts['combination']} combinations + {counts['single']} single "
          f"drugs loaded, {counts['class']} class-level entries skipped. Status: {by_status}")


# Subset of India's National List of Essential Medicines 2022 (CDSCO/MoHFW), single-ingredient INNs only.
NLEM_2022 = set("""
paracetamol ibuprofen aspirin diclofenac morphine tramadol amoxicillin ampicillin azithromycin cefalexin
cefixime ceftriaxone cefuroxime ciprofloxacin clarithromycin co-trimoxazole doxycycline gentamicin
metronidazole nitrofurantoin tinidazole vancomycin linezolid meropenem piperacillin cloxacillin
benzathine benzylpenicillin isoniazid rifampicin pyrazinamide ethambutol bedaquiline delamanid
fluconazole clotrimazole griseofulvin terbinafine itraconazole amphotericin acyclovir aciclovir oseltamivir
tenofovir lamivudine dolutegravir efavirenz zidovudine chloroquine artesunate artemether lumefantrine
primaquine quinine albendazole ivermectin praziquantel diethylcarbamazine metformin glibenclamide
glimepiride gliclazide insulin sitagliptin teneligliptin dapagliflozin amlodipine atenolol enalapril
losartan telmisartan hydrochlorothiazide chlorthalidone furosemide spironolactone metoprolol
carvedilol ramipril digoxin isosorbide clopidogrel atorvastatin rosuvastatin warfarin heparin enoxaparin
levothyroxine carbimazole prednisolone dexamethasone hydrocortisone methylprednisolone salbutamol
budesonide beclometasone ipratropium tiotropium montelukast cetirizine chlorphenamine pheniramine
omeprazole pantoprazole ranitidine famotidine ondansetron domperidone metoclopramide loperamide
lactulose bisacodyl oral rehydration salts zinc ferrous folic acid cyanocobalamin colecalciferol
calcium carbonate phenytoin carbamazepine sodium valproate valproic acid levetiracetam phenobarbital
diazepam lorazepam midazolam clonazepam amitriptyline fluoxetine sertraline escitalopram olanzapine
risperidone haloperidol lithium levodopa carbidopa trihexyphenidyl donepezil methotrexate
hydroxychloroquine sulfasalazine allopurinol colchicine tamoxifen letrozole oxytocin misoprostol
mifepristone magnesium sulfate nifedipine labetalol methyldopa medroxyprogesterone levonorgestrel
ethinylestradiol clomifene lidocaine ketamine propofol atropine adrenaline epinephrine noradrenaline
dopamine naloxone ors mupirocin permethrin silver sulfadiazine povidone iodine timolol tropicamide
""".split()) | {"oral rehydration salts", "folic acid", "valproic acid", "sodium valproate", "calcium carbonate",
                "magnesium sulfate", "silver sulfadiazine"}


_NON_DRUG_CATEGORIES = {"surgical & medical consumables", "ayurvedic", "mosquito repellent", "footcare cream",
                        "throat spray for freshness", "irrigation fluid"}
_FORM_SUFFIX = re.compile(r"\s+(tab|tabs|tablets?|cap|caps|capsules?|syrup|susp|suspension|inj|injection|iv|per|"
                          r"paediatric|pediatric|eye drops?|ear drops?|drops?|gel|cream|ointment|transdermal|"
                          r"liquid|soft|oral|powder|sachet|spray|lotion|solution|ip|bp|usp|i p|injections|suppositor(y|ies)|"
                          r"ophthalmic|opthalmic|use|[a-z])$")
_FRAGMENTS = {"human", "pant", "per", "pre", "solvent", "oxide", "malate", "orotate", "iodide", "wfi", "ferrous",
              "silver", "copper", "in flavour base", "dil oil", "madhu", "karpura", "cocoa", "tetanus", "rabies"}
_NOT_A_DRUG = re.compile(r"(size|napkin|mask|needle|stick|cane|belt|pillow|tape|gauze|dressing|bandage|glove|"
                         r"cap|suture|braided|polyster|cotton|syringe|catheter|sterile|support|repellent|"
                         r"suvidha|janaushadhi|thermometer|kit|strip|test|wheel|chair)")


def _clean_generic_name(g: str) -> str | None:
    g = re.sub(r"\s+", " ", g.strip().lower())
    for _ in range(3):
        g2 = _FORM_SUFFIX.sub("", g)
        if g2 == g:
            break
        g = g2
    if not (3 <= len(g) <= 60) or not re.search(r"[a-z]{3}", g) or _NOT_A_DRUG.search(g) or len(g.split()) > 4 \
            or g in _FRAGMENTS:
        return None
    return g


async def build_generics() -> None:
    """drug_generics = every INN that appears in a loaded product's composition (Jan Aushadhi + brands),
    with its USAN name (for openFDA), NLEM 2022 and narrow-therapeutic-index flags. Products already share
    a canonical composition_key (salt + strength), which is how brands are matched to Jan Aushadhi generics."""
    from app.medicine.inn import canonical_generic, is_nti, to_usan
    from app.models import Generic

    async with SessionLocal() as db:
        names: dict[str, int] = {}
        for comp, cat in await db.execute(select(Product.composition, Product.category)):
            if (cat or "").lower() in _NON_DRUG_CATEGORIES:
                continue
            for ing in comp or []:
                g = _clean_generic_name(canonical_generic(ing.get("generic") or ""))
                if g:
                    names[g] = names.get(g, 0) + 1
        from sqlalchemy import delete as _del
        await db.execute(_del(Generic).where(Generic.inn_name.not_in(list(names))))
        existing = {g.inn_name: g for g in await db.scalars(select(Generic))}
        added = 0
        for g in sorted(names):
            row = existing.get(g)
            if row is None:
                row = Generic(inn_name=g)
                db.add(row)
                added += 1
            usan = to_usan(g)
            row.usan_name = usan if usan != g else None
            row.nlem = g in NLEM_2022 or g.split()[0] in NLEM_2022
            row.nti = is_nti(g)
        await db.commit()
    print(f"drug_generics: {added} added, {len(names)} distinct generics from products")


async def precache_monographs(top: int = 300, to_kb: bool = True) -> None:
    """Fetch openFDA labels for the `top` most common generics in Jan Aushadhi (by number of products),
    store them in drug_monograph_cache and, optionally, as RAG chunks in the `drug_monographs` collection."""
    from app.medicine import openfda
    from app.rag.store import add_document

    async with SessionLocal() as db:
        counts: dict[str, int] = {}
        for comp in await db.scalars(select(Product.composition)):
            for ing in comp or []:
                g = (ing.get("generic") or "").lower()
                if g:
                    counts[g] = counts.get(g, 0) + 1
        order = sorted(counts, key=lambda g: -counts[g])[:top]
        ok = miss = chunks = 0
        for i, g in enumerate(order, 1):
            try:
                payload = await openfda.label(db, g)
            except Exception as e:  # noqa: BLE001 - keep going on one bad label
                print(f"  {g}: error {e}")
                continue
            if not payload:
                miss += 1
                continue
            ok += 1
            if to_kb:
                meta = payload.get("_meta", {})
                text = "\n\n".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in payload.items()
                                     if k != "_meta" and isinstance(v, str))
                chunks += await add_document(db, collection="drug_monographs", source_id=f"openfda:{g}",
                                             title=f"US FDA label — {g}", text=text, source_url=meta.get("url"),
                                             version=meta.get("effective_time"))
            if i % 25 == 0:
                print(f"  {i}/{len(order)}: {ok} found, {miss} not on openFDA")
            await asyncio.sleep(0.3)  # openFDA: 240 req/min without a key
    print(f"openFDA: {ok} cached, {miss} not found (of {len(order)}); {chunks} KB chunks added")


CDSCO_BANNED_URL = "https://cdsco.gov.in/opencms/resources/UploadCDSCOWeb/2018/UploadAlertsFiles/banneddrugs_1.pdf"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["janaushadhi", "brands", "ddinter", "banned-fdc", "generics", "monographs"])
    ap.add_argument("path", type=Path, nargs="?")
    ap.add_argument("--top", type=int, default=300)
    a = ap.parse_args()
    if a.source in ("generics", "monographs"):
        async def run_simple():
            await init_db()
            await (build_generics() if a.source == "generics" else precache_monographs(a.top))
        asyncio.run(run_simple())
        return
    if a.path is None or not a.path.exists():
        sys.exit(f"File not found: {a.path}")
    fn = {"janaushadhi": ingest_janaushadhi, "brands": ingest_brands, "ddinter": ingest_ddinter,
          "banned-fdc": ingest_banned_fdc}[a.source]

    async def run():
        await init_db()
        await fn(a.path)

    asyncio.run(run())


if __name__ == "__main__":
    main()
