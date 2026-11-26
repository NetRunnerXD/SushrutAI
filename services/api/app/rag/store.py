"""Hybrid retrieval over kb_chunks: dense (bge-m3 via Ollama) + lexical, fused with RRF.

SQLite dev mode does brute-force cosine in numpy (fine for tens of thousands of chunks);
Postgres prod mode should move `embedding` to pgvector HNSW (infra/sql/pgvector.sql).
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.providers import ProviderUnavailable, ollama
from app.models import KbChunk

_TOKEN = re.compile(r"[\wऀ-෿]+", re.UNICODE)  # Latin + Indic blocks
_STOP = set("the a an of and or to in is are for with on at by be as it this that from your you".split())

# Authority tier: Indian government guidance first, then labels/WHO/NLM, then literature.
AUTHORITY = {"icmr_stw": 1.0, "nhm_stg": 1.0, "ntep": 1.0, "ncvbdc": 1.0, "icmr_nin": 1.0,
             "openfda": 0.85, "medlineplus": 0.85, "who": 0.85, "nhs": 0.85, "cdc": 0.85, "pmc": 0.7}


def tokens(text: str) -> list[str]:
    return [t for t in (w.lower() for w in _TOKEN.findall(text)) if t not in _STOP and len(t) > 1]


def chunk_text(text: str, max_words: int = 220, overlap: int = 40) -> list[str]:
    """Paragraph-aware chunking (~300 tokens)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], []
    for p in paras:
        words = p.split()
        if len(cur) + len(words) > max_words and cur:
            chunks.append(" ".join(cur))
            cur = cur[-overlap:]
        while len(words) > max_words:
            chunks.append(" ".join(cur + words[:max_words]))
            cur, words = [], words[max_words - overlap:]
        cur += words
    if cur:
        chunks.append(" ".join(cur))
    return chunks


@dataclass
class Hit:
    id: str
    title: str
    content: str
    source_id: str
    source_url: str | None
    collection: str
    score: float


async def _embed(texts: list[str]) -> list[list[float]] | None:
    try:
        return await ollama().embed(get_settings().embed_model, texts)
    except ProviderUnavailable:
        return None


async def add_document(db: AsyncSession, *, collection: str, source_id: str, title: str, text: str,
                       source_url: str | None = None, lang: str = "en", breadcrumb: str | None = None,
                       version: str | None = None, embed: bool = True) -> int:
    pieces = chunk_text(text)
    hashes = [hashlib.sha256(f"{source_id}|{p}".encode()).hexdigest() for p in pieces]
    existing = set(await db.scalars(select(KbChunk.content_hash).where(KbChunk.source_id == source_id)))
    new = [(p, h) for p, h in zip(pieces, hashes) if h not in existing]
    if not new:
        return 0
    vecs = await _embed([f"{title}\n{p}" for p, _ in new]) if embed else None
    for i, (p, h) in enumerate(new):
        emb = np.asarray(vecs[i], dtype=np.float32).tobytes() if vecs else None
        db.add(KbChunk(collection=collection, source_id=source_id, source_url=source_url, lang=lang,
                       title=title, breadcrumb=breadcrumb, content=p, content_hash=h, embedding=emb,
                       version=version))
    await db.commit()
    return len(new)


class _Index:
    """In-memory copy of kb_chunks for one set of collections: metadata, BM25 statistics and a normalised
    float32 matrix. Rebuilt only when the chunk count / embedded count changes (e.g. after an ingest)."""

    def __init__(self, rows: list[KbChunk]):
        self.meta = [(r.id, r.title, r.content, r.source_id, r.source_url, r.collection) for r in rows]
        self.docs = [tokens(f"{r.title} {r.content}") for r in rows]
        self.tfs = [Counter(d) for d in self.docs]
        self.lens = [len(d) for d in self.docs]
        n = len(rows)
        self.avgdl = (sum(self.lens) / n) if n else 1.0
        self.df = Counter(t for d in self.docs for t in set(d))
        self.emb_idx = [i for i, r in enumerate(rows) if r.embedding]
        if self.emb_idx:
            m = np.stack([np.frombuffer(rows[i].embedding, dtype=np.float32) for i in self.emb_idx])
            self.matrix = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
        else:
            self.matrix = None
        self.authority = np.array([AUTHORITY.get(r.source_id.split(":", 1)[0], 0.75) for r in rows])


