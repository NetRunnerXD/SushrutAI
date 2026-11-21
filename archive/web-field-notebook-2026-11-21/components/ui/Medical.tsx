"use client";

import { ArrowDown, ArrowUp, Check, Minus, Moon, Sun, SunDim, Utensils, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useI18n } from "@/lib/i18n";

/* ---------------- chips ---------------- */
export function Chip({ icon: Icon, children, selected, onClick, className = "" }: {
  icon?: LucideIcon; children: ReactNode; selected?: boolean; onClick?: () => void; className?: string;
}) {
  return (
    <button type="button" onClick={onClick} aria-pressed={selected}
      className={`inline-flex min-h-12 shrink-0 items-center gap-2 rounded-full border-[1.5px] px-4 font-semibold transition-colors
        ${selected ? "border-brand bg-accent-soft text-text" : "border-border-input bg-surface text-text hover:bg-surface-2"} ${className}`}>
      {selected ? <Check aria-hidden className="h-5 w-5" /> : Icon ? <Icon aria-hidden className="h-5 w-5 text-brand" /> : null}
      {children}
    </button>
  );
}

/* ---------------- dose pictogram ("1-0-1") ---------------- */
type Freq = { morning?: number; afternoon?: number; night?: number; per_day?: number | null; label?: string } | null | undefined;
const fmt = (n?: number) => (n === 0.5 ? "½" : n == null ? "0" : `${n}`.replace(".5", "½"));

export function DosePictogram({ frequency, timing }: { frequency: Freq; timing?: string | null }) {
  const { t } = useI18n();
  if (!frequency || frequency.per_day == null) {
    return <p className="font-semibold">{frequency?.label ?? t.asNeeded}</p>;
  }
  const slots: [string, number | undefined, LucideIcon][] = [
    [t.morning, frequency.morning, Sun], [t.afternoon, frequency.afternoon, SunDim], [t.night, frequency.night, Moon]];
  const spoken = slots.map(([l, n]) => `${fmt(n)} ${l}`).join(", ");
  return (
    <div className="flex flex-wrap items-center gap-2" role="img" aria-label={`${spoken}${timing ? `, ${t.timing[timing] ?? timing}` : ""}`}>
      {slots.map(([label, n, Icon]) => (
        <span key={label} aria-hidden
          className={`flex min-w-16 flex-col items-center rounded-[6px] border px-2 py-1.5
            ${n ? "border-brand/40 bg-brand-soft text-brand" : "border-dashed border-border-input bg-transparent text-muted"}`}>
          <Icon className="h-5 w-5" />
          <span className="font-mono text-[1.375rem] font-bold leading-tight">{fmt(n)}</span>
          <span className="text-[0.75rem]">{label}</span>
        </span>
      ))}
      {timing && (
        <span aria-hidden className="inline-flex items-center gap-1 rounded-full bg-surface-2 px-3 py-1 text-[0.875rem] font-semibold">
          <Utensils className="h-4 w-4" />{t.timing[timing] ?? timing.replace("_", " ")}
        </span>
      )}
    </div>
  );
}

/* ---------------- lab range bar ---------------- */
export function RangeBar({ value, lo, hi, unit, flag }: {
  value: number | null; lo: number | null; hi: number | null; unit?: string | null; flag?: string | null;
}) {
  const { t } = useI18n();
  if (value == null || (lo == null && hi == null)) return null;
  const min = lo ?? 0;
  const max = hi ?? ((lo ?? 0) * 2 || 1);
  const span = max - min || 1;
  const pos = Math.max(0, Math.min(100, ((value - (min - span * 0.5)) / (span * 2)) * 100));
  const words = flag === "H" ? t.flagHigh : flag === "L" ? t.flagLow : t.flagNormal;
  return (
    <div className="w-full" role="img"
         aria-label={`${value} ${unit ?? ""}, ${words}. ${t.normalRange} ${lo ?? "—"} – ${hi ?? "—"}`}>
      <div aria-hidden className="relative h-2.5 w-full overflow-hidden rounded-full bg-warn-soft">
        <div className="absolute inset-y-0 left-1/4 w-1/2 bg-ok-soft" />
        <div className={`absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface
          ${flag === "H" || flag === "L" ? "bg-danger" : "bg-ok"}`} style={{ left: `${pos}%` }} />
      </div>
      <div aria-hidden className="mt-0.5 flex justify-between text-[0.75rem] text-muted">
        <span className="pl-[22%]">{lo ?? ""}</span><span className="pr-[22%]">{hi ?? ""}</span>
      </div>
    </div>
  );
}

