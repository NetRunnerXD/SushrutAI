"use client";

import { CalendarPlus, ChevronLeft, ChevronRight, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useDataChanged } from "@/components/Home";
import { api } from "@/lib/api";
import { fill, useI18n, bcp47 } from "@/lib/i18n";

type Ev = { type: "care" | "follow_up" | "refill" | "report" | "mine"; title: string; status: string; href: string; id?: string; kind?: string; time?: string | null };
type Day = { taken: number; skipped: number; events: Ev[] };
type Cal = { month: string; today: string; days: Record<string, Day> };
const DOT: Record<Ev["type"], string> = { care: "bg-warn", follow_up: "bg-[var(--d-accent-500)]", refill: "bg-warn", report: "bg-ok", mine: "bg-[var(--d-accent-500)]" };

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
  const loc = bcp47(lang);
  const upcoming = (cal && cal.today) ? Object.entries(cal.days).filter(([k]) => k >= cal.today).sort(([a], [b]) => a.localeCompare(b))
    .flatMap(([k, d]) => d.events.filter((e) => e.type !== "report").map((e) => ({ k, e }))).slice(0, 4) : [];
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
        <label className="relative grid h-10 w-10 place-items-center rounded-full border border-[var(--d-tile-line)] hover:bg-[var(--d-tile-hover)]" title={t.jumpToMonth}>
          <CalendarPlus aria-hidden className="h-4 w-4" />
          <span className="sr-only">{t.jumpToMonth}</span>
          <input type="month" value={month} onChange={(e) => { if (e.target.value) { setMonth(e.target.value); setOpen(null); } }}
            className="absolute inset-0 cursor-pointer opacity-0" />
        </label>
        <span className="flex-1" />
        <button type="button" onClick={() => setMonth(ym(new Date()))} className="d-press h-10 rounded-full px-3.5 text-[0.875rem] font-semibold text-muted hover:bg-[var(--d-tile-hover)] hover:text-text">{t.today}</button>
        <button type="button" aria-label={t.prevMonth} onClick={() => shift(-1)} className="d-press grid h-10 w-10 place-items-center rounded-full border border-[var(--d-tile-line)] hover:bg-[var(--d-tile-hover)]"><ChevronLeft aria-hidden className="h-4 w-4" /></button>
        <button type="button" aria-label={t.nextMonth} onClick={() => shift(1)} className="d-press grid h-10 w-10 place-items-center rounded-full border border-[var(--d-tile-line)] hover:bg-[var(--d-tile-hover)]"><ChevronRight aria-hidden className="h-4 w-4" /></button>
      </div>
      {upcoming.length > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-2" aria-label={t.nextUp}>
          <span className="text-[0.8125rem] font-semibold text-muted">{t.nextUp}</span>
          {upcoming.map(({ k, e }, i) => (
            <button key={i} type="button" onClick={() => { setMonth(k.slice(0, 7)); setOpen(k); }}
              className="d-press inline-flex max-w-[16rem] items-center gap-1.5 rounded-full border border-[var(--d-tile-line)] px-3 py-1 text-[0.8125rem] hover:bg-[var(--d-tile-hover)]">
              <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[e.type]}`} />
              <span className="font-mono text-muted">{new Date(`${k}T00:00:00`).toLocaleDateString(loc, { day: "numeric", month: "short" })}{e.time ? ` ${e.time}` : ""}</span>
              <span className="truncate">{e.title}</span></button>
          ))}
        </div>
      )}
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
                className={`d-tile d-tile-hover flex h-[84px] w-full flex-col items-start rounded-[14px] p-2 text-start min-[1600px]:h-[96px]
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
              {open === key && <DayPopover date={d} iso={key} info={info} onClose={() => setOpen(null)} onChanged={load} loc={loc}
                side={d.getDay() >= 4 ? "right" : "left"} />}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function DayPopover({ date, iso, info, onClose, onChanged, loc, side }: { date: Date; iso: string; info?: Day; onClose: () => void; onChanged: () => void; loc: string; side: "left" | "right" }) {
  const { t } = useI18n();
  const [title, setTitle] = useState("");
  const [time, setTime] = useState("");
  const [kind, setKind] = useState<"appointment" | "reminder" | "note">("appointment");
  const [busy, setBusy] = useState(false);
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || busy) return;
    setBusy(true);
    await api("/me/calendar/events", { method: "POST", body: JSON.stringify({ day: iso, title: title.trim(), kind, time: time || null }) }).catch(() => {});
    setTitle(""); setTime(""); setBusy(false); onChanged();
  };
  const remove = async (id: string) => { await api(`/me/calendar/events/${id}`, { method: "DELETE" }).catch(() => {}); onChanged(); };
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
      className={`d-pop d-dialog-in absolute top-0 z-30 w-[320px] p-4 ${side === "right" ? "end-[calc(100%+8px)]" : "start-[calc(100%+8px)]"}`}>
      <div className="mb-3 flex items-center justify-between gap-2 border-b border-dashed border-[var(--d-tile-line)] pb-3">
        <p className="font-semibold">{date.toLocaleDateString(loc, { weekday: "long", day: "numeric", month: "long" })}</p>
        <button type="button" aria-label={t.close} onClick={onClose} className="grid h-9 w-9 place-items-center rounded-full hover:bg-[var(--d-tile-hover)]"><X aria-hidden className="h-4 w-4" /></button>
      </div>
      {!info?.events.length && !(info && info.taken + info.skipped) ? <p className="text-muted">{t.nothingThisDay}</p> : (
        <dl className="space-y-3 text-[0.9375rem]">
          {(info.taken + info.skipped) > 0 && (
            <div className="flex justify-between gap-3"><dt className="text-muted">{t.todayMeds}</dt>
              <dd className="font-mono font-semibold">{fill(t.dosesTakenOf, { n: info.taken, m: info.taken + info.skipped })}</dd></div>
          )}
          {info.events.map((e, i) => (
            <div key={i} className="flex items-center justify-between gap-3">
              <dt className="flex min-w-0 items-center gap-2"><span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${DOT[e.type]}`} />
                <span className="truncate">{e.title}</span></dt>
              <dd>{e.type === "mine"
                ? <span className="flex items-center gap-1">{e.time && <span className="font-mono text-[0.8125rem] text-muted">{e.time}</span>}
                    <button type="button" aria-label={`${t.removeEvent}: ${e.title}`} onClick={() => remove(e.id!)} className="grid h-8 w-8 place-items-center rounded-full text-muted hover:bg-[var(--d-tile-hover)] hover:text-danger"><Trash2 aria-hidden className="h-4 w-4" /></button></span>
                : <Link href={e.href} className="font-semibold text-info underline underline-offset-4">{t.openReport}</Link>}</dd>
            </div>
          ))}
        </dl>
      )}
      <form onSubmit={add} className="mt-3 space-y-2 border-t border-dashed border-[var(--d-tile-line)] pt-3">
        <label className="sr-only" htmlFor={`ev-${iso}`}>{t.eventTitle}</label>
        <input id={`ev-${iso}`} className="input min-h-10" maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t.eventTitle} />
        <div className="flex gap-2">
          <select aria-label={t.eventKind} className="input min-h-10 flex-1" value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
            <option value="appointment">{t.kindAppointment}</option><option value="reminder">{t.kindReminder}</option><option value="note">{t.kindNote}</option>
          </select>
          <input type="time" aria-label={t.eventTime} className="input min-h-10 w-[7.5rem]" value={time} onChange={(e) => setTime(e.target.value)} />
        </div>
        <button type="submit" disabled={!title.trim() || busy} className="btn btn-primary min-h-10 w-full"><CalendarPlus aria-hidden className="h-4 w-4" />{t.addToCalendar}</button>
      </form>
    </div>
  );
}
