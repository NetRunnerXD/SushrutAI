/**
 * Patient card image, drawn on a canvas in the browser (the person's data never leaves the device).
 * Two styles: "family" (what a relative or responder needs) and "doctor" (adds readings and results).
 * Layout runs twice: once to measure the height, once to paint. Colours follow Steel & Spice (DESIGN.md);
 * the card is always light, because it is a document that gets forwarded and printed.
 */
import qrcode from "qrcode-generator";

export type CardStyle = "family" | "doctor";

export type CardData = {
  name: string;
  line: string;              // "38 yrs · Female · Mother"
  bloodGroup: string | null;
  allergies: string[];
  conditions: string[];
  medicines: { name: string; detail: string }[];
  contact: string | null;
  pregnancy: string | null;  // already worded, or null
  vitals: { label: string; value: string; date: string }[];
  labs: { test: string; value: string; flag: "H" | "L"; detail: string }[];
  date: string;              // "21 Nov 2026"
  spice: string;             // the person's spice colour (hex)
};

export type CardLabels = {
  title: string; titleDoctor: string; bloodGroup: string; bloodUnknown: string; allergies: string; noAllergies: string;
  conditions: string; medicines: string; contact: string; emergencyCall: string; readings: string; labs: string;
  high: string; low: string; none: string; more: string; madeBy: string; note: string; scan: string;
};

const C = {
  bg: "#E4E8EB", panel: "#FFFFFF", text: "#182028", muted: "#4C5762", border: "#C3CBD2", well: "#EEF1F3",
  rail: "#232B33", railText: "#EEF1F3", railMuted: "#B1BBC4", danger: "#A5221A", dangerSoft: "#F6DDD9",
  warn: "#874800", warnSoft: "#F5E2C0", emergency: "#B3121B", brand: "#5B2A7A",
};

const W = 1080;          // logical width (WhatsApp-friendly)
const PANEL_X = 36;      // panel inset from the image edge
const PAD = 56;          // text inset inside the panel
const X0 = PANEL_X + PAD;
const X1 = W - PANEL_X - PAD;
const MAX_ITEMS = 12;

type Fonts = { sans: string; display: string };

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
}

/** Wrap text to a width; long words are kept whole (names, Indic words). */
function wrap(ctx: CanvasRenderingContext2D, text: string, width: number): string[] {
  const out: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const tryLine = line ? `${line} ${word}` : word;
    if (line && ctx.measureText(tryLine).width > width) { out.push(line); line = word; } else line = tryLine;
  }
  if (line) out.push(line);
  return out.length ? out : [""];
}

class Painter {
  y = 0;
  constructor(public ctx: CanvasRenderingContext2D, public paint: boolean, public f: Fonts, public scale = 1) {}
  font(size: number, weight = 400, display = false) {
    this.ctx.font = `${weight} ${size}px ${display ? this.f.display : this.f.sans}`;
  }
  /** Draw wrapped text at x, starting at this.y (top); returns the lines' height. */
  text(s: string, x: number, width: number, size: number, opts: { weight?: number; color?: string; display?: boolean;
    lh?: number; maxLines?: number } = {}) {
    const { weight = 400, color = C.text, display = false, lh = 1.3, maxLines = 99 } = opts;
    this.font(size, weight, display);
    let lines = wrap(this.ctx, s, width);
    if (lines.length > maxLines) {
      lines = lines.slice(0, maxLines);
      lines[maxLines - 1] = `${lines[maxLines - 1].replace(/\s*\S*$/, "")} …`;
    }
    if (this.paint) {
      this.ctx.fillStyle = color;
      this.ctx.textBaseline = "top";
      lines.forEach((l, i) => this.ctx.fillText(l, x, this.y + i * size * lh + size * (lh - 1) / 2));
    }
    const h = lines.length * size * lh;
    this.y += h;
    return h;
  }
  heading(s: string, color = C.muted) {
    if (this.paint) {
      this.ctx.fillStyle = color === C.muted ? C.brand : color;
      roundRect(this.ctx, X0, this.y + 6, 8, 26, 4);
      this.ctx.fill();
    }
    this.text(s.toUpperCase(), X0 + 22, X1 - X0 - 22, 25, { weight: 700, color, lh: 1.5 });
    this.y += 12;
  }
  rule() {
    this.y += 30;
    if (this.paint) { this.ctx.fillStyle = C.border; this.ctx.fillRect(X0, this.y, X1 - X0, 2); }
    this.y += 32;
  }
}

