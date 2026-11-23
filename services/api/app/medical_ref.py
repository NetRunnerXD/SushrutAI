"""Optional, cached non-blocking external clinical & drug reference helpers.

Never blocks real-time voice turns and degrades gracefully when offline or rate-limited.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("sushrut.medical_ref")

_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}
_TTL_S = 1800.0
INDIA_RXNAV_CAVEAT = (
    "US NLM RxNav reference only — exact Indian brand names, strengths, and availability must be "
    "verified with a registered doctor or pharmacist."
)


def _get_cached(kind: str, key: str) -> Any | None:
    item = _CACHE.get((kind, key.lower()))
    if item and time.monotonic() < item[0]:
        return item[1]
    return None


def _set_cached(kind: str, key: str, value: Any) -> Any:
    if len(_CACHE) > 256:
        _CACHE.clear()
    _CACHE[(kind, key.lower())] = (time.monotonic() + _TTL_S, value)
    return value


async def lookup_icd10(term: str, *, limit: int = 5) -> list[dict[str, str]]:
    """Lookup standardized ICD-10 diagnosis codes via NLM ClinicalTables (no auth needed)."""
    q = (term or "").strip()
    if len(q) < 2:
        return []
    if (cached := _get_cached("icd10", q)) is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            r = await c.get(
                "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search",
                params={"sf": "code,name", "terms": q, "maxList": str(min(max(limit, 1), 10))},
            )
            r.raise_for_status()
            data = r.json()
            rows = data[3] if isinstance(data, list) and len(data) > 3 and isinstance(data[3], list) else []
            now = datetime.now(timezone.utc).isoformat()
            out = [{"code": str(row[0]), "name": str(row[1]), "source": "NLM/WHO ICD-10-CM", "retrieved_at": now}
                   for row in rows if isinstance(row, list) and len(row) >= 2]
            return _set_cached("icd10", q, out)
    except Exception as e:  # noqa: BLE001
        log.debug("ICD-10 lookup skipped (%s)", e)
        return []


async def lookup_rxnav(drug_name: str) -> dict[str, Any]:
    """Lookup RxNorm concept info via NLM RxNav with explicit India availability caveat."""
    q = (drug_name or "").strip()
    if len(q) < 2:
        return {"query": q, "concepts": [], "caveat": INDIA_RXNAV_CAVEAT}
    if (cached := _get_cached("rxnav", q)) is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            r = await c.get(
                "https://rxnav.nlm.nih.gov/REST/approximateTerm.json",
                params={"term": q, "maxEntries": "5", "option": "1"},
            )
            r.raise_for_status()
            cands = (r.json().get("approximateGroup") or {}).get("candidate") or []
            concepts: list[dict[str, str]] = []
            seen: set[str] = set()
            for cand in cands:
                rxcui = str(cand.get("rxcui") or "")
                if not rxcui or rxcui in seen:
                    continue
                seen.add(rxcui)
                concepts.append({
                    "rxcui": rxcui,
                    "name": str(cand.get("name") or q),
                    "score": str(cand.get("score") or ""),
                })
            out = {"query": q, "concepts": concepts[:5], "source": "NIH NLM RxNav", "caveat": INDIA_RXNAV_CAVEAT}
            return _set_cached("rxnav", q, out)
    except Exception as e:  # noqa: BLE001
        log.debug("RxNav lookup skipped (%s)", e)
        return {"query": q, "concepts": [], "source": "NIH NLM RxNav", "caveat": INDIA_RXNAV_CAVEAT}


async def lookup_medlineplus(term: str, *, icd_code: str | None = None) -> list[dict[str, str]]:
    """Lookup patient-friendly health topic summaries via MedlinePlus Connect Web Service."""
    q = (icd_code or term or "").strip()
    if len(q) < 2:
        return []
    if (cached := _get_cached("medlineplus", q)) is not None:
        return cached
    try:
        params = {
            "mainSearchCriteria.v.cs": "2.16.840.1.113883.6.90" if icd_code else "2.16.840.1.113883.6.103",
            "mainSearchCriteria.v.dn": q,
            "knowledgeResponseType": "application/json",
        }
        if icd_code:
            params["mainSearchCriteria.v.c"] = icd_code
        async with httpx.AsyncClient(timeout=2.5) as c:
            r = await c.get("https://connect.medlineplus.gov/service", params=params)
            r.raise_for_status()
            entries = ((r.json().get("feed") or {}).get("entry")) or []
            out = []
            for e in entries[:3]:
                title = (e.get("title") or {}).get("_value") or ""
                link_list = e.get("link") or []
                href = link_list[0].get("href") if link_list and isinstance(link_list[0], dict) else ""
                if title:
                    out.append({
                        "title": title,
                        "url": href or "https://medlineplus.gov/",
                        "source": "NIH MedlinePlus",
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    })
            return _set_cached("medlineplus", q, out)
    except Exception as e:  # noqa: BLE001
        log.debug("MedlinePlus lookup skipped (%s)", e)
        return []


async def lookup_pubmed(term: str, *, limit: int = 3) -> list[dict[str, str]]:
    """Lookup peer-reviewed medical literature summaries via NCBI E-utilities (PubMed, public free tier)."""
    q = (term or "").strip()
    if len(q) < 3:
        return []
    if (cached := _get_cached("pubmed", q)) is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            es = await c.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pubmed", "term": q, "retmax": str(min(max(limit, 1), 5)), "retmode": "json"},
            )
            es.raise_for_status()
            ids = ((es.json().get("esearchresult") or {}).get("idlist")) or []
            if not ids:
                return _set_cached("pubmed", q, [])
            sm = await c.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
            )
            sm.raise_for_status()
            res = sm.json().get("result") or {}
            now = datetime.now(timezone.utc).isoformat()
            out: list[dict[str, str]] = []
            for pid in ids:
                item = res.get(pid) or {}
                title = str(item.get("title") or "").strip()
                if title:
                    out.append({
                        "pmid": str(pid),
                        "title": title,
                        "journal": str(item.get("fulljournalname") or item.get("source") or ""),
                        "pubdate": str(item.get("pubdate") or ""),
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                        "source": "NCBI PubMed",
                        "retrieved_at": now,
                    })
            return _set_cached("pubmed", q, out)
    except Exception as e:  # noqa: BLE001
        log.debug("PubMed lookup skipped (%s)", e)
        return []


def to_fhir_condition_bundle(
    *,
    symptom_summary: str,
    department: str,
    specialist: str,
    qualification: str,
    urgency_level: str,
    recommended_tests: list[dict[str, Any]] | None = None,
    icd_codes: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Serialize non-PII clinical routing & recommended investigations into an HL7 FHIR R4 Bundle."""
    now = datetime.now(timezone.utc).isoformat()
    entries: list[dict[str, Any]] = [
        {
            "resource": {
                "resourceType": "Condition",
                "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
                "code": {
                    "text": symptom_summary,
                    "coding": [
                        {"system": "http://hl7.org/fhir/sid/icd-10-cm", "code": c["code"], "display": c["name"]}
                        for c in (icd_codes or [])[:3] if c.get("code")
                    ],
                },
                "recordedDate": now,
            }
        },
        {
            "resource": {
                "resourceType": "ServiceRequest",
                "status": "draft",
                "intent": "proposal",
                "priority": "stat" if urgency_level == "emergency" else "routine",
                "category": [{"text": department}],
                "code": {"text": f"{specialist} ({qualification}) consultation"},
                "authoredOn": now,
            }
        },
    ]
    for test in (recommended_tests or [])[:4]:
        entries.append({
            "resource": {
                "resourceType": "ServiceRequest",
                "status": "draft",
                "intent": "proposal",
                "code": {"text": str(test.get("test_name") or test.get("investigation_name") or "")},
                "note": [{"text": str(test.get("reason") or test.get("why_recommended") or "")}],
            }
        })
    return {"resourceType": "Bundle", "type": "collection", "timestamp": now, "entry": entries}
