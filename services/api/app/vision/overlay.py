"""Draw a screening model's heat map over the original scan as three discrete bands (more / much more / most model
attention) plus an outline around the strongest area, so it reads at a glance on a phone. The heat only exists
inside the relevant anatomy (chest_xray.explain), so the frame, burned-in text, neck and shoulders stay clean;
a faint lung outline gives orientation. Colours follow DESIGN.md's status scale (amber -> red).
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image, ImageFilter

# lower edge of each band (heat map normalised 0..1), fill colour and opacity
BANDS = [(0.5, (226, 167, 42), 0.22), (0.7, (217, 89, 38), 0.34), (0.85, (192, 38, 28), 0.48)]
OUTLINE_AT = 0.7
LUNG_EDGE = (147, 185, 239)  # link-indigo, light: orientation only
MAX_SIDE = 720


def _edge(mask: np.ndarray, outer: int = 5, inner: int = 3) -> np.ndarray:
    m = Image.fromarray((mask * 255).astype(np.uint8))
    return np.asarray(m.filter(ImageFilter.MaxFilter(outer))) > np.asarray(m.filter(ImageFilter.MinFilter(inner)))


def render(img: Image.Image, heat: np.ndarray, outline: np.ndarray | None = None) -> str:
    """`heat`: 0..1 over the whole image (H, W). -> data URL (JPEG) of the scan with the banded heat map."""
    base = img.convert("L").convert("RGB")
    s = min(1.0, MAX_SIDE / max(base.size))
    size = (max(1, round(base.width * s)), max(1, round(base.height * s)))
    base = base.resize(size, Image.BILINEAR)
    h = np.asarray(Image.fromarray(heat.astype(np.float32), mode="F").resize(size, Image.BILINEAR)).clip(0, 1)

    layer = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    if outline is not None:
        o = np.asarray(Image.fromarray((outline * 255).astype(np.uint8)).resize(size, Image.NEAREST)) > 127
        e = _edge(o, 3, 1)
        layer[e, :3] = LUNG_EDGE
        layer[e, 3] = 150
    for lo, rgb, alpha in BANDS:
        m = h >= lo
        layer[m, :3] = rgb
        layer[m, 3] = round(alpha * 255)
    e = _edge(h >= OUTLINE_AT)
    layer[e, :3] = BANDS[-1][1]
    layer[e, 3] = 235

    out = Image.alpha_composite(base.convert("RGBA"), Image.fromarray(layer, mode="RGBA")).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=86)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
