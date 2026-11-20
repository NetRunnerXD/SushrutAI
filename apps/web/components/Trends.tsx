"use client";

import { ArrowDown, ArrowRight, ArrowUp, Check } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { fill, useI18n } from "@/lib/i18n";

// ---------------------------------------------------------------- reference data
/** General adult reference ranges (shaded band). Not personal targets — the UI says so. */
export const VITAL_META: Record<string, { label: string; unit: string; lo?: number; hi?: number }> = {
  bp_sys: { label: "BP — upper (systolic)", unit: "mmHg", lo: 90, hi: 129 },
  bp_dia: { label: "BP — lower (diastolic)", unit: "mmHg", lo: 60, hi: 84 },
  glucose_f: { label: "Fasting sugar", unit: "mg/dL", lo: 70, hi: 99 },
  glucose_pp: { label: "Sugar after meal", unit: "mg/dL", lo: 70, hi: 139 },
  glucose_r: { label: "Random sugar", unit: "mg/dL", lo: 70, hi: 139 },
  hr: { label: "Pulse", unit: "bpm", lo: 60, hi: 100 },
  spo2: { label: "Oxygen (SpO₂)", unit: "%", lo: 95, hi: 100 },
  temp: { label: "Temperature", unit: "°C", lo: 36.1, hi: 37.2 },
  weight: { label: "Weight", unit: "kg" },
  rr: { label: "Breathing rate", unit: "/min", lo: 12, hi: 20 },
  waist: { label: "Waist", unit: "cm" },
};

export type Status = "low" | "normal" | "high" | null;
export function statusOf(v: number | null | undefined, lo?: number | null, hi?: number | null): Status {
  if (v == null) return null;
  if (hi != null && v > hi) return "high";
  if (lo != null && v < lo) return "low";
  return lo != null || hi != null ? "normal" : null;
}

export type Trend = "up" | "down" | "flat" | null;
/** Latest vs the mean of up to 3 earlier values; within ±3% is steady. */
export function trendOf(values: number[]): Trend {
  if (values.length < 2) return null;
  const last = values[values.length - 1];
  const prev = values.slice(-4, -1);
  const base = prev.reduce((a, b) => a + b, 0) / prev.length;
  if (!base) return null;
  const d = (last - base) / Math.abs(base);
  return d > 0.03 ? "up" : d < -0.03 ? "down" : "flat";
}