_CACHE: dict[tuple, tuple[tuple, _Index]] = {}


async def _index(db: AsyncSession, collections: list[str] | None) -> _Index:
    from sqlalchemy import func

    key = tuple(sorted(collections or []))
    stmt = select(func.count(KbChunk.id), func.count(KbChunk.embedding))
    if collections:
        stmt = stmt.where(KbChunk.collection.in_(collections))
    sig = tuple((await db.execute(stmt)).one())
    hit = _CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    q = select(KbChunk)
    if collections:
        q = q.where(KbChunk.collection.in_(collections))
    idx = _Index(list(await db.scalars(q)))
    _CACHE[key] = (sig, idx)
    return idx


def invalidate_cache() -> None:
    _CACHE.clear()


_SEARCH_CACHE: dict[tuple, tuple[float, list[Hit]]] = {}
_CACHE_TTL_S = 300.0


async def search(db: AsyncSession, query: str, *, collections: list[str] | None = None,
                 k: int = 6, candidates: int = 40, rerank: bool | None = None,
                 rerank_top: int = 10) -> list[Hit]:
    """Hybrid BM25 + dense (bge-m3) retrieval fused with RRF x source authority, then (if available) the
    bge-reranker-v2-m3 cross-encoder on the top `rerank_top` fused results. rerank=None -> use if loaded."""
    import time as _time
    ix = await _index(db, collections)
    n = len(ix.meta)
    if not n:
        return []
    ckey = (query.strip().lower(), tuple(sorted(collections or [])), k, rerank, rerank_top, n)
    cached = _SEARCH_CACHE.get(ckey)
    if cached and (_time.monotonic() - cached[0]) < _CACHE_TTL_S:
        return list(cached[1])

    # lexical: BM25
    q = tokens(query)
    lex = np.zeros(n)
    for t in set(q):
        if t not in ix.df:
            continue
        idf = math.log(1 + (n - ix.df[t] + 0.5) / (ix.df[t] + 0.5))
        for i, tf in enumerate(ix.tfs):
            f = tf.get(t)
            if f:
                lex[i] += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * ix.lens[i] / ix.avgdl))
    order = np.argsort(-lex)[:candidates]
    lex_rank = {int(i): r for r, i in enumerate(order) if lex[i] > 0}

    # dense
    dense_rank: dict[int, int] = {}
    if ix.matrix is not None:
        qv = await _embed([query])
        if qv:
            v = np.asarray(qv[0], dtype=np.float32)
            sims = ix.matrix @ (v / (np.linalg.norm(v) + 1e-9))
            dense_rank = {ix.emb_idx[int(j)]: r for r, j in enumerate(np.argsort(-sims)[:candidates])}

    # reciprocal rank fusion x source authority
    fused: dict[int, float] = {}
    for ranks in (lex_rank, dense_rank):
        for i, r in ranks.items():
            fused[i] = fused.get(i, 0.0) + 1.0 / (60 + r)
    for i in fused:
        fused[i] *= ix.authority[i]
    top = sorted(fused, key=lambda i: -fused[i])[:max(k, rerank_top)]

    from app.rag import rerank as rr

    use = rerank if rerank is not None else True
    if use and len(top) > 1:
        import asyncio

        scores = await asyncio.to_thread(rr.score, query, [f"{ix.meta[i][1]}\n{ix.meta[i][2]}" for i in top])
        if scores:
            final = {i: s * (0.8 + 0.2 * ix.authority[i]) for i, s in zip(top, scores)}
            best = sorted(final, key=lambda i: -final[i])[:k]
            res_hits = [Hit(*ix.meta[i], round(float(final[i]), 5)) for i in best]
            if len(_SEARCH_CACHE) > 128:
                _SEARCH_CACHE.clear()
            _SEARCH_CACHE[ckey] = (_time.monotonic(), res_hits)
            return res_hits
    best = top[:k]
    res_hits = [Hit(*ix.meta[i], round(float(fused[i]), 5)) for i in best]
    if len(_SEARCH_CACHE) > 128:
        _SEARCH_CACHE.clear()
    _SEARCH_CACHE[ckey] = (_time.monotonic(), res_hits)
    return res_hits
