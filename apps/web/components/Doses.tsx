"use client";

import { Check, CalendarClock, Moon, Pill, Sun, SunDim, X, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { Card, CardHeader, EmptyState, Skeleton } from "./ui/Card";
import { DosePictogram } from "./ui/Medical";
import { DoseLine } from "./DoseLine";

type Dose = { medication_id: string; name: string; slot: string; time: string | null; timing: string | null;
              frequency: any; status: "taken" | "skipped" | null; days_left: number | null };
type Doses = { items: Dose[]; progress: { done: number; total: number } };

/** The next scheduled dose not yet marked (earliest first): the one thing marked in jamun. */
function nextOf(items: Dose[]): Dose | null {
  return items.filter((i) => i.slot !== "as_needed" && i.time && !i.status)
    .sort((a, b) => (a.time! < b.time! ? -1 : 1))[0] ?? null;
}

/**
 * The day's dabba: one katori per part of the day. A katori fills dhania-green when every dose in it is taken,
 * turns jamun when it holds the next dose, and stays an empty steel well otherwise.
 */
export function Dabba({ items }: { items: Dose[] }) {
  const { t } = useI18n();
  const next = nextOf(items);
  const slots: [string, string, LucideIcon][] = [["morning", t.morning, Sun], ["afternoon", t.afternoon, SunDim], ["night", t.night, Moon]];
  return (
    <ul className="grid grid-cols-3 gap-3" aria-label={t.todayMeds}>
      {slots.map(([key, label, Icon]) => {
        const here = items.filter((i) => i.slot === key);
        const done = here.filter((i) => i.status === "taken").length;
        const full = here.length > 0 && here.every((i) => i.status);
        const isNext = !!next && next.slot === key;
        const time = here.find((i) => i.time)?.time;
        const state = !here.length ? "empty" : full ? "done" : isNext ? "next" : "open";
        return (
          <li key={key} className="flex flex-col items-center gap-1.5 text-center">
            <span className={`relative grid aspect-square w-full max-w-[5rem] place-items-center rounded-full sm:max-w-[6.5rem]
              ${state === "done" ? "fill-in bg-ok text-on-tone shadow-[inset_0_3px_6px_rgb(0_0_0/22%)]"
                : state === "next" ? "bg-brand text-brand-ink shadow-[inset_0_3px_6px_rgb(0_0_0/25%),0_0_0_4px_var(--brand-soft)]"
                : "well text-text"}`}>
              <span className="flex flex-col items-center leading-none">
                {state === "done" ? <Check aria-hidden className="h-8 w-8" strokeWidth={3} />
                  : <span className="font-mono text-[1.5rem] font-bold sm:text-[1.75rem]">{here.length ? `${done}/${here.length}` : "–"}</span>}
              </span>
            </span>
            <span className="flex items-center gap-1 text-[0.875rem] font-bold"><Icon aria-hidden className="h-4 w-4 text-muted" />{label}</span>
            <span className="sr-only">{here.length ? fill(t.dosesProgress, { done, total: here.length }) : "—"}</span>
            {time && <span aria-hidden className="font-mono text-[0.8125rem] text-muted">{time}</span>}
          </li>
        );
      })}
    </ul>
  );
}

/** Today's doses with Taken / Skip. Optimistic updates; the server is the source of truth. */
export function DoseTimeline({ compact = false }: { compact?: boolean }) {
  const { t } = useI18n();
  const [d, setD] = useState<Doses | null>(null);
  const load = () => api<Doses>("/me/doses").then(setD).catch(() => setD({ items: [], progress: { done: 0, total: 0 } }));
  useEffect(() => {
    load();
    window.addEventListener("sushrut:data-changed", load); // a voice action or the Home next-dose card changed doses
    return () => window.removeEventListener("sushrut:data-changed", load);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function mark(item: Dose, status: "taken" | "skipped") {
    const next = item.status === status ? "clear" : status;
    setD((cur) => cur && { ...cur, items: cur.items.map((i) => i === item ? { ...i, status: next === "clear" ? null : next } : i) });
    await api("/me/doses", { method: "POST", body: JSON.stringify({ medication_id: item.medication_id, slot: item.slot, status: next }) });
    window.dispatchEvent(new Event("sushrut:data-changed"));
  }
  const next = d ? nextOf(d.items) : null;

  return (
    <Card>
      <CardHeader icon={CalendarClock} title={t.todayMeds}
        sub={d && d.progress.total ? fill(t.dosesProgress, d.progress) : undefined} />
      {!d ? <div className="space-y-2"><Skeleton className="h-24" /><Skeleton className="h-16" /></div>
        : d.items.length === 0 ? (
          <EmptyState icon={Pill} text={t.noMedsToday}
            action={<Link href="/medicines/rx" className="btn btn-primary">{t.addPrescription}</Link>} />
        ) : (
          <div className="space-y-4">
            <Dabba items={d.items} />
            <ol className="space-y-2">
              {d.items.map((i) => {
                const isNext = next === i;
                return (
                  <li key={i.medication_id + i.slot}
                      className={`rounded-[16px] border p-3 transition-colors ${i.status === "taken" ? "border-ok/40 bg-ok-soft"
                        : i.status === "skipped" ? "border-border bg-surface-2" : isNext ? "border-brand bg-brand-soft" : "border-border"}`}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <p className="font-semibold">
                          <span className="me-2 font-mono font-bold">{i.time ?? "—"}</span>{i.name}
                        </p>
                        {!compact && <div className="mt-2"><DosePictogram frequency={i.frequency} timing={i.timing} /></div>}
                        {i.days_left != null && i.days_left <= 3 && i.days_left >= 0 && (
                          <p className="mt-1 text-[0.875rem] font-semibold text-warn">{i.days_left} days left — plan a refill or check with your doctor</p>
                        )}
                      </div>
                      <div className="flex gap-2">
                        <button onClick={() => mark(i, "taken")} aria-pressed={i.status === "taken"}
                          className={`inline-flex min-h-12 items-center gap-1.5 rounded-[14px] px-3.5 font-bold transition-colors
                            ${i.status === "taken" ? "bg-ok text-on-tone" : isNext ? "bg-brand text-brand-ink hover:bg-brand-strong"
                              : "border-[1.5px] border-border-input bg-surface hover:bg-surface-2"}`}>
                          <Check aria-hidden className="h-5 w-5" strokeWidth={2.6} />{t.taken}
                        </button>
                        <button onClick={() => mark(i, "skipped")} aria-pressed={i.status === "skipped"} aria-label={`${t.skip} ${i.name}`}
                          className={`inline-flex min-h-12 items-center gap-1 rounded-[14px] px-3 font-semibold
                            ${i.status === "skipped" ? "bg-text text-surface" : "bg-surface-2 hover:bg-border"}`}>
                          <X aria-hidden className="h-5 w-5" />{i.status === "skipped" ? t.skipped : t.skip}
                        </button>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        )}
    </Card>
  );
}

function untilText(time: string, t: any): string {
  const [h, m] = time.split(":").map(Number);
  const now = new Date();
  const mins = h * 60 + m - (now.getHours() * 60 + now.getMinutes());
  return mins <= 0 ? (mins > -60 ? t.dueNow : t.overdue) : mins < 60 ? fill(t.inMinutes, { n: mins })
    : fill(t.inHours, { h: Math.floor(mins / 60), m: mins % 60 });
}

/**
 * Home's dabba: one steel tray holding the day's katoris and the single next step (the only jamun Taken on Home).
 * The full Taken / Skip list lives on Medicines.
 */
export function DabbaTray() {
  const { t } = useI18n();
  const [d, setD] = useState<Doses | null>(null);
  const load = () => api<Doses>("/me/doses").then(setD).catch(() => setD({ items: [], progress: { done: 0, total: 0 } }));
  useEffect(() => {
    load();
    window.addEventListener("sushrut:data-changed", load);
    return () => window.removeEventListener("sushrut:data-changed", load);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const next = d ? nextOf(d.items) : null;
  async function take(item: Dose) {
    setD((cur) => cur && { ...cur, items: cur.items.map((i) => i === item ? { ...i, status: "taken" } : i) });
    await api("/me/doses", { method: "POST", body: JSON.stringify({ medication_id: item.medication_id, slot: item.slot, status: "taken" }) });
    window.dispatchEvent(new Event("sushrut:data-changed"));
  }
  const [open, setOpen] = useState(false);
  const total = d?.progress.total ?? 0, done = d?.progress.done ?? 0;
  const rest = (d?.items ?? []).filter((i) => i !== next && !i.status && i.slot !== "as_needed").length;
  // Compact strip: progress ring, the next dose with "Taken", and the day's slots behind a disclosure
  return (
    <section aria-labelledby="dabba-h" className="rounded-[20px] border border-border bg-surface px-3 py-2.5 shadow-[var(--rim),var(--e1)] sm:px-4">
      <h2 id="dabba-h" className="sr-only">{t.todayMeds}</h2>
      {!d ? <Skeleton className="h-14" /> : d.items.length === 0 ? (
        <div className="flex min-h-14 flex-wrap items-center gap-3">
          <span aria-hidden className="grid h-12 w-12 place-items-center rounded-full bg-surface-2"><Pill className="h-5 w-5 text-muted" /></span>
          <p className="min-w-0 flex-1 font-semibold">{t.noMedsToday}</p>
          <Link href="/medicines/rx" className="inline-flex min-h-12 items-center rounded-full bg-surface-2 px-3.5 text-[0.9375rem] font-semibold hover:bg-border">{t.addPrescription}</Link>
        </div>
      ) : (
        <>
          <p className="mb-1 flex justify-between text-[0.8125rem] font-semibold text-muted"><span>{t.todayMeds}</span>
            <span className="font-mono">{fill(t.dosesTakenOf, { n: d.progress.done, m: d.progress.total })}</span></p>
          {d.items.some((i) => i.time && i.slot !== "as_needed") ? <DoseLine items={d.items} /> : (
            <p className="flex min-h-12 items-center text-muted">{d.items.map((i) => i.name).join(", ")}</p>)}
        </>
      )}
    </section>
  );
}

/** Doses taken today in a katori well: an empty steel well with the count, filled dhania-green once every dose is
 *  taken (the number is the label; colour is never the only signal). */
function ProgressRing({ done, total, label }: { done: number; total: number; label: string }) {
  const full = total > 0 && done >= total;
  return (
    <span role="img" aria-label={label}
      className={`grid h-12 w-12 shrink-0 place-items-center rounded-full ${full ? "fill-in bg-ok text-on-tone shadow-[inset_0_3px_6px_rgb(0_0_0/22%)]" : "well text-text"}`}>
      {full ? <Check aria-hidden className="h-6 w-6" strokeWidth={3} /> : <span className="font-mono text-[0.9375rem] font-bold tabular-nums">{done}/{total}</span>}
    </span>
  );
}