export function StatusTag({ status }: { status: Status }) {
  const { t } = useI18n();
  if (!status) return null;
  const cfg = {
    high: { cls: "bg-danger-soft text-danger", Icon: ArrowUp, label: t.flagHigh ?? "High" },
    low: { cls: "bg-warn-soft text-warn", Icon: ArrowDown, label: t.flagLow ?? "Low" },
    normal: { cls: "bg-ok-soft text-ok", Icon: Check, label: t.flagNormal ?? "Normal" },
  }[status];
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.8125rem] font-semibold ${cfg.cls}`}>
      <cfg.Icon aria-hidden className="h-3.5 w-3.5" />{cfg.label}
    </span>
  );
}

export function TrendWord({ trend }: { trend: Trend }) {
  const { t } = useI18n();
  if (!trend) return null;
  const Icon = trend === "up" ? ArrowUp : trend === "down" ? ArrowDown : ArrowRight;
  return (
    <span className="inline-flex items-center gap-1 text-[0.875rem] text-muted">
      <Icon aria-hidden className="h-4 w-4" />{trend === "up" ? t.trendUp : trend === "down" ? t.trendDown : t.trendFlat}
    </span>
  );
}

// ---------------------------------------------------------------- chart
export type Point = { t: number; v: number };
export type Series = { name: string; color: "series-1" | "series-2"; points: Point[]; lo?: number; hi?: number };

const fmtDate = (ms: number) => new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" });
const fmtNum = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : String(+v.toFixed(1)));

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    setW(Math.floor(ref.current.getBoundingClientRect().width)); // first paint, even before the observer reports
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/**
 * Line chart for one or two series over time (one y-axis, same unit). Shaded band = usual range.
 * Hover / tap / arrow keys move a crosshair with a tooltip; a table of every reading sits underneath.
 */
export function TrendChart({ title, unit, series, height = 170 }: { title: string; unit: string; series: Series[]; height?: number }) {
  const { t } = useI18n();
  const [box, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const tableId = useId();
  const two = series.length > 1;

  // x positions are shared timestamps across series (BP pairs share a time)
  const times = Array.from(new Set(series.flatMap((s) => s.points.map((p) => p.t)))).sort((a, b) => a - b);
  const vals = series.flatMap((s) => [...s.points.map((p) => p.v), s.lo, s.hi].filter((x): x is number => x != null));
  const vmin = Math.min(...vals), vmax = Math.max(...vals);
  const pad = (vmax - vmin || Math.abs(vmax) || 1) * 0.12;
  const y0 = vmin - pad, y1 = vmax + pad;

  const L = 40, R = two ? 44 : 14, T = 10, B = 24;
  const W = Math.max(width, 200), H = height;
  const pw = W - L - R, ph = H - T - B;
  const tmin = times[0], tmax = times[times.length - 1];
  const x = (ms: number) => L + (times.length === 1 ? pw / 2 : ((ms - tmin) / (tmax - tmin || 1)) * pw);
  const y = (v: number) => T + (1 - (v - y0) / (y1 - y0)) * ph;
  const ticks = [y0 + (y1 - y0) * 0.15, (y0 + y1) / 2, y1 - (y1 - y0) * 0.15];

  const nearest = (px: number) => {
    let best = 0;
    times.forEach((ms, i) => { if (Math.abs(x(ms) - px) < Math.abs(x(times[best]) - px)) best = i; });
    return best;
  };
  const onMove = (e: PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setActive(nearest(e.clientX - r.left));
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      setActive((a) => Math.min(times.length - 1, Math.max(0, (a ?? times.length - 1) + (e.key === "ArrowRight" ? 1 : -1))));
    } else if (e.key === "Escape") setActive(null);
  };

  const latest = series.map((s) => s.points[s.points.length - 1]);
  const summary = `${title}: ${fill(t.readings, { n: times.length })}. ${t.latest} ` +
    series.map((s, i) => `${two ? s.name + " " : ""}${fmtNum(latest[i].v)} ${unit}`).join(", ") + ".";
  const at = active != null ? times[active] : null;

  return (
    <div className="space-y-2">
      {two && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[0.875rem]" aria-label="Legend">
          {series.map((s) => (
            <li key={s.name} className="flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-0.5 w-5 rounded-full" style={{ background: `var(--${s.color})` }} />
              {s.name}
            </li>
          ))}
        </ul>
      )}
      <div ref={box} className="relative" onPointerLeave={() => setActive(null)}>
        {width > 0 && (
          <svg width={W} height={H} role="img" aria-label={summary} tabIndex={0} onKeyDown={onKey}
               onPointerMove={onMove} onPointerDown={onMove} className="touch-pan-y rounded-[12px] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-info">
            {/* usual-range bands */}
            {series.map((s) => s.lo != null && s.hi != null && (
              <rect key={`band-${s.name}`} x={L} width={pw} y={y(Math.min(s.hi, y1))} height={Math.max(0, y(Math.max(s.lo, y0)) - y(Math.min(s.hi, y1)))}
                    fill="var(--ok-soft)" opacity={two ? 0.7 : 1} />
            ))}
            {/* recessive grid + y ticks */}
            {ticks.map((v) => (
              <g key={v}>
                <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="var(--border)" strokeWidth={1} />
                <text x={L - 6} y={y(v) + 4} textAnchor="end" fontSize={11} fill="var(--muted)">{fmtNum(v)}</text>
              </g>
            ))}
            {/* x labels: first and last date */}
            <text x={x(tmin)} y={H - 6} textAnchor={times.length === 1 ? "middle" : "start"} fontSize={11} fill="var(--muted)">{fmtDate(tmin)}</text>
            {times.length > 1 && <text x={x(tmax)} y={H - 6} textAnchor="end" fontSize={11} fill="var(--muted)">{fmtDate(tmax)}</text>}
            {/* crosshair */}
            {at != null && <line x1={x(at)} x2={x(at)} y1={T} y2={T + ph} stroke="var(--muted)" strokeWidth={1} strokeDasharray="3 3" />}
            {series.map((s) => {
              const d = s.points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
              const last = s.points[s.points.length - 1];
              return (
                <g key={s.name}>
                  <path d={d} fill="none" stroke={`var(--${s.color})`} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                  {s.points.map((p) => (
                    <circle key={p.t} cx={x(p.t)} cy={y(p.v)} r={p.t === at || p === last ? 5 : 3.5}
                            fill={`var(--${s.color})`} stroke="var(--surface)" strokeWidth={2} />
                  ))}
                  {two && <text x={x(last.t) + 8} y={y(last.v) + 4} fontSize={11} fill="var(--text)">{s.name.split(" ")[0]}</text>}
                </g>
              );
            })}
          </svg>
        )}
        {at != null && (
          <div role="status" className="pointer-events-none absolute top-0 z-10 min-w-28 rounded-[12px] border border-border bg-surface px-2.5 py-1.5 text-[0.8125rem] shadow-[var(--e2)]"
               style={{ left: Math.min(Math.max(0, x(at) - 60), W - 140) }}>
            <p className="font-semibold">{new Date(at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</p>
            {series.map((s) => {
              const p = s.points.find((q) => q.t === at);
              return p ? <p key={s.name} className="flex items-center gap-1.5">
                <span aria-hidden className="inline-block h-2 w-2 rounded-full" style={{ background: `var(--${s.color})` }} />
                {two ? `${s.name.split(" ")[0]} ` : ""}<span className="font-mono font-semibold">{fmtNum(p.v)}</span> {unit}
                <StatusTag status={statusOf(p.v, s.lo, s.hi)} /></p> : null;
            })}
          </div>
        )}
      </div>
      <details className="text-[0.875rem]">
        <summary className="inline-flex min-h-12 cursor-pointer items-center font-semibold text-info underline">{t.showReadings}</summary>
        <div className="mt-1 overflow-x-auto"><table aria-describedby={tableId} className="w-full min-w-[16rem] text-left">
          <caption id={tableId} className="sr-only">{title}</caption>
          <thead><tr className="text-muted"><th className="py-1 pr-3 font-semibold">{t.date}</th>
            {series.map((s) => <th key={s.name} className="py-1 pr-3 font-semibold">{two ? s.name : t.value} ({unit})</th>)}</tr></thead>
          <tbody className="divide-y divide-border">
            {times.slice().reverse().map((ms) => (
              <tr key={ms}><td className="py-1 pr-3">{new Date(ms).toLocaleDateString()}</td>
                {series.map((s) => { const p = s.points.find((q) => q.t === ms);
                  return <td key={s.name} className="py-1 pr-3 font-mono">{p ? fmtNum(p.v) : "—"}</td>; })}</tr>
            ))}
          </tbody>
        </table></div>
      </details>
    </div>
  );
}

/** Word-sized history line for a lab test. The values are in its accessible name. */
export function Sparkline({ points, lo, hi, label }: { points: Point[]; lo?: number | null; hi?: number | null; label: string }) {
  const { t } = useI18n();
  if (points.length < 2) return null;
  const W = 84, H = 26, P = 4;
  const vals = [...points.map((p) => p.v), lo, hi].filter((v): v is number => v != null);
  const vmin = Math.min(...vals), vmax = Math.max(...vals), span = vmax - vmin || 1;
  const t0 = points[0].t, t1 = points[points.length - 1].t;
  const x = (ms: number) => P + ((ms - t0) / (t1 - t0 || 1)) * (W - 2 * P);
  const y = (v: number) => P + (1 - (v - vmin) / span) * (H - 2 * P);
  const last = points[points.length - 1];
  const name = `${label} — ${t.history}: ` + points.map((p) => `${fmtNum(p.v)} (${fmtDate(p.t)})`).join(", ");
  return (
    <svg width={W} height={H} role="img" aria-label={name} className="shrink-0">
      <title>{name}</title>
      {lo != null && hi != null && <rect x={0} width={W} y={y(hi)} height={Math.max(0, y(lo) - y(hi))} fill="var(--ok-soft)" />}
      <path d={points.map((p, i) => `${i ? "L" : "M"}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ")}
            fill="none" stroke="var(--series-1)" strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last.t)} cy={y(last.v)} r={3} fill="var(--series-1)" stroke="var(--surface)" strokeWidth={1.5} />
    </svg>
  );
}

