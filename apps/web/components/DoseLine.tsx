"use client";

import { Check, ChevronDown, Pill, X } from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { fill, useI18n, bcp47 } from "@/lib/i18n";

export type DoseItem = { medication_id: string; name: string; slot: string; time: string | null; status: string | null };

const mins = (hhmm: string) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
type State = "taken" | "skipped" | "next" | "overdue" | "later";
type Stop = { time: string; at: number; items: DoseItem[]; state: State };

/** Today's medicines as a vertical day-line: one compact row per dose time on a thin rail, in time order.
 *  A station is green with a tick when everything at that time is taken, glows in the next-step colour when it is
 *  next, turns amber once its time has passed unmarked, and is an empty well while still to come. A "now" marker sits
 *  between the rows, and the rail fills down to it. The next row carries "Taken"; any row opens to mark each medicine.
 *  Motion: the rail draws down, rows settle in one after another, "now" slides into place, and taking a dose fills
 *  the station and the rail. Reduced motion turns all of it off (globals.css). */
export function DoseLine({ items, size = "md" }: { items: DoseItem[]; size?: "md" | "lg" }) {
  const { t, lang } = useI18n();
  const [now, setNow] = useState(() => new Date());
  const [open, setOpen] = useState<string | null>(null);
  const [local, setLocal] = useState<Record<string, string>>({});   // optimistic marks, so the motion is immediate
  const [justTook, setJustTook] = useState<string | null>(null);
  useEffect(() => { const id = setInterval(() => setNow(new Date()), 60_000); return () => clearInterval(id); }, []);
  useEffect(() => { setLocal({}); }, [items]);
  const nowM = now.getHours() * 60 + now.getMinutes();
  const key = (i: DoseItem) => `${i.medication_id}|${i.slot}`;
  const statusOf = (i: DoseItem) => local[key(i)] ?? i.status;

  const timed = items.filter((i) => i.time && i.slot !== "as_needed");
  const byTime = new Map<string, DoseItem[]>();
  for (const i of timed) byTime.set(i.time!, [...(byTime.get(i.time!) ?? []), i]);
  let nextAssigned = false;
  const stops: Stop[] = [...byTime.entries()].sort(([a], [b]) => mins(a) - mins(b)).map(([time, its]) => {
    const all = its.every((i) => statusOf(i)), taken = its.every((i) => statusOf(i) === "taken");
    let state: State = taken ? "taken" : all ? "skipped" : "later";
    if (!all && !nextAssigned && mins(time) >= nowM - 60) { state = "next"; nextAssigned = true; }
    else if (!all && mins(time) < nowM - 60) state = "overdue";
    return { time, at: mins(time), items: its, state };
  });
  const done = timed.filter((i) => statusOf(i) === "taken").length;
  const nowIdx = stops.findIndex((s) => s.at > nowM);           // the "now" marker goes before this row
  const passed = nowIdx === -1 ? stops.length : nowIdx;          // rows already behind us
  const lg = size === "lg";
  const row = lg ? 52 : 44;                                      // px per row (rail fill is measured in rows)

  async function mark(list: DoseItem[], status: "taken" | "skipped") {
    setLocal((cur) => ({ ...cur, ...Object.fromEntries(list.map((i) => [key(i), status])) }));
    if (status === "taken") { setJustTook(list[0]?.time ?? null); setTimeout(() => setJustTook(null), 700); }
    await Promise.all(list.map((i) => api("/me/doses", { method: "POST", body: JSON.stringify({ medication_id: i.medication_id, slot: i.slot, status }) })));
    window.dispatchEvent(new Event("sushrut:data-changed"));
  }
  const fmt = (hhmm: string) => new Date(2000, 0, 1, ...hhmm.split(":").map(Number) as [number, number])
    .toLocaleTimeString(bcp47(lang), { hour: "numeric", minute: "2-digit" });
  const until = (m: number) => { const d = m - nowM; return d <= 0 ? (d > -60 ? t.dueNow : t.overdue) : d < 60 ? fill(t.inMinutes, { n: d }) : fill(t.inHours, { h: Math.floor(d / 60), m: d % 60 }); };
  if (!stops.length) return null;
  return <Rail stops={stops} nowIdx={nowIdx} passed={passed} row={row} lg={lg} body={{ t, now, open, setOpen, statusOf, mark, fmt, until, justTook, done, total: timed.length }} />;
}

