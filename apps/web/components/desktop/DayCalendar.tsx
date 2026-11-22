"use client";

import { ChevronLeft, ChevronRight, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useDataChanged } from "@/components/Home";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

type Ev = { type: "care" | "follow_up" | "refill" | "report"; title: string; status: string; href: string };
type Day = { taken: number; skipped: number; events: Ev[] };
type Cal = { month: string; today: string; days: Record<string, Day> };
const DOT: Record<Ev["type"], string> = { care: "bg-warn", follow_up: "bg-[var(--d-accent-500)]", refill: "bg-warn", report: "bg-ok" };

const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;

/** The care calendar (docs/DESKTOP_UI_SPEC.md §4.1): a month of translucent day tiles in the style of the reference —
 *  today glows, each day lists up to two events with a status dot, and a click opens a solid popover card with the
 *  day's doses and events. */
export function DayCalendar() {
  const { t, lang } = useI18n();
  const [month, setMonth] = useState(() => ym(new Date()));
  const [cal, setCal] = useState<Cal | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => { api<Cal>(`/me/calendar?month=${month}`).then(setCal).catch(() => setCal({ month, today: "", days: {} })); }, [month]);
  useEffect(load, [load]);
  useDataChanged(load);
  const loc = lang === "hi" ? "hi-IN" : "en-IN";
  const first = new Date(`${month}-01T00:00:00`);
  const start = new Date(first); start.setDate(1 - first.getDay());
  const cells = Array.from({ length: 42 }, (_, i) => { const d = new Date(start); d.setDate(start.getDate() + i); return d; });
  const weeks = cells[35].getMonth() !== first.getMonth() ? cells.slice(0, 35) : cells;
  const shift = (n: number) => { const d = new Date(first); d.setMonth(d.getMonth() + n); setMonth(ym(d)); setOpen(null); };
  const iso = (d: Date) => `${ym(d)}-${String(d.getDate()).padStart(2, "0")}`;
  const weekdays = Array.from({ length: 7 }, (_, i) => new Date(2026, 1, i + 1).toLocaleDateString(loc, { weekday: "short" }));

  return (
    <section aria-label={t.careCalendar} className="d-tile p-5">
      <div className="mb-4 flex items-center gap-3">
        <h2 className="display text-[1.375rem] font-bold">{first.toLocaleDateString(loc, { month: "long", year: "numeric" })}</h2>
        <span className="flex-1" />
        <button type="button" onClick={() => setMonth(ym(new Date()))} className="d-press h-10 rounded-full px-3.5 text-[0.875rem] font-semibold text-muted hover:bg-[var(--d-tile-hover)] hover:text-text">{t.today}</button>
        <button type="button" aria-label={t.prevMonth} onClick={() => shift(-1)} className="d-press grid h-10 w-10 place-items-center rounded-full border border-[var(--d-tile-line)] hover:bg-[var(--d-tile-hover)]"><ChevronLeft aria-hidden className="h-4 w-4" /></button>
        <button type="button" aria-label={t.nextMonth} onClick={() => shift(1)} className="d-press grid h-10 w-10 place-items-center rounded-full border border-[var(--d-tile-line)] hover:bg-[var(--d-tile-hover)]"><ChevronRight aria-hidden className="h-4 w-4" /></button>
      </div>
      <div role="grid" aria-label={t.careCalendar} className="grid grid-cols-7 gap-2">
        {weekdays.map((w) => <div key={w} role="columnheader" className="px-1 pb-1 text-[0.8125rem] font-semibold text-muted">{w}</div>)}
        {!cal ? Array.from({ length: 35 }, (_, i) => <div key={i} className="skeleton h-[84px] rounded-[14px]" />) : weeks.map((d) => {
          const key = iso(d);
          const info = cal.days[key];
          const inMonth = d.getMonth() === first.getMonth();
          const isToday = key === (cal.today || iso(new Date()));
          const doses = info ? info.taken + info.skipped : 0;
          return (
            <div key={key} role="gridcell" className="relative">
              <button type="button" onClick={() => setOpen(open === key ? null : key)} aria-expanded={open === key}
                aria-label={`${d.toLocaleDateString(loc, { weekday: "long", day: "numeric", month: "long" })}${info?.events.length ? `, ${fill(t.nEvents, { n: info.events.length })}` : ""}`}
                className={`d-tile d-tile-hover flex h-[84px] w-full flex-col items-start rounded-[14px] p-2 text-left min-[1600px]:h-[96px]
                  ${isToday ? "d-glow" : ""} ${inMonth ? "" : "opacity-40"}`}>
                <span className={`font-mono text-[1.125rem] leading-none ${isToday ? "font-bold" : "font-semibold"}`}>{d.getDate()}</span>
                <span className="mt-auto w-full space-y-0.5">
                  {info?.events.slice(0, 2).map((e, i) => (
                    <span key={i} className="flex min-w-0 items-center gap-1 text-[0.6875rem] leading-tight text-muted">
                      <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[e.type]}`} /><span className="truncate">{e.title}</span></span>
                  ))}
                  {!!doses && (
                    <span className="flex items-center gap-0.5" aria-hidden>
                      {Array.from({ length: Math.min(doses, 6) }, (_, i) => <span key={i} className={`h-1 w-1 rounded-full ${i < (info?.taken ?? 0) ? "bg-ok" : "bg-danger"}`} />)}
                    </span>
                  )}
                </span>
              </button>
              {open === key && <DayPopover date={d} info={info} onClose={() => setOpen(null)} loc={loc}
                side={d.getDay() >= 4 ? "right" : "left"} />}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function DayPopover({ date, info, onClose, loc, side }: { date: Date; info?: Day; onClose: () => void; loc: string; side: "left" | "right" }) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const click = (e: MouseEvent) => { if (!ref.current?.parentElement?.contains(e.target as Node)) onClose(); };
    document.addEventListener("keydown", key);
    document.addEventListener("mousedown", click);
    return () => { document.removeEventListener("keydown", key); document.removeEventListener("mousedown", click); };
  }, [onClose]);
  return (
    <div ref={ref} tabIndex={-1} role="dialog" aria-label={date.toLocaleDateString(loc, { day: "numeric", month: "long" })}
      className={`d-pop d-dialog-in absolute top-0 z-30 w-[320px] p-4 ${side === "right" ? "right-[calc(100%+8px)]" : "left-[calc(100%+8px)]"}`}>
      <div className="mb-3 flex items-center justify-between gap-2 border-b border-dashed border-[var(--d-tile-line)] pb-3">
        <p className="font-semibold">{date.toLocaleDateString(loc, { weekday: "long", day: "numeric", month: "long" })}</p>
        <button type="button" aria-label={t.close} onClick={onClose} className="grid h-9 w-9 place-items-center rounded-full hover:bg-[var(--d-tile-hover)]"><X aria-hidden className="h-4 w-4" /></button>
      </div>
      {!info ? <p className="text-muted">{t.nothingThisDay}</p> : (
        <dl className="space-y-3 text-[0.9375rem]">
          {(info.taken + info.skipped) > 0 && (
            <div className="flex justify-between gap-3"><dt className="text-muted">{t.todayMeds}</dt>
              <dd className="font-mono font-semibold">{fill(t.dosesTakenOf, { n: info.taken, m: info.taken + info.skipped })}</dd></div>
          )}
          {info.events.map((e, i) => (
            <div key={i} className="flex items-center justify-between gap-3">
              <dt className="flex min-w-0 items-center gap-2"><span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${DOT[e.type]}`} />
                <span className="truncate">{e.title}</span></dt>
              <dd><Link href={e.href} className="font-semibold text-info underline underline-offset-4">{t.openReport}</Link></dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}