export function FlagTag({ flag }: { flag?: string | null }) {
  const { t } = useI18n();
  if (!flag) return null;
  const m = { H: { cls: "bg-danger-soft text-danger", icon: ArrowUp, w: t.flagHigh },
              L: { cls: "bg-warn-soft text-warn", icon: ArrowDown, w: t.flagLow },
              N: { cls: "bg-ok-soft text-ok", icon: Check, w: t.flagNormal } }[flag as "H" | "L" | "N"];
  if (!m) return null;
  const Icon = m.icon;
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.8125rem] font-semibold ${m.cls}`}>
    <Icon aria-hidden className="h-3.5 w-3.5" />{m.w}</span>;
}

/* ---------------- vital card ---------------- */
export function VitalCard({ label, value, unit, status, trend, icon: Icon }: {
  label: string; value: string; unit: string; status?: "ok" | "high" | "low"; trend?: "up" | "down" | "flat"; icon: LucideIcon;
}) {
  const { t } = useI18n();
  const st = status === "high" ? { cls: "text-danger", w: t.flagHigh } : status === "low" ? { cls: "text-warn", w: t.flagLow }
    : status === "ok" ? { cls: "text-ok", w: t.flagNormal } : null;
  const Trend = trend === "up" ? ArrowUp : trend === "down" ? ArrowDown : Minus;
  return (
    <div className="relative overflow-hidden rounded-[6px] border border-border bg-bg/60 p-3">
      <p className="flex items-center gap-1.5 text-[0.875rem] text-muted"><Icon aria-hidden className="h-4 w-4" />{label}</p>
      <p className="mt-1 break-all font-mono text-[1.625rem] font-bold leading-none tracking-tight">{value}<span className="ml-1 font-sans text-[0.8125rem] font-normal tracking-normal text-muted">{unit}</span></p>
      {st && <span aria-hidden className={`absolute inset-y-0 left-0 w-[3px] ${status === "high" ? "bg-danger" : status === "low" ? "bg-warn" : "bg-ok"}`} />}
      {(st || trend) && (
        <p className={`mt-1 flex items-center gap-1 text-[0.8125rem] font-semibold ${st?.cls ?? "text-muted"}`}>
          {trend && <Trend aria-hidden className="h-3.5 w-3.5" />}{st?.w}
        </p>
      )}
    </div>
  );
}

/* ---------------- step indicator ---------------- */
export function Steps({ steps, current }: { steps: string[]; current: number }) {
  return (
    <ol className="flex items-center gap-2" aria-label="Progress">
      {steps.map((s, i) => (
        <li key={s} aria-current={i === current ? "step" : undefined} className="flex flex-1 items-center gap-2">
          <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-[0.875rem] font-semibold
            ${i < current ? "bg-ok text-on-tone" : i === current ? "bg-brand text-brand-ink" : "bg-surface-2 text-muted"}`}>
            {i < current ? <Check aria-hidden className="h-4 w-4" /> : i + 1}</span>
          <span className={`hidden text-[0.875rem] sm:inline ${i === current ? "font-semibold" : "text-muted"}`}>{s}</span>
          {i < steps.length - 1 && <span aria-hidden className="h-0 flex-1 border-t-2 border-dashed border-border-input" />}
        </li>
      ))}
    </ol>
  );
}