function header(p: Painter, d: CardData, style: CardStyle, L: CardLabels) {
  const { ctx } = p;
  const top = PANEL_X;
  const badge = 172;
  const textW = X1 - X0 - badge - 32;
  // measure the text block first, so the band fits it
  const start = top + 52;
  p.y = start;
  const measure = new Painter(ctx, false, p.f);
  measure.y = start;
  measure.text((style === "doctor" ? L.titleDoctor : L.title).toUpperCase(), X0, textW, 24, { weight: 700, lh: 1.4 });
  measure.y += 6;
  measure.text(d.name, X0, textW, 60, { weight: 800, display: true, lh: 1.12, maxLines: 2 });
  measure.y += 8;
  if (d.line) measure.text(d.line, X0, textW, 31, { lh: 1.35 });
  const bottom = Math.max(measure.y, start + badge) + 52;
  if (p.paint) {
    ctx.save();
    roundRect(ctx, PANEL_X, top, W - 2 * PANEL_X, bottom - top + 36, 40);
    ctx.clip();
    ctx.fillStyle = C.rail;
    ctx.fillRect(PANEL_X, top, W - 2 * PANEL_X, bottom - top);
    ctx.fillStyle = d.spice;                       // the person's spice colour, as on their avatar rim
    ctx.fillRect(PANEL_X, top, 14, bottom - top);
    ctx.fillRect(PANEL_X, bottom - 8, W - 2 * PANEL_X, 8);
    ctx.restore();
  }
  p.text((style === "doctor" ? L.titleDoctor : L.title).toUpperCase(), X0, textW, 24, { weight: 700, color: C.railMuted, lh: 1.4 });
  p.y += 6;
  p.text(d.name, X0, textW, 60, { weight: 800, color: C.railText, display: true, lh: 1.12, maxLines: 2 });
  p.y += 8;
  if (d.line) p.text(d.line, X0, textW, 31, { color: C.railMuted, lh: 1.35 });
  // blood-group badge
  const cx = X1 - badge / 2;
  const cy = start + badge / 2;
  if (p.paint) {
    ctx.beginPath();
    ctx.arc(cx, cy, badge / 2, 0, Math.PI * 2);
    ctx.fillStyle = d.bloodGroup ? C.emergency : "#3A444D";
    ctx.fill();
    ctx.lineWidth = 6;
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.stroke();
    ctx.fillStyle = "#FFFFFF";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    p.font(d.bloodGroup ? 64 : 56, 800, true);
    ctx.fillText(d.bloodGroup ?? "?", cx, cy - 12);
    p.font(20, 700);
    const sub = d.bloodGroup ? L.bloodGroup : L.bloodUnknown;
    const lines = wrap(ctx, sub, badge - 36).slice(0, 2);
    lines.forEach((l, i) => ctx.fillText(l, cx, cy + 40 + i * 22 - (lines.length - 1) * 11));
    ctx.textAlign = "left";
  }
  p.y = bottom + 44;
}

function allergyBox(p: Painter, d: CardData, L: CardLabels) {
  const { ctx } = p;
  const has = d.allergies.length > 0;
  const top = p.y;
  const inner = new Painter(ctx, false, p.f);
  inner.y = top + 26;
  inner.text(L.allergies.toUpperCase(), X0 + 30, X1 - X0 - 60, 25, { weight: 700, lh: 1.5 });
  inner.y += 4;
  inner.text(has ? d.allergies.join(", ") : L.noAllergies, X0 + 30, X1 - X0 - 60, has ? 38 : 31, { weight: has ? 800 : 400, lh: 1.3 });
  const h = inner.y - top + 26;
  if (p.paint) {
    roundRect(ctx, X0, top, X1 - X0, h, 24);
    ctx.fillStyle = has ? C.dangerSoft : C.well;
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = has ? C.danger : C.border;
    ctx.stroke();
  }
  p.y = top + 26;
  p.text(L.allergies.toUpperCase(), X0 + 30, X1 - X0 - 60, 25, { weight: 700, color: has ? C.danger : C.muted, lh: 1.5 });
  p.y += 4;
  p.text(has ? d.allergies.join(", ") : L.noAllergies, X0 + 30, X1 - X0 - 60, has ? 38 : 31,
    { weight: has ? 800 : 400, color: has ? C.danger : C.muted, lh: 1.3 });
  p.y = top + h + 40;
}