/** The rail fill follows the real position of "now" (rows can open, so it is measured, not computed). */
function Rail({ stops, nowIdx, passed, row, lg, body }: { stops: Stop[]; nowIdx: number; passed: number; row: number; lg: boolean; body: any }) {
  const { t, now, open, setOpen, statusOf, mark, fmt, until, justTook, done, total } = body;
  const box = useRef<HTMLDivElement>(null);
  const [fillPx, setFillPx] = useState(0);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const marker = el.querySelector<HTMLElement>("[data-dl-now]");
    const rail = el.querySelector<HTMLElement>(".dl-rail");
    if (!marker || !rail || passed === 0) { setFillPx(0); return; }
    setFillPx(marker.getBoundingClientRect().top - rail.getBoundingClientRect().top + marker.offsetHeight / 2);
  });


  return (
    <div ref={box} className="relative" aria-label={`${t.todayMeds}: ${fill(t.dosesTakenOf, { n: done, m: total })}`}>
      {/* the rail */}
      <div aria-hidden className={`dl-rail absolute bottom-3 top-3 w-[3px] overflow-hidden rounded-full bg-surface-2 ${lg ? "left-[21px]" : "left-[17px]"}`}>
        <div className="dl-fill w-full rounded-full bg-[linear-gradient(180deg,var(--accent),var(--brand))]" style={{ height: fillPx }} />
      </div>
      <ol className="relative">
        {stops.map((s, i) => {
          const isOpen = open === s.time;
          const pending = s.items.filter((x) => !statusOf(x));
          const names = s.items.map((x) => x.name).join(", ");
          const dot = s.state === "taken" ? "bg-ok text-on-tone border-ok"
            : s.state === "next" ? "bg-brand text-brand-ink border-brand dl-halo"
            : s.state === "overdue" ? "bg-warn-soft text-warn border-warn"
            : s.state === "skipped" ? "bg-surface text-muted border-border-input"
            : "bg-surface text-muted border-border-input shadow-[var(--well)]";
          return (
            <Fragment key={s.time}>
              {i === nowIdx && <NowMarker label={fmt(`${now.getHours()}:${String(now.getMinutes()).padStart(2, "0")}`)} lg={lg} />}
              <li className="dl-row" style={{ animationDelay: `${80 + i * 60}ms` }}>
                <div className="flex items-center gap-3" style={{ minHeight: row }}>
                  <button type="button" onClick={() => setOpen(isOpen ? null : s.time)} aria-expanded={isOpen}
                    aria-label={`${fmt(s.time)}: ${names} — ${s.state === "taken" ? t.taken : s.state === "skipped" ? t.skipped : s.state === "next" ? t.nextDose : s.state === "overdue" ? t.overdue : t.notYet}`}
                    className={`relative z-10 grid shrink-0 place-items-center rounded-full border-2 transition-colors duration-200 ${lg ? "h-11 w-11" : "h-9 w-9"} ${dot} ${justTook === s.time ? "dl-pop" : ""}`}>
                    {s.state === "taken" ? <Check aria-hidden className="dl-tick h-4 w-4" strokeWidth={3} />
                      : s.state === "skipped" ? <X aria-hidden className="h-4 w-4" />
                      : s.items.length > 1 ? <span className="font-mono text-[0.8125rem] font-bold">{s.items.length}</span> : <Pill aria-hidden className="h-4 w-4" />}
                  </button>
                  <button type="button" onClick={() => setOpen(isOpen ? null : s.time)} tabIndex={-1} aria-hidden
                    className="flex min-w-0 flex-1 items-baseline gap-2 text-left">
                    <span className={`shrink-0 font-mono text-[0.875rem] ${s.state === "next" ? "font-bold" : "text-muted"}`}>{s.time}</span>
                    <span className={`min-w-0 truncate ${s.state === "taken" || s.state === "skipped" ? "text-muted line-through decoration-1" : "font-semibold"} ${lg ? "" : "text-[0.9375rem]"}`}>{names}</span>
                    {s.state === "next" && <span className="hidden shrink-0 text-[0.8125rem] text-muted min-[400px]:inline">· {until(s.at)}</span>}
                    {s.state === "overdue" && <span className="shrink-0 text-[0.8125rem] font-semibold text-warn">· {t.overdue}</span>}
                  </button>
                  {s.state === "next" && (
                    <button type="button" onClick={() => mark(pending, "taken")}
                      className="d-press inline-flex h-10 shrink-0 items-center gap-1.5 rounded-full bg-brand px-3 text-[0.875rem] font-semibold text-brand-ink">
                      <Check aria-hidden className="h-4 w-4" strokeWidth={2.6} />{t.taken}</button>
                  )}
                  {s.state !== "next" && pending.length > 0 && (
                    <ChevronDown aria-hidden className={`h-4 w-4 shrink-0 text-muted transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`} />
                  )}
                </div>
                {isOpen && (
                  <ul className="expand-in mb-1.5 ml-12 space-y-1 border-l border-dashed border-border pl-3">
                    {s.items.map((x) => {
                      const st = statusOf(x);
                      return (
                        <li key={x.medication_id} className="flex min-h-10 items-center gap-2">
                          <span className={`min-w-0 flex-1 truncate text-[0.9375rem] ${st === "taken" ? "text-muted line-through" : ""}`}>{x.name}</span>
                          {st ? <span className={`text-[0.8125rem] font-semibold ${st === "taken" ? "text-ok" : "text-muted"}`}>{st === "taken" ? t.taken : t.skipped}</span> : (<>
                            <button type="button" onClick={() => mark([x], "taken")} aria-label={`${t.taken}: ${x.name}`} className="grid h-10 w-10 place-items-center rounded-full bg-ok-soft text-ok"><Check aria-hidden className="h-4 w-4" /></button>
                            <button type="button" onClick={() => mark([x], "skipped")} aria-label={`${t.skip}: ${x.name}`} className="grid h-10 w-10 place-items-center rounded-full bg-surface-2 text-muted"><X aria-hidden className="h-4 w-4" /></button>
                          </>)}
                        </li>
                      );
                    })}
                    <li><Link href="/medicines" className="text-[0.8125rem] font-semibold text-info underline">{t.allDoses}</Link></li>
                  </ul>
                )}
              </li>
            </Fragment>
          );
        })}
        {nowIdx === -1 && <NowMarker label={fmt(`${now.getHours()}:${String(now.getMinutes()).padStart(2, "0")}`)} lg={lg} />}
      </ol>
      {stops.every((s) => s.state === "taken" || s.state === "skipped") && (
        <p className="dl-row mt-1 pl-12 text-[0.9375rem] font-semibold text-ok" style={{ animationDelay: "200ms" }}>{t.allDosesDone}</p>
      )}
    </div>
  );
}

/** "Now": a short tick on the rail with the time, sliding in from the left. */
function NowMarker({ label, lg }: { label: string; lg: boolean }) {
  return (
    <li aria-hidden data-dl-now className="dl-now relative flex h-3 items-center">
      <span className={`absolute h-[3px] rounded-full bg-text ${lg ? "left-[14px] w-[17px]" : "left-[10px] w-[17px]"}`} />
      <span className="ml-12 font-mono text-[0.6875rem] font-semibold uppercase tracking-wide text-muted">{label}</span>
    </li>
  );
}