// ---------------------------------------------------------------- lab panels
// Order matters: specific panels first ("Urine glucose" is urine, "Glycated haemoglobin" is diabetes).
const PANELS: [string, RegExp][] = [
  ["Urine", /urine|urinary|pus cell|epithelial|specific gravity|ketone/i],
  ["Diabetes", /glucose|sugar|hba1c|glycated|glycosylated|a1c|\bfbs\b|\bppbs\b|\brbs\b|\bgtt\b|\bpp ?bs\b/i],
  ["Blood count (CBC)", /h(a)?emoglobin|\bhb\b|\bhgb\b|rbc|red (blood )?cell|wbc|white (blood )?cell|leu[ck]ocyte|platelet|pcv|hematocrit|haematocrit|\bhct\b|mcv|mch|rdw|neutrophil|lymphocyte|monocyte|eosinophil|basophil|\besr\b|sedimentation/i],
  ["Cholesterol (lipids)", /cholesterol|\bldl\b|\bhdl\b|vldl|triglycer|lipid/i],
  ["Liver", /bilirubin|sgot|sgpt|\bast\b|\balt\b|alkaline phosphatase|\balp\b|\bg\.? ?g\.? ?t\.? ?p?\b|gamma|albumin|globulin|total protein|a\/g ratio/i],
  ["Kidney", /creatinine|urea|\bbun\b|uric acid|egfr|gfr/i],
  ["Salts (electrolytes)", /sodium|potassium|chloride|calcium|phosph|magnesium|bicarbonate/i],
  ["Thyroid", /\btsh\b|thyro|\bt3\b|\bt4\b|ft3|ft4/i],
  ["Vitamins & iron", /vitamin|\bb12\b|folate|folic|ferritin|\biron\b|tibc|transferrin/i],
];
export function panelOf(testName: string): string | null {
  return PANELS.find(([, re]) => re.test(testName))?.[0] ?? null;
}
export const PANEL_ORDER = ["Blood count (CBC)", "Diabetes", "Cholesterol (lipids)", "Liver", "Kidney",
  "Salts (electrolytes)", "Thyroid", "Vitamins & iron", "Urine"];