function chips(p: Painter, items: string[], L: CardLabels) {
  const { ctx } = p;
  if (!items.length) { p.text(L.none, X0, X1 - X0, 30, { color: C.muted }); return; }
  const shown = items.slice(0, MAX_ITEMS);
  if (items.length > shown.length) shown.push(`+${items.length - shown.length} ${L.more}`);
  const size = 30, hpad = 26, h = 62, gap = 14;
  p.font(size, 600);
  let x = X0;
  let y = p.y;
  for (const s of shown) {
    const label = ctx.measureText(s).width > X1 - X0 - 2 * hpad ? wrap(ctx, s, X1 - X0 - 2 * hpad)[0] + " …" : s;
    const w = Math.min(ctx.measureText(label).width + 2 * hpad, X1 - X0);
    if (x + w > X1) { x = X0; y += h + gap; }
    if (p.paint) {
      roundRect(ctx, x, y, w, h, h / 2);
      ctx.fillStyle = C.well;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = C.border;
      ctx.stroke();
      p.font(size, 600);
      ctx.fillStyle = C.text;
      ctx.textBaseline = "middle";
      ctx.fillText(label, x + hpad, y + h / 2 + 1);
    }
    x += w + gap;
  }
  p.y = y + h;
}

function medicines(p: Painter, d: CardData, L: CardLabels) {
  const { ctx } = p;
  if (!d.medicines.length) { p.text(L.none, X0, X1 - X0, 30, { color: C.muted }); return; }
  const shown = d.medicines.slice(0, MAX_ITEMS);
  shown.forEach((m, i) => {
    if (i > 0) {
      p.y += 16;
      if (p.paint) { ctx.fillStyle = C.border; ctx.fillRect(X0 + 34, p.y, X1 - X0 - 34, 1.5); }
      p.y += 16;
    }
    const top = p.y;
    if (p.paint) {
      ctx.beginPath();
      ctx.arc(X0 + 10, top + 22, 9, 0, Math.PI * 2);
      ctx.fillStyle = d.spice;
      ctx.fill();
    }
    p.text(m.name, X0 + 34, X1 - X0 - 34, 34, { weight: 700, lh: 1.3 });
    if (m.detail) p.text(m.detail, X0 + 34, X1 - X0 - 34, 28, { color: C.muted, lh: 1.35 });
  });
  if (d.medicines.length > shown.length) {
    p.y += 14;
    p.text(`+${d.medicines.length - shown.length} ${L.more}`, X0 + 34, X1 - X0 - 34, 28, { weight: 600, color: C.muted });
  }
}

function contact(p: Painter, d: CardData, L: CardLabels) {
  p.text(d.contact || L.none, X0, X1 - X0, d.contact ? 38 : 30, { weight: d.contact ? 700 : 400, color: d.contact ? C.text : C.muted });
  p.y += 8;
  p.text(L.emergencyCall, X0, X1 - X0, 28, { weight: 700, color: C.emergency });
}

function vitals(p: Painter, d: CardData, L: CardLabels) {
  const { ctx } = p;
  if (!d.vitals.length) { p.text(L.none, X0, X1 - X0, 30, { color: C.muted }); return; }
  const gap = 20, cols = 2;
  const w = (X1 - X0 - gap) / cols, h = 150;
  d.vitals.slice(0, 8).forEach((v, i) => {
    const x = X0 + (i % cols) * (w + gap);
    const y = p.y + Math.floor(i / cols) * (h + gap);
    if (p.paint) {
      roundRect(ctx, x, y, w, h, 22);
      ctx.fillStyle = C.well;
      ctx.fill();
      ctx.textBaseline = "top";
      ctx.fillStyle = C.muted;
      p.font(24, 600);
      ctx.fillText(wrap(ctx, v.label, w - 48)[0], x + 24, y + 20);
      ctx.fillStyle = C.text;
      p.font(42, 800, true);
      ctx.fillText(wrap(ctx, v.value, w - 48)[0], x + 24, y + 54);
      ctx.fillStyle = C.muted;
      p.font(22);
      ctx.fillText(v.date, x + 24, y + 110);
    }
  });
  const rows = Math.ceil(Math.min(d.vitals.length, 8) / cols);
  p.y += rows * h + (rows - 1) * gap;
}

