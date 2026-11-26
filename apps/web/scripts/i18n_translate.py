"""Machine-translate the interface texts each language lacks, with IndicTrans2 (local, MIT), into
lib/mt/<lang>.json. Hand-written translations (messages.ts, messages-core.ts) always win over these.

Run from the repo root (after `node apps/web/scripts/i18n-extract.mjs`):
  services/api/.venv/Scripts/python apps/web/scripts/i18n_translate.py [--langs ta,pa] [--sample 12]
Per-language results are cached in apps/web/scripts/i18n-cache/<lang>.json, so a stopped run resumes."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "services" / "api"))
from app.speech import models as M  # noqa: E402

FLORES = {k: v[0] for k, v in M.LANGS.items()}
# Kept exactly as written: placeholders, the app's name, phone numbers and common abbreviations.
PROTECT = re.compile(r"\{\w+\}|SushrutAI|Tele-MANAS|eSanjeevani|ABHA|ASHA|ANM|ORS|PDF|BP|108|112|14416|102|Ctrl\+\w|⌘K|\bOK\b")
TOKEN0 = 9001  # protected spans become 9001, 9002, … (numbers pass through the model unchanged)
NATIVE_RANGE = {"mr": (0x0900, 0x097F), "hi": (0x0900, 0x097F), "bn": (0x0980, 0x09FF), "as": (0x0980, 0x09FF), "pa": (0x0A00, 0x0A7F), "gu": (0x0A80, 0x0AFF),
                "or": (0x0B00, 0x0B7F), "ta": (0x0B80, 0x0BFF), "te": (0x0C00, 0x0C7F), "kn": (0x0C80, 0x0CFF),
                "ml": (0x0D00, 0x0D7F), "ur": (0x0600, 0x06FF)}


def protect(s: str) -> tuple[str, list[str]]:
    kept: list[str] = []

    def rep(m):
        kept.append(m.group(0))
        return str(TOKEN0 + len(kept) - 1)
    return PROTECT.sub(rep, s), kept


def restore(s: str, kept: list[str]) -> str | None:
    for i, k in enumerate(kept):
        tok = str(TOKEN0 + i)
        if tok not in s:
            return None  # a placeholder was lost: do not use this translation
        s = s.replace(tok, k, 1)
    return s


def tidy(src: str, out: str, lang: str) -> str:
    out = re.sub(r"\s+([।.,!?:;)])", r"\1", out).replace("( ", "(").strip()
    out = re.sub(r"\s+۔", "۔", out)
    if not re.search(r"[.!?…]$", src.strip()):  # a label: no sentence-ending mark added
        out = re.sub(r"\s*[।.۔]$", "", out)
    if src.endswith("…") and not out.endswith("…"):
        out = re.sub(r"[।.۔]$", "", out) + "…"
    return out


def native_ok(s: str, lang: str) -> bool:
    lo, hi = NATIVE_RANGE[lang]
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return True
    native = sum(1 for c in letters if lo <= ord(c) <= hi or (lang == "ur" and 0xFB50 <= ord(c) <= 0xFEFF))
    return native / len(letters) >= 0.6


def run(lang: str, items: dict[str, str], batch: int, beams: int) -> dict[str, str]:
    import torch

    cache_f = HERE / "i18n-cache" / f"{lang}.json"
    cache_f.parent.mkdir(exist_ok=True)
    done: dict[str, str | None] = json.loads(cache_f.read_text("utf-8")) if cache_f.exists() else {}
    # similar lengths per batch: less padding, and the output cap below fits every text in the batch
    todo = sorted(((k, v) for k, v in items.items() if k not in done), key=lambda kv: len(kv[1]))
    tok, model = M._it2("en-indic")
    t0, total = time.time(), len(todo)
    for i in range(0, total, batch):
        chunk = todo[i:i + batch]
        prot = [protect(v) for _, v in chunk]
        enc = tok([f"eng_Latn {FLORES[lang]} {p}" for p, _ in prot], return_tensors="pt", padding=True,
                  truncation=True, max_length=256)
        with torch.no_grad():
            # an output much longer than the input is a runaway loop: cap it (and keep each batch fast)
            cap = min(256, int(enc["input_ids"].shape[1] * 2.2) + 12)
            out = model.generate(**enc, num_beams=beams, max_length=cap, repetition_penalty=1.1)
        for (k, src), (_, kept), raw in zip(chunk, prot, tok.batch_decode(out, skip_special_tokens=True)):
            txt = M.from_devanagari(raw, lang) if lang != "ur" else raw
            txt = restore(txt, kept) if txt is not None else None
            done[k] = tidy(src, txt, lang) if txt and native_ok(txt, lang) else None
        cache_f.write_text(json.dumps(done, ensure_ascii=False, indent=0), "utf-8")
        n = min(i + batch, total)
        rate = (time.time() - t0) / max(n, 1)
        print(f"  {lang}: {n}/{total}  ({rate * (total - n) / 60:.1f} min left)", flush=True)
    return {k: v for k, v in done.items() if v and k in items}


KEEP_AS_IS = re.compile(r"^(?:[A-Z]{2,5}|SushrutAI|Ctrl\+\w|⌘K)$")  # "SOS", "BP", "ORS": everyone reads these as written
EN_SRC: dict[str, str] = {}


def nest(flat: dict[str, str], arrays: set[str]) -> dict:
    root: dict = {}
    for k, v in flat.items():
        if KEEP_AS_IS.match(EN_SRC.get(k, "").strip()):
            continue  # falls back to the English text
        parts = k.split(".")
        cur = root
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v
    return root


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", default="bn,ta,te,mr,gu,kn,ml,or,pa,as,ur")
    ap.add_argument("--sample", type=int, default=0, help="translate only N texts per language and print them")
    ap.add_argument("--batch", type=int, default=24)
    ap.add_argument("--beams", type=int, default=3)
    ap.add_argument("--write", action="store_true", help="only write lib/mt/*.json from what is cached so far")
    a = ap.parse_args()
    data = json.loads((HERE / "i18n-todo.json").read_text("utf-8"))
    EN_SRC.update(data["en"])
    langs = a.langs.split(",")
    if a.sample:
        import torch
        tok, model = M._it2("en-indic")
        keys = list(data["en"])[:: max(1, len(data["en"]) // a.sample)][: a.sample]
        for lang in langs:
            prot = [protect(data["en"][k]) for k in keys]
            enc = tok([f"eng_Latn {FLORES[lang]} {p}" for p, _ in prot], return_tensors="pt", padding=True)
            with torch.no_grad():
                out = model.generate(**enc, num_beams=a.beams, max_length=256)
            for k, (_, kept), raw in zip(keys, prot, tok.batch_decode(out, skip_special_tokens=True)):
                txt = M.from_devanagari(raw, lang) if lang != "ur" else raw
                txt = restore(txt, kept) if txt is not None else None
                ok = bool(txt) and native_ok(txt, lang)
                print(f"{lang} {'ok ' if ok else 'BAD'} {data['en'][k]!r} -> {tidy(data['en'][k], txt, lang) if txt else None!r}")
        return
    out: dict[str, dict] = {}
    for n, lang in enumerate([] if a.write else langs, 1):
        print(f"[{n}/{len(langs)}] {lang}: {len(data['todo'][lang])} texts", flush=True)
        got = run(lang, data["todo"][lang], a.batch, a.beams)
        print(f"  {lang}: {len(got)} translated, {len(data['todo'][lang]) - len(got)} kept in English", flush=True)
        out[lang] = nest(got, set(data["arrays"]))
    # merge with any languages translated in earlier runs
    for f in (HERE / "i18n-cache").glob("*.json"):
        lang = f.stem
        if lang not in out and lang in data["todo"]:
            cached = json.loads(f.read_text("utf-8"))
            out[lang] = nest({k: v for k, v in cached.items() if v and k in data["todo"][lang]}, set(data["arrays"]))
    # one file per language, loaded only when that language is chosen (lib/i18n.tsx imports it on demand)
    mt_dir = HERE.parent / "lib" / "mt"
    mt_dir.mkdir(exist_ok=True)
    for lang, tree in out.items():
        (mt_dir / f"{lang}.json").write_text(json.dumps(tree, ensure_ascii=False, separators=(",", ":")), "utf-8")
    (mt_dir / "README.md").write_text(
        "GENERATED by scripts/i18n_translate.py (IndicTrans2 machine translation) — do not edit by hand.\n"
        "Draft quality: hand-written translations in ../messages.ts and ../messages-core.ts override these.\n"
        "Native-speaker review is pending.\n", "utf-8")
    print(f"wrote lib/mt/*.json ({', '.join(sorted(out))})", flush=True)


if __name__ == "__main__":
    main()
