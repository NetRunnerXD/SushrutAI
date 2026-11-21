"use client";

import { Check, CalendarClock, Pill, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { Card, CardHeader, EmptyState, Skeleton } from "./ui/Card";
import { DosePictogram } from "./ui/Medical";

type Dose = { medication_id: string; name: string; slot: string; time: string | null; timing: string | null;
              frequency: any; status: "taken" | "skipped" | null; days_left: number | null };
type Doses = { items: Dose[]; progress: { done: number; total: number } };

function Ring({ done, total }: { done: number; total: number }) {
  const pct = total ? done / total : 0;
  const r = 22, c = 2 * Math.PI * r;
  return (
    <svg width="56" height="56" viewBox="0 0 56 56" aria-hidden className="shrink-0">
      <circle cx="28" cy="28" r={r} fill="none" stroke="var(--surface-2)" strokeWidth="6" />
      <circle cx="28" cy="28" r={r} fill="none" stroke="var(--ok)" strokeWidth="6" strokeLinecap="round"
              strokeDasharray={c} strokeDashoffset={c * (1 - pct)} transform="rotate(-90 28 28)" />
      <text x="28" y="33" textAnchor="middle" fontSize="15" fontWeight="700" fill="var(--text)">{done}/{total}</text>
    </svg>
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

  return (
    <Card>
      <CardHeader icon={CalendarClock} title={t.todayMeds}
        sub={d && d.progress.total ? fill(t.dosesProgress, d.progress) : undefined}
        action={d && d.progress.total ? <Ring {...d.progress} /> : undefined} />
      {!d ? <div className="space-y-2"><Skeleton className="h-16" /><Skeleton className="h-16" /></div>
        : d.items.length === 0 ? (
          <EmptyState icon={Pill} text={t.noMedsToday}
            action={<Link href="/medicines/rx" className="inline-flex min-h-12 items-center rounded-[8px] bg-brand px-4 font-semibold text-brand-ink">{t.addPrescription}</Link>} />
        ) : (
          <ol className="space-y-2">
            {d.items.map((i) => (
              <li key={i.medication_id + i.slot}
                  className={`rounded-[8px] border p-3 ${i.status === "taken" ? "border-ok bg-ok-soft" : i.status === "skipped" ? "border-border bg-surface-2" : "border-border"}`}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <p className="font-semibold">
                      <span className="mr-2 font-mono">{i.time ?? "—"}</span>{i.name}
                    </p>
                    {!compact && <div className="mt-1.5"><DosePictogram frequency={i.frequency} timing={i.timing} /></div>}
                    {i.days_left != null && i.days_left <= 3 && i.days_left >= 0 && (
                      <p className="mt-1 text-[0.875rem] font-semibold text-warn">{i.days_left} days left — plan a refill or check with your doctor</p>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => mark(i, "taken")} aria-pressed={i.status === "taken"}
                      className={`inline-flex min-h-12 items-center gap-1.5 rounded-[8px] px-3 font-semibold
                        ${i.status === "taken" ? "bg-ok text-on-tone" : "bg-surface border-[1.5px] border-border-input"}`}>
                      <Check aria-hidden className="h-5 w-5" />{i.status === "taken" ? t.taken : t.taken}
                    </button>
                    <button onClick={() => mark(i, "skipped")} aria-pressed={i.status === "skipped"} aria-label={`${t.skip} ${i.name}`}
                      className={`inline-flex min-h-12 items-center gap-1 rounded-[8px] px-3 font-semibold
                        ${i.status === "skipped" ? "bg-text text-surface" : "bg-surface-2"}`}>
                      <X aria-hidden className="h-5 w-5" />{i.status === "skipped" ? t.skipped : t.skip}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}
    </Card>
  );
}
