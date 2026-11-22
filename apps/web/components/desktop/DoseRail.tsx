"use client";

import { Check, Pill, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useDoses } from "@/components/Home";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

type Item = { medication_id: string; name: string; slot: string; time: string | null; status: string | null };
type State = "taken" | "skipped" | "next" | "overdue" | "later";
const START = 6 * 60, END = 23 * 60;
const mins = (hhmm: string) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const pos = (m: number) => Math.min(100, Math.max(0, ((m - START) / (END - START)) * 100));

/** Desktop "Today's medicines", collapsed to one horizontal rail (6 am–11 pm): only stations and times, no names, so
 *  it takes a single slim row under the voice tile. A station is green with a tick when taken, the accent when next,
 *  amber when its time passed unmarked, an empty well while still to come; "now" is a needle and the rail fills to it.
 *  Clicking a station opens its medicines (Taken / Skip). Motion: the rail draws left to right, stations pop in one
 *  after another, the needle slides to now; taking a dose pops the station. */
export function DoseRail() {
  const { t, lang } = useI18n();
  const { items } = useDoses();
  const [now, setNow] = useState(() => new Date());
  const [open, setOpen] = useState<string | null>(null);
  const [local, setLocal] = useState<Record<string, string>>({});
  const [popped, setPopped] = useState<string | null>(null);
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 60_000); return () => clearInterval(id); }, []);
  useEffect(() => { setLocal({}); }, [items]);
  const nowM = now.getHours() * 60 + now.getMinutes();
  const k = (i: Item) => `${i.medication_id}|${i.slot}`;
  const st = (i: Item) => local[k(i)] ?? i.status;

  const timed = ((items ?? []) as Item[]).filter((i) => i.time && i.slot !== "as_needed");
  const byTime = new Map<string, Item[]>();
  for (const i of timed) byTime.set(i.time!, [...(byTime.get(i.time!) ?? []), i]);
  let nextSet = false;
  const stops = [...byTime.entries()].sort(([a], [b]) => mins(a) - mins(b)).map(([time, its]) => {
    const all = its.every((i) => st(i)), taken = its.every((i) => st(i) === "taken");
    let state: State = taken ? "taken" : all ? "skipped" : "later";
    if (!all && !nextSet && mins(time) >= nowM - 60) { state = "next"; nextSet = true; }
    else if (!all && mins(time) < nowM - 60) state = "overdue";
    return { time, at: mins(time), items: its, state };
  });
  const done = timed.filter((i) => st(i) === "taken").length;
  const fmt = (hhmm: string) => new Date(2000, 0, 1, ...hhmm.split(":").map(Number) as [number, number])
    .toLocaleTimeString(lang === "hi" ? "hi-IN" : "en-IN", { hour: "numeric", minute: "2-digit" });

  async function mark(list: Item[], status: "taken" | "skipped") {
    setLocal((c) => ({ ...c, ...Object.fromEntries(list.map((i) => [k(i), status])) }));
    if (status === "taken") { setPopped(list[0]?.time ?? null); setTimeout(() => setPopped(null), 700); }
    await Promise.all(list.map((i) => api("/me/doses", { method: "POST", body: JSON.stringify({ medication_id: i.medication_id, slot: i.slot, status }) })));
    window.dispatchEvent(new Event("sushrut:data-changed"));
  }

  return (
    <section aria-label={`${t.todayMeds}: ${fill(t.dosesTakenOf, { n: done, m: timed.length })}`} className="d-tile relative z-20 col-span-full flex items-center gap-5 px-5 py-3">
      <div className="shrink-0">
        <p className="text-[0.8125rem] font-semibold text-muted">{t.todayMeds}</p>
        <p className="font-mono text-[1.125rem] font-bold">{items === null ? "…" : `${done}/${timed.length}`}</p>
      </div>
      {items === null ? <div className="skeleton h-10 flex-1" /> : timed.length === 0 ? (
        <p className="flex-1 text-muted">{t.noMedsToday} <Link href="/medicines/rx" className="font-semibold text-info underline">{t.addPrescription}</Link></p>
      ) : (
        <div role="list" className="relative mx-3 h-14 flex-1">
          <div aria-hidden className="dr-rail absolute inset-x-0 top-[18px] h-[3px] overflow-hidden rounded-full bg-surface-2">
            <div className="dl-fill h-full rounded-full bg-[linear-gradient(90deg,var(--accent),var(--brand))]" style={{ width: `${pos(nowM)}%` }} />
          </div>
          <div aria-hidden className="dr-now absolute top-[6px] h-[27px] w-[2px] rounded-full bg-text" style={{ left: `${pos(nowM)}%` }}>
            <span className="absolute left-1/2 top-[30px] -translate-x-1/2 whitespace-nowrap font-mono text-[0.625rem] font-semibold uppercase text-muted">{t.now}</span>
          </div>
          {stops.map((s, i) => {
            const cls = s.state === "taken" ? "bg-ok text-on-tone border-ok"
              : s.state === "next" ? "bg-brand text-brand-ink border-brand dl-halo"
              : s.state === "overdue" ? "bg-warn-soft text-warn border-warn"
              : s.state === "skipped" ? "bg-surface text-muted border-border-input" : "bg-surface text-muted border-border-input shadow-[var(--well)]";
            const word = s.state === "taken" ? t.taken : s.state === "skipped" ? t.skipped : s.state === "next" ? t.nextDose : s.state === "overdue" ? t.overdue : t.notYet;
            return (
              <div role="listitem" key={s.time} className="dr-stop absolute top-0 -translate-x-1/2" style={{ left: `${pos(s.at)}%`, animationDelay: `${200 + i * 70}ms` }}>
                <button type="button" onClick={() => setOpen(open === s.time ? null : s.time)} aria-expanded={open === s.time}
                  aria-label={`${fmt(s.time)}: ${fill(t.nMedicines, { n: s.items.length })} — ${word}`} title={`${fmt(s.time)} · ${word}`}
                  className={`d-press grid h-9 w-9 place-items-center rounded-full border-2 transition-[transform,background-color] duration-200 hover:scale-110 ${cls} ${popped === s.time ? "dl-pop" : ""}`}>
                  {s.state === "taken" ? <Check aria-hidden className="dl-tick h-4 w-4" strokeWidth={3} /> : s.state === "skipped" ? <X aria-hidden className="h-4 w-4" />
                    : s.items.length > 1 ? <span className="font-mono text-[0.8125rem] font-bold">{s.items.length}</span> : <Pill aria-hidden className="h-4 w-4" />}
                </button>
                <span aria-hidden className={`absolute left-1/2 top-[40px] -translate-x-1/2 font-mono text-[0.6875rem] ${s.state === "next" ? "font-bold text-text" : "text-muted"}`}>{s.time}</span>
                {open === s.time && <StopPopover stop={s} st={st} onMark={mark} onClose={() => setOpen(null)} fmt={fmt} edge={s.at > (START + END) / 2 ? "right" : "left"} />}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function StopPopover({ stop, st, onMark, onClose, fmt, edge }: {
  stop: { time: string; items: Item[] }; st: (i: Item) => string | null; onMark: (l: Item[], s: "taken" | "skipped") => void;
  onClose: () => void; fmt: (s: string) => string; edge: "left" | "right";
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    const click = (e: MouseEvent) => { if (!ref.current?.parentElement?.contains(e.target as Node)) onClose(); };
    document.addEventListener("keydown", key); document.addEventListener("mousedown", click);
    return () => { document.removeEventListener("keydown", key); document.removeEventListener("mousedown", click); };
  }, [onClose]);
  const pending = stop.items.filter((i) => !st(i));
  return (
    <div ref={ref} tabIndex={-1} role="dialog" aria-label={fmt(stop.time)}
      className={`d-pop d-dialog-in absolute top-12 z-30 w-72 space-y-2 p-3 ${edge === "right" ? "right-0" : "left-0"}`}>
      <div className="flex items-center justify-between gap-2">
        <p className="font-mono font-semibold">{fmt(stop.time)}</p>
        {pending.length > 1 && (
          <button type="button" onClick={() => onMark(pending, "taken")} className="d-press inline-flex h-9 items-center gap-1 rounded-full bg-brand px-3 text-[0.8125rem] font-semibold text-brand-ink">
            <Check aria-hidden className="h-4 w-4" />{t.allTaken}</button>
        )}
      </div>
      <ul className="d-stagger space-y-1">
        {stop.items.map((i) => {
          const s = st(i);
          return (
            <li key={i.medication_id} className="flex min-h-10 items-center gap-2">
              <span className={`min-w-0 flex-1 truncate ${s === "taken" ? "text-muted line-through" : "font-semibold"}`}>{i.name}</span>
              {s ? <span className={`text-[0.8125rem] font-semibold ${s === "taken" ? "text-ok" : "text-muted"}`}>{s === "taken" ? t.taken : t.skipped}</span> : (<>
                <button type="button" onClick={() => onMark([i], "taken")} aria-label={`${t.taken}: ${i.name}`} className="grid h-9 w-9 place-items-center rounded-full bg-ok-soft text-ok"><Check aria-hidden className="h-4 w-4" /></button>
                <button type="button" onClick={() => onMark([i], "skipped")} aria-label={`${t.skip}: ${i.name}`} className="grid h-9 w-9 place-items-center rounded-full bg-surface-2 text-muted"><X aria-hidden className="h-4 w-4" /></button>
              </>)}
            </li>
          );
        })}
      </ul>
      <Link href="/medicines" className="block text-[0.8125rem] font-semibold text-info underline">{t.allDoses}</Link>
    </div>
  );
}