function labs(p: Painter, d: CardData, L: CardLabels) {
  const { ctx } = p;
  if (!d.labs.length) { p.text(L.none, X0, X1 - X0, 30, { color: C.muted }); return; }
  const shown = d.labs.slice(0, MAX_ITEMS);
  shown.forEach((l, i) => {
    if (i > 0) p.y += 22;
    const flagText = l.flag === "H" ? `↑ ${L.high}` : `↓ ${L.low}`;
    p.font(28, 800);
    const fw = ctx.measureText(flagText).width + 32;
    const top = p.y;
    if (p.paint) {
      roundRect(ctx, X1 - fw, top + 2, fw, 46, 23);
      ctx.fillStyle = l.flag === "H" ? C.dangerSoft : C.warnSoft;
      ctx.fill();
      ctx.fillStyle = l.flag === "H" ? C.danger : C.warn;
      ctx.textBaseline = "middle";
      ctx.fillText(flagText, X1 - fw + 16, top + 26);
    }
    p.text(`${l.test}  ${l.value}`, X0, X1 - X0 - fw - 20, 32, { weight: 700, lh: 1.3 });
    if (l.detail) p.text(l.detail, X0, X1 - X0 - fw - 20, 26, { color: C.muted, lh: 1.35 });
    p.y = Math.max(p.y, top + 50);
  });
  if (d.labs.length > shown.length) {
    p.y += 12;
    p.text(`+${d.labs.length - shown.length} ${L.more}`, X0, X1 - X0, 28, { weight: 600, color: C.muted });
  }
}

/** UTF-8 text cut to a byte budget on a character boundary, so the QR stays scannable from a phone screen. */
export function clip(text: string, maxBytes = 450): string {
  const enc = new TextEncoder();
  if (enc.encode(text).length <= maxBytes) return text;
  let out = "";
  for (const ch of text) {
    if (enc.encode(out + ch + " …").length > maxBytes) break;
    out += ch;
  }
  return out + " …";
}

let utf8Ready = false;
export function qrMatrix(text: string): boolean[][] {
  if (!utf8Ready) { qrcode.stringToBytes = qrcode.stringToBytesFuncs["UTF-8"]; utf8Ready = true; }
  const q = qrcode(0, "M");
  q.addData(clip(text), "Byte");
  q.make();
  const n = q.getModuleCount();
  return Array.from({ length: n }, (_, r) => Array.from({ length: n }, (_, c) => q.isDark(r, c)));
}

/** The essentials only, kept short so the QR stays a small, easily scanned code (version ~13). */
export function qrText(d: CardData, L: CardLabels): string {
  return [[d.name, d.line].filter(Boolean).join(", "), `${L.bloodGroup}: ${d.bloodGroup ?? "—"}`,
    `${L.allergies}: ${d.allergies.join(", ") || "—"}`, `${L.conditions}: ${d.conditions.join(", ") || "—"}`,
    `${L.medicines}: ${d.medicines.map((m) => m.name).join(", ") || "—"}`, `${L.contact}: ${d.contact || "—"}`,
    d.pregnancy ?? "", `SushrutAI · ${d.date}`].filter(Boolean).join("\n");
}

/** The same facts as plain text: the text share. */
export function cardText(d: CardData, L: CardLabels, style: CardStyle): string {
  const out = [`SushrutAI — ${style === "doctor" ? L.titleDoctor : L.title}`, [d.name, d.line].filter(Boolean).join(", "),
    `${L.bloodGroup}: ${d.bloodGroup ?? "—"}`,
    `${L.allergies}: ${d.allergies.join(", ") || L.noAllergies}`,
    `${L.conditions}: ${d.conditions.join(", ") || L.none}`,
    `${L.medicines}: ${d.medicines.map((m) => [m.name, m.detail].filter(Boolean).join(" ")).join("; ") || L.none}`,
    `${L.contact}: ${d.contact || "—"}`];
  if (d.pregnancy) out.push(d.pregnancy);
  if (style === "doctor") {
    if (d.vitals.length) out.push(`${L.readings}: ${d.vitals.map((v) => `${v.label} ${v.value}`).join("; ")}`);
    if (d.labs.length) out.push(`${L.labs}: ${d.labs.map((l) => `${l.test} ${l.value} ${l.flag === "H" ? L.high : L.low}`).join("; ")}`);
  }
  out.push(d.date);
  return out.join("\n");
}

