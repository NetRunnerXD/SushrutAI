"""Dedicated "screening" models that look at one kind of scan before MedGemma describes it.

Each specialist runs on the CPU (the GPU stays free for MedGemma), is loaded on first use, and fails open: if its
package or weights are missing it is simply skipped. A specialist is only registered after it passed the gate in
eval/specialist_eval.py on independent public data (see eval/reports/specialists.json).
Findings are screening hints for MedGemma and for the person's doctor: never a diagnosis.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PIL import Image

log = logging.getLogger(__name__)


@dataclass
class Finding:
    key: str            # model's own label, e.g. "Effusion"
    plain_en: str       # what a non-expert understands: "possible fluid around the lungs"
    plain_hi: str
    score: float        # model output on its operating scale (0.5 = the model's chosen threshold)
    flagged: bool
    band: str | None = None  # "possible" | "likely" (validated on data) when flagged

    def plain(self, lang: str) -> str:
        return self.plain_hi if lang == "hi" else self.plain_en


@dataclass
class Specialist:
    name: str                       # shown to the person: "chest X-ray screening model"
    model_id: str                   # for logs, the licence register and the response
    scan_types: tuple[str, ...]     # from app/ocr/check.py SCAN_TYPES (+ "skin")
    licence: str
    _state: dict = field(default_factory=dict, repr=False)

    def load(self):  # -> model or None
        raise NotImplementedError

    def predict(self, model, img: Image.Image) -> list[Finding]:
        raise NotImplementedError

    def available(self) -> bool:
        if "model" not in self._state and "error" not in self._state:
            try:
                self._state["model"] = self.load()
            except Exception as e:  # noqa: BLE001 - optional component
                log.warning("%s unavailable: %s", self.model_id, e)
                self._state["error"] = str(e)
        return self._state.get("model") is not None

    def run(self, img: Image.Image) -> list[Finding] | None:
        if not self.available():
            return None
        return self.predict(self._state["model"], img)

    def explain(self, img: Image.Image, key: str):
        """Where the model looked for a finding: (heat map 0..1 as a 2-D array, (left, top, right, bottom) box of the
        original image it covers), or None if this specialist can't explain."""
        return None


_REGISTRY: list[Specialist] = []


def register(s: Specialist) -> Specialist:
    _REGISTRY.append(s)
    return s


def for_scan_type(scan_type: str | None) -> Specialist | None:
    for s in _REGISTRY:
        if scan_type in s.scan_types:
            return s
    return None
