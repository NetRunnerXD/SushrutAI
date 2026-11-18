"""Knowledge-base ingestion for RAG.

    python -m app.pipelines.kb medlineplus "diabetes" "dengue" "typhoid" ...   # NLM MedlinePlus web service
    python -m app.pipelines.kb medlineplus --file topics.txt
    python -m app.pipelines.kb medlineplus-all             # every English MedlinePlus health topic (XML file)
    python -m app.pipelines.kb cdc                         # CDC pages for conditions common in India
    python -m app.pipelines.kb who                         # WHO fact sheets (CC BY-NC-SA 3.0 IGO)
    python -m app.pipelines.kb nhs                         # NHS Health A-Z (needs NHS_API_KEY in .env)
          add --fetch-only to just download into data/sources/web_cache (no database writes)
    python -m app.pipelines.kb pdf-dir <folder> --source icmr_stw --collection guidelines_in
          # e.g. ICMR Standard Treatment Workflows PDFs downloaded from https://stw.icmr.org.in

MedlinePlus Web Service (free, no key): https://medlineplus.gov/about/developers/webservices/
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.db import SessionLocal, init_db
from app.rag.store import add_document

MEDLINEPLUS = "https://wsearch.nlm.nih.gov/ws/query"
UA = "SushrutAI-KB-ingest/0.2 (health information app; polite, 1 request/s)"
CACHE = Path(__file__).resolve().parents[4] / "data" / "sources" / "web_cache"

# CDC topics relevant in India (public domain). Each topic's pages for the public (about, symptoms, prevention,
# treatment) are fetched; pages for clinicians (/hcp/) and public-health staff (/php/) are skipped.
CDC_TOPICS = [
    "dengue", "malaria", "typhoid-fever", "tb", "hepatitis-a", "hepatitis-b", "hepatitis-c", "hepatitis-e", "cholera",
    "diarrhea", "heat-health", "rabies", "measles", "mumps", "chickenpox", "shingles", "pertussis", "diphtheria",
    "tetanus", "polio", "flu", "covid", "rsv", "pneumonia", "chikungunya", "zika", "japanese-encephalitis",
    "leptospirosis", "scrub-typhus", "hand-foot-mouth", "pinkeye", "norovirus", "diabetes", "high-blood-pressure",
    "heart-disease", "stroke", "asthma", "copd", "kidney-disease", "cancer", "arthritis", "obesity", "sleep",
    "mental-health", "oral-health", "hiv", "sti", "anemia", "sickle-cell", "thalassemia",
    "parasites", "food-safety", "clean-hands", "pregnancy", "breastfeeding",
]
_CDC_KEEP = re.compile(r"(?i)about|sign|symptom|prevent|treat|cause|risk|spread|living|manag|care|faq|basics|what-is")


async def _get(c: httpx.AsyncClient, url: str, name: str) -> str | None:
    """Fetch with a disk cache (data/sources/web_cache) and at most one request per second."""
    path = CACHE / re.sub(r"[^\w.-]+", "_", name)[:180]
    if path.exists():
        return path.read_text(encoding="utf-8")
    await asyncio.sleep(1.0)
    try:
        r = await c.get(url)
    except httpx.HTTPError as e:
        print(f"  ! {url}: {e}", flush=True)
        return None
    if r.status_code != 200:
        print(f"  ! {url}: HTTP {r.status_code}", flush=True)
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text, encoding="utf-8")
    print(f"  fetched {url}", flush=True)
    return r.text


_BOILER = re.compile(r"(?i)^(this page was last (updated|reviewed)|the information on this page was last reviewed|"
                     r"cdc logo|for professionals$|featured$|on this page$|sources?$|related (pages|links)$|"
                     r"print$|share$|español$|view all)")


def page_text(html: str) -> tuple[str, str]:
    """(title, main text) of a CDC / WHO page: headings, paragraphs and list items inside <main>/<article>,
    without navigation, footers, scripts or 'share this page' blocks."""
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.find("h1") or soup.find("title")
    title = h1.get_text(" ", strip=True) if h1 else ""
    title = re.sub(r"\s*[|\-–]\s*(CDC|World Health Organization|WHO|NHS)\s*$", "", title).strip()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    for t in root.find_all(["script", "style", "nav", "footer", "aside", "form", "noscript", "header", "button"]):
        t.decompose()
    for t in root.find_all(class_=re.compile(r"(?i)share|social|breadcrumb|related|feedback|last-?reviewed")):
        t.decompose()
    out, seen = [], set()
    for el in root.find_all(["h2", "h3", "p", "li"]):
        if el.name == "p" and el.find_parent("li") is not None:
            continue
        txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if len(txt) < 3 or txt in seen or _BOILER.search(txt):
            continue
        seen.add(txt)
        out.append(f"## {txt}" if el.name in ("h2", "h3") else (f"- {txt}" if el.name == "li" else txt))
    return title, "\n\n".join(out)


def medlineplus_topics(xml: str) -> list[dict]:
    """English health topics from the MedlinePlus Health Topic XML file: title, url, full summary text."""
    soup = BeautifulSoup(xml, "xml")
    out = []
    for t in soup.find_all("health-topic"):
        if t.get("language") != "English":
            continue
        summary = t.find("full-summary")
        if not summary:
            continue
        text = BeautifulSoup(summary.get_text(), "lxml").get_text("\n\n")
        also = [a.get_text() for a in t.find_all("also-called")]
        if also:
            text = f"Also called: {', '.join(also)}\n\n{text}"
        out.append({"title": t.get("title"), "url": t.get("url"), "text": text})
    return out


async def medlineplus_all(embed: bool, fetch_only: bool) -> None:
    async with httpx.AsyncClient(timeout=180, headers={"User-Agent": UA}, follow_redirects=True) as c:
        idx = await _get(c, "https://medlineplus.gov/xml.html", "medlineplus_xml_index.html")
        m = re.search(r"https://medlineplus\.gov/xml/mplus_topics_\d{4}-\d{2}-\d{2}\.xml", idx or "")
        if not m:
            sys.exit("MedlinePlus XML file link not found on https://medlineplus.gov/xml.html")
        xml = await _get(c, m.group(0), m.group(0).rsplit("/", 1)[1])
    topics = medlineplus_topics(xml or "")
    print(f"MedlinePlus XML: {len(topics)} English topics ({m.group(0)})", flush=True)
    if fetch_only:
        return
    total = 0
    async with SessionLocal() as db:
        for i, t in enumerate(topics, 1):
            total += await add_document(db, collection="patient_ed", source_id=f"medlineplus:{t['url']}",
                                        title=f"MedlinePlus: {t['title']}", text=t["text"], source_url=t["url"],
                                        embed=embed)
            if i % 50 == 0:
                print(f"  {i}/{len(topics)} topics, {total} new chunks", flush=True)
    print(f"MedlinePlus (all topics): {total} new chunks")


async def _web_ingest(pages: list[tuple[str, str]], source: str, label: str, embed: bool, fetch_only: bool,
                      c: httpx.AsyncClient, min_words: int = 60) -> None:
    total, kept = 0, 0
    async with SessionLocal() as db:
        for url, name in pages:
            html = await _get(c, url, name)
            if not html or fetch_only:
                continue
            title, text = page_text(html)
            if len(text.split()) < min_words:
                continue
            kept += 1
            n = await add_document(db, collection="patient_ed", source_id=f"{source}:{url}",
                                   title=f"{label}: {title}", text=text, source_url=url, embed=embed)
            total += n
            print(f"  {title}: {n} chunks", flush=True)
    print(f"{label}: {len(pages)} pages, {kept} kept, {total} new chunks")


def cdc_links(topic: str, html: str) -> list[str]:
    links = sorted({h.split("#")[0] for h in re.findall(rf'href="(/{re.escape(topic)}/[^"#]+?index\.html)', html)})
    return [h for h in links if h != f"/{topic}/index.html"
            and not re.search(r"/(hcp|php|es|media|data-research|communication-resources|site|resources|toolkit|"
                              r"stories|partners|outbreaks?|situation-summary)/", h)
            and _CDC_KEEP.search(h)][:4]


async def cdc(embed: bool, fetch_only: bool) -> None:
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as c:
        pages: list[tuple[str, str]] = []
        for topic in CDC_TOPICS:
            home = f"https://www.cdc.gov/{topic}/index.html"
            html = await _get(c, home, f"cdc_{topic}_index.html")
            if not html:
                continue
            pages.append((home, f"cdc_{topic}_index.html"))
            pages += [(f"https://www.cdc.gov{h}", f"cdc{h}") for h in cdc_links(topic, html)]
        await _web_ingest(pages, "cdc", "CDC", embed, fetch_only, c)


async def who(embed: bool, fetch_only: bool) -> None:
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as c:
        idx = await _get(c, "https://www.who.int/news-room/fact-sheets", "who_fact_sheets_index.html") or ""
        slugs = sorted(set(re.findall(r"fact-sheets/detail/([^\"#?\s<]+)", idx)))
        print(f"WHO: {len(slugs)} fact sheets", flush=True)
        pages = [(f"https://www.who.int/news-room/fact-sheets/detail/{s}", f"who_{s}.html") for s in slugs]
        await _web_ingest(pages, "who", "WHO fact sheet", embed, fetch_only, c, min_words=120)


async def nhs(embed: bool, fetch_only: bool) -> None:
    """NHS website content API (Open Government Licence v3.0). Needs a free subscription key."""
    import json as _json

    from app.config import get_settings

    key = get_settings().nhs_api_key
    if not key:
        print("NHS: skipped (set NHS_API_KEY in .env; free key from https://developer.api.nhs.uk)")
        return
    base = "https://api.nhs.uk/conditions"
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA, "subscription-key": key},
                                 follow_redirects=True) as c, SessionLocal() as db:
        urls, page = [], 1
        while page < 100:
            raw = await _get(c, f"{base}/?page={page}", f"nhs_conditions_p{page}.json")
            items = (_json.loads(raw).get("significantLink") or []) if raw else []
            if not items:
                break
            urls += [i["url"] for i in items if i.get("url")]
            page += 1
        total = 0
        for u in urls:
            raw = await _get(c, u, "nhs_" + u.rsplit("/conditions/", 1)[-1].strip("/").replace("/", "_") + ".json")
            if not raw or fetch_only:
                continue
            d = _json.loads(raw)
            parts = []
            for sec in d.get("hasPart") or []:
                for el in sec.get("hasPart") or []:
                    txt = BeautifulSoup(el.get("text") or "", "lxml").get_text("\n\n").strip()
                    if txt:
                        parts.append(f"## {sec.get('headline')}\n\n{txt}" if sec.get("headline") else txt)
            text = "\n\n".join(parts)
            if len(text.split()) < 60:
                continue
            web = d.get("url", u).replace("api.nhs.uk", "www.nhs.uk")
            total += await add_document(db, collection="patient_ed", source_id=f"nhs:{web}",
                                        title=f"NHS: {d.get('name') or d.get('headline')}", text=text,
                                        source_url=web, embed=embed)
        print(f"NHS: {len(urls)} conditions, {total} new chunks")


async def medlineplus(terms: list[str], embed: bool) -> None:
    total = 0
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": "SushrutAI-KB-ingest/0.1"}) as c, \
            SessionLocal() as db:
        for term in terms:
            r = await c.get(MEDLINEPLUS, params={"db": "healthTopics", "term": term, "retmax": 3})
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "xml")
            for doc in soup.find_all("document"):
                url = doc.get("url")
                title = BeautifulSoup((doc.find("content", {"name": "title"}) or "").get_text(), "lxml").get_text()
                summary_tag = doc.find("content", {"name": "FullSummary"})
                if not summary_tag:
                    continue
                text = BeautifulSoup(summary_tag.get_text(), "lxml").get_text("\n\n")
                n = await add_document(db, collection="patient_ed", source_id=f"medlineplus:{url}",
                                       title=f"MedlinePlus: {title}", text=text, source_url=url, embed=embed)
                total += n
                print(f"  {title}: {n} chunks")
            await asyncio.sleep(0.5)  # NLM asks for <= 85 requests/minute
    print(f"MedlinePlus: {total} new chunks")


SOURCE_PREFIX = {
    "icmr_stw": "ICMR Standard Treatment Workflow",
    "nhm_stg": "MoHFW / NHM guideline",
    "ntep": "NTEP (National TB Elimination Programme)",
    "ncvbdc": "NCVBDC (vector-borne disease) guideline",
    "icmr_nin": "ICMR-NIN Dietary Guidelines",
    "immunisation": "MoHFW National Immunization Schedule",
}
SOURCE_HOME = {
    "nhm_stg": "https://www.mohfw.gov.in/", "ntep": "https://tbcindia.mohfw.gov.in/guidelines/",
    "ncvbdc": "https://ncvbdc.mohfw.gov.in/", "icmr_nin": "https://www.nin.res.in/",
    "immunisation": "https://nhm.gov.in/New_Updates_2018/NHM_Components/Immunization/report/"
                    "National_%20Immunization_Schedule.pdf",
}


async def pdf_dir(folder: Path, source: str, collection: str, embed: bool) -> None:
    """Ingest every PDF in `folder`. A `urls.txt` beside the PDFs (lines: `<file.pdf> <url>`) sets each
    document's source_url; otherwise the programme's home page is used."""
    import fitz

    total = 0
    urls = {}
    if (folder / "urls.txt").exists():
        for line in (folder / "urls.txt").read_text(encoding="utf-8").splitlines():
            if " " in line.strip():
                name, url = line.strip().split(" ", 1)
                urls[name] = url.strip()
    async with SessionLocal() as db:
        for pdf in sorted(folder.glob("*.pdf")):
            with fitz.open(pdf) as doc:
                pages = [p.get_text() for p in doc]
            text = re.sub(r"[ \t]+", " ", "\n\n".join(pages))
            title = re.sub(r"^\d+\s+(\d+\s+)?", "", pdf.stem.replace("_", " ").replace("-", " "))
            title = re.sub(r"\ball\b", "", title).strip().title()
            if source in SOURCE_PREFIX:
                title = f"{SOURCE_PREFIX[source]} — {title}"
            n = await add_document(db, collection=collection, source_id=f"{source}:{pdf.name}", title=title,
                                   text=text, source_url=urls.get(pdf.name) or SOURCE_HOME.get(source),
                                   embed=embed)
            total += n
            print(f"  {pdf.name}: {n} chunks")
    print(f"{source}: {total} new chunks")


async def reembed(batch: int = 16) -> None:
    """Embed chunks that were ingested without embeddings (e.g. before bge-m3 was pulled)."""
    import numpy as np
    from sqlalchemy import select

    from app.config import get_settings
    from app.llm.providers import ollama
    from app.models import KbChunk

    done = 0
    async with SessionLocal() as db:
        rows = list(await db.scalars(select(KbChunk).where(KbChunk.embedding.is_(None))))
        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            vecs = await ollama().embed(get_settings().embed_model, [f"{r.title}\n{r.content}" for r in part])
            for r, v in zip(part, vecs):
                r.embedding = np.asarray(v, dtype=np.float32).tobytes()
            await db.commit()
            done += len(part)
            print(f"  embedded {done}/{len(rows)}")
    print(f"Re-embedded {done} chunks")


WEB = {"medlineplus-all": medlineplus_all, "cdc": cdc, "who": who, "nhs": nhs}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("medlineplus")
    m.add_argument("terms", nargs="*")
    m.add_argument("--file", type=Path)
    d = sub.add_parser("pdf-dir")
    d.add_argument("folder", type=Path)
    d.add_argument("--source", required=True, help="e.g. icmr_stw, nhm_stg, ntep, ncvbdc, icmr_nin")
    d.add_argument("--collection", default="guidelines_in")
    sub.add_parser("reembed", help="embed chunks that have no embedding yet")
    for name in WEB:
        sub.add_parser(name)
    ap.add_argument("--fetch-only", action="store_true", help="download into the web cache only")
    ap.add_argument("--no-embed", action="store_true", help="skip embeddings (lexical search only)")
    a = ap.parse_args()

    async def run():
        await init_db()
        if a.cmd == "medlineplus":
            terms = list(a.terms) + (a.file.read_text(encoding="utf-8").split("\n") if a.file else [])
            terms = [t.strip() for t in terms if t.strip()]
            if not terms:
                sys.exit("Give topics, e.g. diabetes hypertension dengue")
            await medlineplus(terms, not a.no_embed)
        elif a.cmd in WEB:
            await WEB[a.cmd](not a.no_embed, a.fetch_only)
        elif a.cmd == "reembed":
            await reembed()
        else:
            await pdf_dir(a.folder, a.source, a.collection, not a.no_embed)

    asyncio.run(run())


if __name__ == "__main__":
    main()
