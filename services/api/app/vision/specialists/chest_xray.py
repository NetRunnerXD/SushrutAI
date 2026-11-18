"""Chest X-ray screening: TorchXRayVision DenseNet-121 "all" (Cohen et al.; code Apache-2.0, weights trained on
NIH, PadChest, CheXpert, MIMIC-CXR, Google, OpenI and RSNA-Kaggle data -> research use; see docs/licence-register.md).
18 findings, calibrated so 0.5 is the model's own operating point (`op_threshs`). Runs on the CPU in ~0.2 s.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from app.vision.specialists.base import Finding, Specialist, register

# model label -> (plain English, plain Hindi); worded as possibilities for a doctor to confirm
PLAIN: dict[str, tuple[str, str]] = {
    "Atelectasis": ("part of a lung may not be fully open", "फेफड़े का कुछ हिस्सा पूरा न खुला हो सकता है"),
    "Consolidation": ("a dense patch in a lung, as can happen with an infection", "फेफड़े में घना धब्बा, जैसा संक्रमण में हो सकता है"),
    "Infiltration": ("hazy patches in the lungs", "फेफड़ों में धुंधले धब्बे"),
    "Pneumothorax": ("possible air around a lung (a collapsed lung) — show this to a doctor soon",
                     "फेफड़े के आसपास हवा हो सकती है (फेफड़ा सिकुड़ना) — जल्दी डॉक्टर को दिखाएँ"),
    "Edema": ("possible fluid in the lungs", "फेफड़ों में पानी हो सकता है"),
    "Emphysema": ("signs of over-inflated lungs", "फेफड़े ज़्यादा फूले हुए होने के संकेत"),
    "Fibrosis": ("possible scarring in the lungs", "फेफड़ों में निशान (फाइब्रोसिस) हो सकते हैं"),
    "Effusion": ("possible fluid around the lungs", "फेफड़ों के आसपास पानी हो सकता है"),
    "Pneumonia": ("patterns that can be seen with a lung infection (pneumonia)", "फेफड़ों के संक्रमण (निमोनिया) जैसे निशान"),
    "Pleural_Thickening": ("thickening of the lining around a lung", "फेफड़े की झिल्ली मोटी हो सकती है"),
    "Cardiomegaly": ("the heart may look larger than usual", "दिल सामान्य से बड़ा दिख सकता है"),
    "Nodule": ("a small spot in a lung", "फेफड़े में एक छोटा धब्बा"),
    "Mass": ("a larger spot or lump in a lung", "फेफड़े में एक बड़ा धब्बा या गाँठ"),
    "Hernia": ("part of the stomach may be pushing up into the chest", "पेट का कुछ हिस्सा छाती की ओर उठा हो सकता है"),
    "Lung Lesion": ("an unusual area in a lung", "फेफड़े में एक असामान्य हिस्सा"),
    "Fracture": ("a possible rib or bone fracture", "पसली या हड्डी टूटी हो सकती है"),
    "Lung Opacity": ("an area of a lung that looks less clear than usual", "फेफड़े का एक हिस्सा सामान्य से कम साफ़ दिखता है"),
    "Enlarged Cardiomediastinum": ("the middle of the chest looks wider than usual", "छाती का बीच का हिस्सा सामान्य से चौड़ा दिखता है"),
}
FLAG_AT = 0.5  # the model's own operating point: far too eager on real films (see THRESH)
# Which input the classifier scores (eval/specialist_eval.py cxr_scores + offline analysis, Kermany 624 films):
#   "centre" = frame/labels trimmed, then the centred square: AUC 0.922 (the earlier untrimmed centre: 0.920)
#   "frame"  = whole trimmed film padded to a square: AUC 0.911;   "lungs" = lung crop: AUC 0.899
# The "where the model looked" map is always made on the lung crop, where it localised best.
CLASSIFY_VIEW = "centre"
LUNG = ("Pneumonia", "Consolidation", "Lung Opacity", "Infiltration", "Atelectasis")

# What is shown, and when (eval/specialist_eval.py cxr_calibrate + eval/reports/specialists.json).
# Thresholds were set on half of the Kermany films (normals: lung <= 15% flagged, others <= 1%) and checked on the
# other half. Trimmed-centre input (2026-11-22): normal films flagged 7.7%, pneumonia films 77% (AUC 0.922);
# MedGemma alone mentions an abnormality on 30% of held-out normal films. The other findings
# (mass, nodule, fracture, hernia...) were flagged on up to 59% of normal children's films at the model's default
# and are not shown at all.
LUNG_AT = 0.705
SHOWN_AT = {"Effusion": 0.548, "Cardiomegaly": 0.666, "Edema": 0.41, "Pneumothorax": 0.559}
# "likely": a stricter level reached by only 1.7% of held-out normal films (q0.99 of calibration normals);
# only the lung finding has labels to validate it — the others are only ever "possible".
LIKELY_AT = {"Lung": 0.830}
LUNG_PLAIN = ("parts of the lungs look hazier or denser than usual — this can be seen with an infection such as pneumonia",
              "फेफड़ों के कुछ हिस्से सामान्य से ज़्यादा धुंधले या घने दिखते हैं — ऐसा निमोनिया जैसे संक्रमण में हो सकता है")


class ChestXray(Specialist):
    def load(self):
        import torch
        import torchxrayvision as xrv

        torch.set_num_threads(4)
        m = xrv.models.DenseNet(weights="densenet121-res224-all")
        m.eval()
        return m

    # ------------------------------------------------------------------ input: the lungs, not the frame
    def prepare(self, img: Image.Image) -> dict:
        """Trim a white/black frame, find the lungs and heart (anatomy.py), and take a square around them (+ margin)
        as the model's input. Without anatomy, the whole trimmed film padded to a square (never cut at the sides).
        -> {"x": 1x1x224x224 array, "box": square region in `img` coordinates (may extend past the edges, padded
        black), "masks": {"lungs", "heart"} on the 224 grid or None, "size": img.size}. Cached for the same image."""
        key = id(img)
        if self._state.get("prep_key") == key:
            return self._state["prep"]
        from app.vision.specialists.anatomy import ANATOMY, trim_frame

        trimmed, (tl, tt, tr, tb) = trim_frame(img)
        m = ANATOMY.masks(trimmed)
        if m is not None:
            ys, xs = np.nonzero(m["lungs"] | m["heart"])
            x0, x1, y0, y1 = xs.min() + tl, xs.max() + tl, ys.min() + tt, ys.max() + tt
            side = max(x1 - x0, y1 - y0) * 1.10  # 5% margin each side
        else:
            x0, x1, y0, y1 = tl, tr, tt, tb
            side = max(x1 - x0, y1 - y0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        side = int(round(side))
        l0, t0 = int(round(cx - side / 2)), int(round(cy - side / 2))
        box = (l0, t0, l0 + side, t0 + side)  # exactly square
        g = img.convert("L").crop(box).resize((224, 224), Image.BILINEAR)  # outside the image -> black
        x = ((np.asarray(g, dtype=np.float32) / 255.0) * 2048.0 - 1024.0)[None, None]
        masks = None
        if m is not None:
            def grid(a: np.ndarray) -> np.ndarray:
                full = Image.new("L", img.size, 0)
                full.paste(Image.fromarray((a * 255).astype(np.uint8)), (tl, tt))
                return np.asarray(full.crop(box).resize((224, 224), Image.NEAREST)) > 127
            masks = {"lungs": grid(m["lungs"]), "heart": grid(m["heart"])}
        # the whole film without its frame, padded to a square (nothing cut): the classifier's other candidate input
        fs = max(tr - tl, tb - tt)
        fl0, ft0 = int(round((tl + tr) / 2 - fs / 2)), int(round((tt + tb) / 2 - fs / 2))
        gf = img.convert("L").crop((fl0, ft0, fl0 + fs, ft0 + fs)).resize((224, 224), Image.BILINEAR)
        x_frame = ((np.asarray(gf, dtype=np.float32) / 255.0) * 2048.0 - 1024.0)[None, None]
        cs = min(tr - tl, tb - tt)  # the centred square of the trimmed film (the classifier's input)
        cl0, ct0 = tl + (tr - tl - cs) // 2, tt + (tb - tt - cs) // 2
        gc = img.convert("L").crop((cl0, ct0, cl0 + cs, ct0 + cs)).resize((224, 224), Image.BILINEAR)
        x_centre = ((np.asarray(gc, dtype=np.float32) / 255.0) * 2048.0 - 1024.0)[None, None]
        prep = {"x": x, "box": box, "masks": masks, "size": img.size, "x_frame": x_frame, "x_centre": x_centre}
        self._state.update(prep_key=key, prep=prep)
        return prep

    def scores(self, model, img: Image.Image, view: str | None = None) -> dict[str, float]:
        """Finding scores. view "centre" | "frame" | "lungs" (see CLASSIFY_VIEW, the default)."""
        import torch

        prep = self.prepare(img)
        x = {"frame": prep["x_frame"], "centre": prep["x_centre"]}.get(view or CLASSIFY_VIEW, prep["x"])
        with torch.no_grad():
            out = model(torch.from_numpy(x))[0].numpy()
        return {p: float(v) for p, v in zip(model.pathologies, out) if p}

    def predict(self, model, img: Image.Image) -> list[Finding]:
        """One combined lung finding plus four findings that matter; nothing else is reported."""
        s = self.scores(model, img)
        lung = max(s[k] for k in LUNG)
        band = lambda key, v, at: None if v < at else ("likely" if v >= LIKELY_AT.get(key, 9) else "possible")  # noqa: E731
        out = [Finding("Lung", *LUNG_PLAIN, score=round(lung, 3), flagged=lung >= LUNG_AT, band=band("Lung", lung, LUNG_AT))]
        out += [Finding(k, *PLAIN[k], score=round(s[k], 3), flagged=s[k] >= t, band=band(k, s[k], t))
                for k, t in SHOWN_AT.items()]
        return out

    # ------------------------------------------------------------------ where the model looked
    @staticmethod
    def _gradcam(model, x, idx: int) -> np.ndarray:
        import torch
        import torch.nn.functional as F

        feats = model.features(torch.from_numpy(x)).detach().requires_grad_(True)
        logits = model.classifier(F.adaptive_avg_pool2d(F.relu(feats), 1).flatten(1))
        (grad,) = torch.autograd.grad(logits[0, idx], feats)
        cam = F.relu((grad.mean(dim=(2, 3), keepdim=True) * feats).sum(1))[0].detach().numpy()
        return np.asarray(Image.fromarray(cam.astype(np.float32), mode="F").resize((224, 224), Image.BICUBIC)).clip(0)

    @staticmethod
    def _occlusion(model, x, idx: int, patch: int = 32, stride: int = 16, batch: int = 32) -> np.ndarray:
        """Hide one patch at a time (filled with the film's mean) and record how much the finding's score drops."""
        import torch

        xt = torch.from_numpy(x)
        with torch.no_grad():
            base = float(model(xt)[0, idx])
            pos = [(i, j) for i in range(0, 224 - patch + 1, stride) for j in range(0, 224 - patch + 1, stride)]
            fill = float(xt.mean())
            heat, count = np.zeros((224, 224), np.float32), np.zeros((224, 224), np.float32)
            for k in range(0, len(pos), batch):
                chunk = pos[k:k + batch]
                b = xt.repeat(len(chunk), 1, 1, 1)
                for n, (i, j) in enumerate(chunk):
                    b[n, :, i:i + patch, j:j + patch] = fill
                drop = (base - model(b)[:, idx].numpy()).clip(0)
                for n, (i, j) in enumerate(chunk):
                    heat[i:i + patch, j:j + patch] += drop[n]
                    count[i:i + patch, j:j + patch] += 1
        heat = heat / np.maximum(count, 1)
        if heat.max() > 0:  # smooth the patch grid so the bands follow shapes, not squares
            u8 = Image.fromarray((heat / heat.max() * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(patch / 4))
            heat = np.asarray(u8, dtype=np.float32) / 255.0
        return heat

    def explain(self, img: Image.Image, key: str, method: str = "occlusion"):
        """-> (heat over the whole uploaded image, 0..1, shape (H, W); lung outline mask (H, W) or None) or None.
        The heat is kept only inside the anatomy the finding is about (the heart for an enlarged heart, the lungs
        otherwise), so the frame, text, neck and shoulders are never coloured. Approximate: shown as "where the
        model looked", not as an outline of disease. method "gradcam_full" = the earlier behaviour (eval only)."""
        if not self.available():
            return None
        model = self._state["model"]
        if key == "Lung":  # mapped through consolidation: it localised best on radiologist-drawn lesions (69%;
            key = "Consolidation"  # atelectasis 0%) in eval/specialist_eval.py loc
        idx = list(model.pathologies).index(key)
        W, H = img.size
        outline = None
        if key == "Cardiomegaly" and method != "gradcam_full":
            # the region of an enlarged heart is the heart itself: its outline from the anatomy model
            prep = self.prepare(img)
            if prep["masks"] is None:
                return None
            heart = prep["masks"]["heart"].astype(np.float32) * 0.75  # one band (orange) + outline
            return (_to_full(heart, prep["box"], (W, H)),
                    _to_full(prep["masks"]["lungs"].astype(np.float32), prep["box"], (W, H)) > 0.5)
        if method == "gradcam_full":  # earlier behaviour: centre square of the raw image, no anatomy
            side = min(W, H)
            box = ((W - side) // 2, (H - side) // 2, (W - side) // 2 + side, (H - side) // 2 + side)
            g = img.convert("L").crop(box).resize((224, 224), Image.BILINEAR)
            x = ((np.asarray(g, dtype=np.float32) / 255.0) * 2048.0 - 1024.0)[None, None]
            heat = self._gradcam(model, x, idx)
        else:
            prep = self.prepare(img)
            x, box = prep["x"], prep["box"]
            heat = self._occlusion(model, x, idx) if method == "occlusion" else self._gradcam(model, x, idx)
            if prep["masks"] is not None:
                region = prep["masks"]["heart"] if key == "Cardiomegaly" else _dilate(prep["masks"]["lungs"], 6)
                heat = heat * region
                outline = prep["masks"]["lungs"]
        if heat.max() <= 0:
            return None
        full = _to_full(heat / heat.max(), box, (W, H))
        return full, (_to_full(outline.astype(np.float32), box, (W, H)) > 0.5 if outline is not None else None)


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    return np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(2 * r + 1))) > 127


def _to_full(a: np.ndarray, box: tuple[int, int, int, int], size: tuple[int, int]) -> np.ndarray:
    """A 224x224 map over `box` -> the whole image (zeros outside the box)."""
    l, t, r, b = box
    side = r - l
    big = np.asarray(Image.fromarray(a.astype(np.float32), mode="F").resize((side, side), Image.BILINEAR))
    W, H = size
    out = np.zeros((H, W), np.float32)
    x0, y0, x1, y1 = max(l, 0), max(t, 0), min(r, W), min(b, H)
    if x1 > x0 and y1 > y0:
        out[y0:y1, x0:x1] = big[y0 - t:y1 - t, x0 - l:x1 - l]
    return out


CHEST_XRAY = register(ChestXray(name="chest X-ray screening model", model_id="torchxrayvision densenet121-res224-all",
                                scan_types=("chest_xray",), licence="Apache-2.0 code; research-use weights"))
