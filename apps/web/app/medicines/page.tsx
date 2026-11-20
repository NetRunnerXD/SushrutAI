"use client";

import { Camera, ChevronRight, FileText, Pill, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { DoseTimeline } from "@/components/Doses";
import { Card, CardHeader, EmptyState, PageTitle, Skeleton } from "@/components/ui/Card";
import { DosePictogram } from "@/components/ui/Medical";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { AskAbout } from "@/components/AskAbout";
import { AddMedicine } from "@/components/AddMedicine";
import { Refills } from "@/components/Refills";

const rs = (n?: number | null) => (n == null ? "—" : `₹${n.toFixed(2)}`);

export default function Medicines() {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<any[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [mine, setMine] = useState<any[] | null>(null);
  const [rx, setRx] = useState<any[]>([]);

  const loadMine = () => api<any[]>("/me/medications").then(setMine).catch(() => setMine([]));
  useEffect(() => {
    loadMine();
    api<any[]>("/prescriptions").then(setRx).catch(() => {});
  }, []);

  async function search(e?: React.FormEvent) {
    e?.preventDefault();
    if (q.trim().length < 2) return;
    setSearching(true);
    try { setResults((await api<any>(`/medicines/search?q=${encodeURIComponent(q)}`)).results); } finally { setSearching(false); }
  }

  const active = (mine ?? []).filter((m) => m.status === "active");
  return (
    <div className="space-y-5">
      <PageTitle>{t.medsTitle}</PageTitle>

      <form onSubmit={search} role="search" className="flex gap-2">
        <label htmlFor="msearch" className="sr-only">{t.medsSearch}</label>
        <div className="relative flex-1">
          <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-5 w-5 -translate-y-1/2 text-muted" />
          <input id="msearch" type="search" className="input pl-10" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.medsSearch} />
        </div>
        <button className="btn btn-primary" disabled={searching || q.trim().length < 2}><Search aria-hidden className="h-5 w-5" /><span className="sr-only sm:not-sr-only">Search</span></button>
      </form>

      {results && (
        <section aria-live="polite" aria-label="Search results">
          {results.length === 0 ? <p className="rounded-[12px] bg-surface-2 p-3 text-muted">No match. Try the medicine and strength, e.g. "Paracetamol 650".</p> : (
            <ul className="space-y-2">
              {results.map((p) => (
                <li key={p.id}>
                  <Link href={`/medicines/${p.id}`} className="flex min-h-16 items-center gap-3 rounded-[16px] border border-border bg-surface p-3 hover:border-border-input">
                    <span aria-hidden className="grid h-10 w-10 shrink-0 place-items-center rounded-full well text-text"><Pill className="h-5 w-5" /></span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-semibold">{p.name}</span>
                      <span className="flex flex-wrap gap-1.5 text-[0.8125rem]">
                        {p.is_janaushadhi && <span className="rounded bg-accent px-1.5 font-semibold text-accent-ink">{t.janAushadhi}</span>}
                        <span className="text-muted">{p.form} {p.release_type !== "IR" ? `· ${p.release_type}` : ""}</span>
                      </span>
                    </span>
                    <span className="text-right"><span className="block font-mono font-semibold">{rs(p.price?.per_unit_inr)}</span>
                      <span className="text-[0.75rem] text-muted">{t.perUnit}</span></span>
                    <ChevronRight aria-hidden className="h-5 w-5 text-muted" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <Link href="/medicines/rx" className="flex min-h-20 items-center gap-4 rounded-[20px] bg-brand p-4 text-brand-ink shadow-[inset_0_1px_0_rgb(255_255_255/18%),var(--e2)] hover:bg-brand-strong">
            <Camera aria-hidden className="h-8 w-8 shrink-0" />
            <span><span className="block text-[1.125rem] font-semibold">{t.addPrescription}</span>
              <span className="text-[0.9375rem] opacity-90">{t.rxUpload}</span></span>
          </Link>
          <Refills />
          <AddMedicine onAdded={loadMine} />
          <Card>
            <CardHeader title={t.myMedicines} />
            {mine === null ? <Skeleton className="h-16" /> : active.length === 0 ? (
              <EmptyState icon={Pill} art="meds" text={t.noMedsToday} />
            ) : (
              <ul className="divide-y divide-border">
                {active.map((m) => (
                  <li key={m.id} className="py-3 first:pt-0 last:pb-0">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><p className="font-semibold">{m.name}</p>
                      <AskAbout compact refId={`med:${m.id}`} label={m.name} /></div>
                    <DosePictogram frequency={m.frequency} timing={m.timing} />
                    {m.end_date && <p className="mt-1.5 text-[0.875rem] text-muted">Until {new Date(m.end_date).toLocaleDateString()}</p>}
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
        <div className="space-y-5">
          <DoseTimeline />
          {rx.length > 0 && (
            <Card>
              <CardHeader title={t.rxTitle} />
              <ul className="space-y-1">
                {rx.map((r) => (
                  <li key={r.id}>
                    <Link href={`/medicines/rx?id=${r.id}`} className="flex min-h-12 items-center justify-between gap-2 rounded-[12px] px-2 hover:bg-surface-2">
                      <span className="min-w-0 truncate">{r.medications?.map((m: any) => m.name).join(", ") || t.rxTitle}</span>
                      <span className={`shrink-0 rounded-full px-2 text-[0.8125rem] font-semibold ${r.status === "confirmed" ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"}`}>
                        {r.status === "confirmed" ? "✓" : t.rxReview}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
