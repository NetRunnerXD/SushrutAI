"""Where the lungs and heart are on a chest X-ray, so the screening model can be pointed at them.

1. `trim_frame` removes a near-uniform white or black frame (paper margins of a scanned figure, black bars), so the
   frame is never fed to the model or coloured in the overlay.
2. `ChestAnatomy` runs TorchXRayVision's ChestX-Det PSPNet (anatomy segmentation: lungs, heart, clavicles...; CPU,
   512 px, ~273 MB weights) and returns boolean masks in the trimmed image's coordinates. Fails open: if the
   weights are missing or the result is implausible, callers fall back to the whole trimmed film.
"""
from __future__ import annotations

import logging
import threading

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)


def trim_frame(img: Image.Image, std_max: float = 8.0, tol: int = 22) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Crop away border rows/columns that are almost uniformly white or black. -> (cropped, box in img)."""
    g = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = g.shape

    def flat(v: np.ndarray) -> bool:
        return v.std() < std_max and (v.mean() > 255 - tol or v.mean() < tol)

    top, bottom, left, right = 0, h, 0, w
    while top < bottom - 32 and flat(g[top, left:right]):
        top += 1
    while bottom > top + 32 and flat(g[bottom - 1, left:right]):
        bottom -= 1
    while left < right - 32 and flat(g[top:bottom, left]):
        left += 1
    while right > left + 32 and flat(g[top:bottom, right - 1]):
        right -= 1
    box = (left, top, right, bottom)
    return (img.crop(box) if box != (0, 0, w, h) else img), box


def pad_square(g: np.ndarray, fill: float = 0.0) -> tuple[np.ndarray, int, int]:
    """Pad a grey array to a square (X-ray background is black). -> (square, dx, dy)."""
    h, w = g.shape
    side = max(h, w)
    out = np.full((side, side), fill, dtype=g.dtype)
    dx, dy = (side - w) // 2, (side - h) // 2
    out[dy:dy + h, dx:dx + w] = g
    return out, dx, dy


class ChestAnatomy:
    _lock = threading.Lock()

    def __init__(self):
        self._state: dict = {}

    def available(self) -> bool:
        if "model" not in self._state and "error" not in self._state:
            with self._lock:
                if "model" not in self._state and "error" not in self._state:
                    try:
                        from torchxrayvision.baseline_models.chestx_det import PSPNet

                        self._state["model"] = PSPNet()
                    except Exception as e:  # noqa: BLE001 - optional component
                        log.warning("chest anatomy model unavailable: %s", e)
                        self._state["error"] = str(e)
        return "model" in self._state

    def masks(self, img: Image.Image) -> dict[str, np.ndarray] | None:
        """Boolean masks (same size as `img`) for "lungs", "left_lung", "right_lung", "heart"; None if unavailable
        or implausible (e.g. not a frontal chest film)."""
        if not self.available():
            return None
        import torch

        m = self._state["model"]
        g = np.asarray(img.convert("L"), dtype=np.float32)
        sq, dx, dy = pad_square(g)
        x = torch.from_numpy(sq / 255.0 * 2048.0 - 1024.0)[None, None]
        with torch.no_grad():
            y = torch.sigmoid(m(x))[0].numpy() > 0.5
        side = sq.shape[0]

        def back(name: str) -> np.ndarray:
            a = Image.fromarray((y[m.targets.index(name)] * 255).astype(np.uint8)).resize((side, side), Image.NEAREST)
            return np.asarray(a)[dy:dy + g.shape[0], dx:dx + g.shape[1]] > 127

        left, right, heart = back("Left Lung"), back("Right Lung"), back("Heart")
        lungs = left | right
        # plausible frontal film: both lungs present, together covering a sensible share of the image
        if left.mean() < 0.03 or right.mean() < 0.03 or not (0.10 <= lungs.mean() <= 0.75):
            return None
        return {"lungs": lungs, "left_lung": left, "right_lung": right, "heart": heart}


ANATOMY = ChestAnatomy()