function footer(p: Painter, d: CardData, style: CardStyle, L: CardLabels) {
  const { ctx } = p;
  const size = 280;
  const top = p.y;
  const textW = X1 - X0 - size - 40;
  p.text(`${L.madeBy} · ${d.date}`, X0, textW, 27, { weight: 700, lh: 1.4 });
  p.y += 8;
  p.text(L.note, X0, textW, 24, { color: C.muted, lh: 1.45 });
  const textBottom = p.y;
  const qx = X1 - size;
  if (p.paint) {
    const m = qrMatrix(qrText(d, L));
    const n = m.length;
    roundRect(ctx, qx, top, size, size, 16);
    ctx.fillStyle = "#FFFFFF";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = C.border;
    ctx.stroke();
    // modules on whole device pixels (no blur between them), centred, with a 4-module quiet zone
    const k = p.scale;
    const cellPx = Math.floor((size * k) / (n + 8));
    const off = Math.round((size * k - cellPx * n) / 2);
    const x0 = Math.round(qx * k) + off, y0 = Math.round(top * k) + off;
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = "#000000";
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      if (m[r][c]) ctx.fillRect(x0 + c * cellPx, y0 + r * cellPx, cellPx, cellPx);
    }
    ctx.restore();
    p.font(21, 600);
    ctx.fillStyle = C.muted;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    ctx.fillText(L.scan, qx + size / 2, top + size + 10);
    ctx.textAlign = "left";
  }
  p.y = Math.max(textBottom, top + size + 40);
}

function layout(p: Painter, d: CardData, style: CardStyle, L: CardLabels) {
  header(p, d, style, L);
  if (d.pregnancy) {
    p.text(d.pregnancy, X0, X1 - X0, 32, { weight: 800, color: C.warn });
    p.y += 24;
  }
  allergyBox(p, d, L);
  p.heading(L.conditions);
  chips(p, d.conditions, L);
  p.rule();
  p.heading(L.medicines);
  medicines(p, d, L);
  p.rule();
  if (style === "doctor") {
    p.heading(L.readings);
    vitals(p, d, L);
    p.rule();
    p.heading(L.labs);
    labs(p, d, L);
    p.rule();
  }
  p.heading(L.contact, C.emergency);
  contact(p, d, L);
  p.rule();
  footer(p, d, style, L);
  p.y += PAD - 10;
}

/** Draw the card onto `canvas` (resized to fit) at `scale`x for crisp text. */
export function drawPatientCard(canvas: HTMLCanvasElement, d: CardData, style: CardStyle, L: CardLabels,
                                fonts: Fonts, scale = 2) {
  const ctx = canvas.getContext("2d")!;
  const measure = new Painter(ctx, false, fonts);
  layout(measure, d, style, L);
  const H = Math.ceil(measure.y + PANEL_X);
  canvas.width = W * scale;
  canvas.height = H * scale;
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, W, H);
  ctx.save();
  ctx.shadowColor = "rgba(24,32,40,0.18)";
  ctx.shadowBlur = 24;
  ctx.shadowOffsetY = 6;
  roundRect(ctx, PANEL_X, PANEL_X, W - 2 * PANEL_X, H - 2 * PANEL_X, 40);
  ctx.fillStyle = C.panel;
  ctx.fill();
  ctx.restore();
  layout(new Painter(ctx, true, fonts, scale), d, style, L);
}

/** Font stacks the page actually uses, and make sure every glyph on the card is loaded before drawing. */
export async function cardFonts(sample: string): Promise<Fonts> {
  const sans = getComputedStyle(document.body).fontFamily;
  const probe = document.createElement("span");
  probe.className = "display";
  probe.style.position = "absolute";
  probe.style.visibility = "hidden";
  document.body.appendChild(probe);
  const display = getComputedStyle(probe).fontFamily || sans;
  probe.remove();
  const text = sample + " 0123456789 ↑↓·…+";
  try {
    await Promise.all([400, 600, 700, 800].flatMap((w) => [
      document.fonts.load(`${w} 32px ${sans}`, text), document.fonts.load(`${w} 32px ${display}`, text)]));
  } catch { /* fall back to whatever is loaded */ }
  return { sans, display };
}
