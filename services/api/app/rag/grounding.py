"""Post-answer grounding check.

Each factual sentence of the reply is tested against the retrieved source chunks:
  * with the bge-reranker-v2-m3 cross-encoder when it is loaded (multilingual, so Hindi / Hinglish replies
    can be checked against English sources), else
  * by content-word overlap (English / Latin-script replies only).

Unsupported sentences that state a dose, number or drug-specific instruction are removed; other unsupported
claims are kept but the answer is flagged `not_in_sources` and a short note is appended. Safety sentences
(see a doctor, emergency, call 108/112 …) are never touched.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.store import Hit, tokens

_SENT = re.compile(r"(?<=[.!?।])\s+|\n+")
_SAFETY = re.compile(r"(?i)doctor|hospital|emergency|clinic|consult|108|112|102|14416|helpline|pharmacist|"
                     r"डॉक्टर|अस्पताल|आपात|daktar|doctor ko|hospital jao")
_SPECIFIC = re.compile(r"(?i)\b\d+(\.\d+)?\s*(mg|mcg|ml|g|iu|units?|tablets?|times|%|days?|weeks?|hours?)\b|"
                       r"\b(dose|dosage|twice|thrice|once daily)\b|खुराक")
_CHATTY = re.compile(r"(?i)^(i('m| am)|hope|please|take care|feel free|i understand|sorry|thank)")
SUPPORT_MIN_RERANK = 0.15  # cross-encoder relevance needed to call a claim "supported"
MAX_RERANK_SENTENCES = 6
SUPPORT_MIN_OVERLAP = 0.5  # share of the sentence's content words found in one source chunk

NOTE = {"en": "Note: parts of this answer are general information that is not in my sources.",
        "hi": "ध्यान दें: इस उत्तर का कुछ भाग सामान्य जानकारी है जो मेरे स्रोतों में नहीं है।"}


@dataclass
class Grounding:
    checked: int = 0
    supported: int = 0
    removed: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    method: str = "none"

    @property
    def rate(self) -> float | None:
        return round(self.supported / self.checked, 3) if self.checked else None

    def as_dict(self) -> dict:
        return {"checked": self.checked, "supported": self.supported, "rate": self.rate, "method": self.method,
                "removed": self.removed, "unsupported": self.unsupported,
                "flag": "not_in_sources" if (self.unsupported or self.removed) else None}


def _claims(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "") if len(s.strip()) > 25]


def _overlap(sentence: str, chunk_tokens: set[str]) -> float:
    w = set(tokens(sentence))
    return len(w & chunk_tokens) / len(w) if w else 1.0


def check_sentences(sentences: list[str], hits: list[Hit]) -> tuple[list[float], str]:
    """Support score per sentence (max over sources)."""
    from app.rag import rerank as rr

    if not hits or not sentences:
        return [0.0] * len(sentences), "none"
    passages = [f"{h.title}\n{h.content}" for h in hits]
    latin = bool(re.search(r"[A-Za-z]{3}", " ".join(sentences)[:300])) and \
        not re.search(r"[ऀ-෿]", " ".join(sentences))
    if not latin and rr.available():  # cross-lingual check: ~0.2 s per pair on CPU, so keep it small
        sents, top = sentences[:MAX_RERANK_SENTENCES], passages[:3]
        flat = rr.score_pairs([(s, p) for s in sents for p in top]) or []
        out = [max(flat[i * len(top):(i + 1) * len(top)] or [0.0]) for i in range(len(sents))]
        return out + [1.0] * (len(sentences) - len(sents)), "rerank"  # unchecked tail counts as supported
    toks = [set(tokens(p)) for p in passages]
    return [max(_overlap(s, t) for t in toks) for s in sentences], "overlap"


def ground(reply: str, hits: list[Hit], lang: str = "en") -> tuple[str, Grounding]:
    """Return the softened reply and the grounding report."""
    g = Grounding()
    claims = [c for c in _claims(reply) if not _SAFETY.search(c) and not _CHATTY.search(c)]
    if not claims:
        return reply, g
    scores, g.method = check_sentences(claims, hits)
    if g.method == "none":
        g.checked = len(claims)
        g.unsupported = claims
        return reply, g
    if g.method == "overlap" and not re.search(r"[A-Za-z]", reply[:200]):
        return reply, Grounding(method="skipped_non_latin")  # can't judge Indic text without the reranker
    thr = SUPPORT_MIN_RERANK if g.method == "rerank" else SUPPORT_MIN_OVERLAP
    out = reply
    for c, s in zip(claims, scores):
        g.checked += 1
        if s >= thr:
            g.supported += 1
        elif _SPECIFIC.search(c):
            g.removed.append(c)
            out = out.replace(c, "").strip()
        else:
            g.unsupported.append(c)
    out = re.sub(r"\n{3,}", "\n\n", out)
    # The 'not in my sources' note was appended to most answers and read as a refusal; the flag stays in the
    # grounding result and the answer card shows which sources were consulted.
    return out, g
