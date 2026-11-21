"use client";

import { AlertTriangle, BadgeIndianRupee, Check, Info, MapPin, ShieldAlert } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ListenButton } from "@/components/Voice";
import { Card, PageTitle, Skeleton } from "@/components/ui/Card";
import { Banner, DisclaimerChip, SourceList } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { bcp47, fill, useI18n } from "@/lib/i18n";
import { speak } from "@/lib/voice";
import { AskAbout } from "@/components/AskAbout";

const rs = (n?: number | null) => (n == null ? "—" : `₹${n.toFixed(2)}`);
type Tab = "overview" | "how" | "effects" | "cheaper" | "warnings";

export default function MedicineDetail() {
  const { t, lang } = useI18n();
  const { id } = useParams<{ id: string }>();
  const [p, setP] = useState<any>(null);
  const [alt, setAlt] = useState<any>(null);
  const [g, setG] = useState<any>(null);
  const [tab, setTab] = useState<Tab>("overview");

  useEffect(() => {
    api<any>(`/medicines/${id}`).then((prod) => {
      setP(prod);
      const generic = prod.composition?.[0]?.generic;
      if (generic) api(`/medicines/guidance/${encodeURIComponent(generic)}?lang=${lang}&explain=true`).then(setG).catch(() => setG({}));
    });
    api(`/medicines/${id}/alternatives`).then(setAlt).catch(() => {});
  }, [id, lang]);

  if (!p) return <div className="space-y-3"><Skeleton className="h-10 w-2/3" /><Skeleton className="h-40" /></div>;
  const ex = g?.explanation;
  const tabs: [Tab, string][] = [["overview", t.overview], ["how", t.guidance], ["effects", t.sideEffects],
    ["cheaper", t.cheaperOptions], ["warnings", t.warnings]];
  const personal = g?.alerts ?? [];
  const say = (txt: string) => speak(txt, lang, bcp47(lang));

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PageTitle>{p.name}</PageTitle>
        <AskAbout refId={`product:${p.id}`} label={p.name} />
      </div>
      <div className="-mt-2 flex flex-wrap gap-1.5">
        {p.composition?.map((c: any) => (
          <span key={c.generic} className="rounded-full bg-brand-soft px-3 py-1 text-[0.9375rem] font-semibold text-brand">
            {c.generic} {c.strength ?? ""}{c.unit ?? ""}</span>
        ))}
        {p.form && <span className="rounded-full bg-surface-2 px-3 py-1 text-[0.9375rem]">{p.form}</span>}
        {p.release_type !== "IR" && <span className="rounded-full bg-surface-2 px-3 py-1 text-[0.9375rem]">{p.release_type}</span>}
        {p.is_janaushadhi && <span className="rounded-full bg-accent px-3 py-1 text-[0.9375rem] font-semibold text-accent-ink">{t.janAushadhi}</span>}
      </div>
      {p.price && (
        <p className="text-[0.9375rem]"><span className="font-mono text-[1.25rem] font-semibold">{rs(p.price.per_unit_inr)}</span> {t.perUnit} ·
          MRP {rs(p.price.mrp_inr)}<br /><span className="text-muted">{fill(t.priceSource, { source: p.price.source, date: p.price.observed_on })}</span></p>
      )}

      {personal.some((a: any) => a.level === "high") && (
        <Banner tone="danger" title={t.warnings}>{personal.filter((a: any) => a.level === "high").map((a: any, i: number) => <p key={i}>{a.text}</p>)}</Banner>
      )}

      <div role="tablist" aria-label={p.name} className="no-scrollbar -mx-4 flex gap-1 overflow-x-auto border-b border-border px-4">
        {tabs.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} aria-controls={`panel-${k}`} id={`tab-${k}`} onClick={() => setTab(k)}
            className={`min-h-12 shrink-0 border-b-[3px] px-3 font-semibold ${tab === k ? "border-brand text-brand" : "border-transparent text-muted"}`}>{label}</button>
        ))}
      </div>

      <section role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="space-y-4">
        {tab === "overview" && (
          <Card>
            {g === null ? <Skeleton className="h-20" /> : ex ? (
              <>
                <p className="text-[1.0625rem]">{ex.what_it_is_for}</p>
                <div className="mt-3"><ListenButton label={t.readAloud} onClick={() => say(ex.what_it_is_for)} /></div>
              </>
            ) : <p className="text-muted">{g?.note ?? "—"}</p>}
          </Card>
        )}

        {tab === "how" && (
          <Card className="space-y-3">
            {ex?.how_to_take?.length ? <ul className="list-disc space-y-1 pl-5">{ex.how_to_take.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul> : <p className="text-muted">—</p>}
            {ex?.with_food && <p className="rounded-[8px] bg-surface-2 p-3"><b>Food:</b> {ex.with_food}</p>}
            <Banner tone="info" icon={Info}>Always take it exactly as your doctor prescribed. Do not change the dose yourself.</Banner>
            {ex && <ListenButton label={t.readAloud} onClick={() => say([...(ex.how_to_take ?? []), ex.with_food ?? ""].join(". "))} />}
          </Card>
        )}

        {tab === "effects" && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Card><h2 className="mb-2 text-[1.0625rem]">{t.commonEffects}</h2>
              <ul className="list-disc space-y-1 pl-5">{(ex?.common_side_effects ?? []).map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></Card>
            <Card className="border-danger"><h2 className="mb-2 flex items-center gap-2 text-[1.0625rem] text-danger"><ShieldAlert aria-hidden className="h-5 w-5" />{t.seeDoctorIf}</h2>
              <ul className="list-disc space-y-1 pl-5">{(ex?.serious_side_effects_see_doctor ?? []).map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></Card>
          </div>
        )}

        {tab === "cheaper" && (
          <div className="space-y-3">
            {alt?.warnings?.map((w: string, i: number) => <Banner key={i} tone={i === 0 && alt.nti?.length ? "danger" : "warning"}>{w}</Banner>)}
            {alt?.banned_fdc?.map((b: any, i: number) => (
              <Banner key={i} tone={b.status === "in_force" ? "danger" : "warning"} icon={AlertTriangle} title="CDSCO">{b.description}{b.status !== "in_force" ? ` — ${b.status_note}` : ""}</Banner>
            ))}
            {!alt ? <Skeleton className="h-24" /> : alt.alternatives.length === 0 ? (
              <p className="rounded-[8px] bg-surface-2 p-3 text-muted">No other product with exactly the same composition, form and release type yet.</p>
            ) : (
              <ol className="space-y-2">
                {alt.alternatives.map((a: any) => (
                  <li key={a.id} className="rounded-[6px] border border-border bg-surface p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-semibold">{a.name}</p>
                        <p className="text-[0.875rem] text-muted">{a.manufacturer}</p>
                        <p className="mt-1 flex flex-wrap gap-x-3 text-[0.8125rem] text-ok">
                          {[t.sameComposition, t.sameForm, t.sameRelease].map((x) => <span key={x} className="inline-flex items-center gap-0.5"><Check aria-hidden className="h-3.5 w-3.5" />{x}</span>)}
                        </p>
                      </div>
                      <div className="shrink-0 text-right">
                        <p className="font-mono text-[1.125rem] font-semibold">{rs(a.price?.per_unit_inr)}</p>
                        <p className="text-[0.75rem] text-muted">{t.perUnit}</p>
                        {a.savings_pct > 0 && <p className="mt-1 inline-flex items-center gap-1 rounded-full bg-accent px-2 text-[0.8125rem] font-semibold text-accent-ink">
                          <BadgeIndianRupee aria-hidden className="h-3.5 w-3.5" />−{a.savings_pct}%</p>}
                        {a.is_janaushadhi && <p className="mt-1 text-[0.8125rem] font-semibold text-brand">{t.janAushadhi}</p>}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            )}
            <a href="https://www.google.com/maps/search/Jan+Aushadhi+Kendra+near+me" target="_blank" rel="noreferrer" className="btn btn-outline">
              <MapPin aria-hidden className="h-5 w-5" />{t.findKendra}</a>
          </div>
        )}

        {tab === "warnings" && (
          <div className="space-y-3">
            {personal.length === 0 && !ex?.do_not_take_if?.length ? <p className="text-muted">—</p> : null}
            {personal.map((a: any, i: number) => <Banner key={i} tone={a.level === "high" ? "danger" : "warning"}>{a.text}</Banner>)}
            {ex?.do_not_take_if?.length > 0 && (
              <Card><h2 className="mb-2 text-[1.0625rem]">{t.doNotTakeIf}</h2>
                <ul className="list-disc space-y-1 pl-5">{ex.do_not_take_if.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></Card>
            )}
          </div>
        )}
      </section>

      {g?.label?._meta && <SourceList sources={[{ n: 1, title: g.label._meta.source, url: g.label._meta.url }]} />}
      <DisclaimerChip />
    </div>
  );
}
