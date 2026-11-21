"use client";

import { Download, FileText, FlaskConical, HeartPulse, ImageUp, ScanLine } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, EmptyState, PageTitle, Skeleton } from "@/components/ui/Card";
import { FlagTag, RangeBar } from "@/components/ui/Medical";
import { PANEL_ORDER, Sparkline, StatusTag, TrendChart, TrendWord, VITAL_META, panelOf, statusOf, trendOf, type Series } from "@/components/Trends";
import { Banner, DisclaimerChip } from "@/components/ui/Trust";
import { api, downloadFile } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { AskAbout } from "@/components/AskAbout";

type Tab = "timeline" | "labs" | "images" | "vitals";
const VITALS = [["bp_sys", "BP — upper (systolic)", "mmHg"], ["bp_dia", "BP — lower (diastolic)", "mmHg"], ["glucose_f", "Fasting sugar", "mg/dL"],
  ["glucose_pp", "Sugar after meal", "mg/dL"], ["hr", "Pulse", "bpm"], ["spo2", "Oxygen (SpO₂)", "%"], ["weight", "Weight", "kg"], ["temp", "Temperature", "°C"]];

async function upload(f: File, kind: string) {
  const fd = new FormData();
  fd.append("file", f);
  fd.append("kind", kind);
  return (await api<{ id: string }>("/documents", { method: "POST", body: fd })).id;
}


function RecordsInner() {
  const { t, lang } = useI18n();
  const params = useSearchParams();
  const startedDoc = useRef<string | null>(null);
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "timeline");
  const [timeline, setTimeline] = useState<any[] | null>(null);
  const [labs, setLabs] = useState<any[] | null>(null);
  const [onlyAbn, setOnlyAbn] = useState(false);
  const [vitals, setVitals] = useState<any[]>([]);
  const [vin, setVin] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [lab, setLab] = useState<any>(null);
  const [imgRes, setImgRes] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => {
    api<any[]>("/me/timeline").then(setTimeline).catch(() => setTimeline([]));
    api<any[]>("/reports/labs").then(setLabs).catch(() => setLabs([]));
    api<any[]>("/me/vitals").then(setVitals).catch(() => {});
  };
  useEffect(load, []);

  async function readLab(documentId: string) {
    setBusy("lab"); setMsg(null);
    try { setLab(await api("/reports/parse", { method: "POST", body: JSON.stringify({ document_id: documentId }) })); load(); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(null); }
  }
  async function describeImage(documentId: string) {
    setBusy("img"); setMsg(null); setImgRes(null);
    try { setImgRes(await api("/images/analyze", { method: "POST", body: JSON.stringify({ document_id: documentId, lang }) })); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(null); }
  }
  const onLab = async (f: File) => { try { await readLab(await upload(f, "lab_report")); } catch (e: any) { setMsg(e.message); } };
  const onImage = async (f: File) => { try { await describeImage(await upload(f, "xray")); } catch (e: any) { setMsg(e.message); } };
  // a document already uploaded elsewhere (attached in chat): read it straight away
  useEffect(() => {
    const doc = params.get("doc");
    if (!doc || startedDoc.current === doc) return; // effects run twice in development: read once
    startedDoc.current = doc;
    if (tab === "labs") readLab(doc);
    if (tab === "images") describeImage(doc);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  async function saveVitals(e: React.FormEvent) {
    e.preventDefault();
    const body = Object.entries(vin).filter(([, v]) => v).map(([type, value]) => ({ type, value: Number(value) }));
    if (!body.length) return;
    try { await api("/me/vitals", { method: "POST", body: JSON.stringify(body) }); setVin({}); setMsg(t.saved); load(); }
    catch (e: any) { setMsg(e.message); }
  }

  const tabs: [Tab, string, any][] = [["timeline", t.timeline, FileText], ["labs", t.labResults, FlaskConical],
    ["images", t.images, ScanLine], ["vitals", t.vitals, HeartPulse]];
  // labs: one row per test (latest result + its history), grouped into panels, panels with problems first
  const isAbn = (l: any) => l.flag === "H" || l.flag === "L";
  const byTest = new Map<string, any[]>();
  for (const l of labs ?? []) {
    const k = l.test_name.trim().toLowerCase();
    byTest.set(k, [...(byTest.get(k) ?? []), l]);
  }
  const panels = new Map<string, any[][]>();
  for (const hist of byTest.values()) {
    hist.sort((a, b) => +new Date(a.observed_at) - +new Date(b.observed_at));
    if (onlyAbn && !isAbn(hist[hist.length - 1])) continue;
    const p = panelOf(hist[0].test_name) ?? t.panelOther;
    panels.set(p, [...(panels.get(p) ?? []), hist]);
  }
  const panelRank = (p: string) => (PANEL_ORDER.indexOf(p) + 1 || 99);
  const panelList = [...panels.entries()].map(([name, tests]) => ({ name, tests, abn: tests.filter((h) => isAbn(h[h.length - 1])).length }))
    .sort((a, b) => (b.abn > 0 ? 1 : 0) - (a.abn > 0 ? 1 : 0) || panelRank(a.name) - panelRank(b.name));

  // vitals: one chart per measure; blood pressure pairs upper/lower readings taken together
  const pts = (type: string) => vitals.filter((v) => v.type === type)
    .map((v) => ({ t: +new Date(v.measured_at), v: v.value })).sort((a, b) => a.t - b.t);
  const sys = pts("bp_sys"), dia = pts("bp_dia");
  const pairs = sys.flatMap((s) => { const d = dia.find((q) => Math.abs(q.t - s.t) < 120_000); return d ? [{ t: s.t, s: s.v, d: d.v }] : []; });
  const charts: { key: string; title: string; unit: string; series: Series[] }[] = [];
  if (pairs.length) charts.push({ key: "bp", title: "Blood pressure", unit: "mmHg", series: [
    { name: "Upper (systolic)", color: "series-1", points: pairs.map((p) => ({ t: p.t, v: p.s })), lo: VITAL_META.bp_sys.lo, hi: VITAL_META.bp_sys.hi },
    { name: "Lower (diastolic)", color: "series-2", points: pairs.map((p) => ({ t: p.t, v: p.d })), lo: VITAL_META.bp_dia.lo, hi: VITAL_META.bp_dia.hi }] });
  for (const type of ["glucose_f", "glucose_pp", "glucose_r", "weight", "hr", "spo2", "temp", "rr", "waist"]) {
    const p = pts(type);
    const m = VITAL_META[type];
    if (p.length) charts.push({ key: type, title: m.label, unit: m.unit, series: [{ name: m.label, color: "series-1", points: p, lo: m.lo, hi: m.hi }] });
  }

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PageTitle>{t.recordsTitle}</PageTitle>
        <Button variant="secondary" icon={Download} onClick={() => downloadFile("/me/export/fhir", "sushrutai-health-record.json")}>{t.exportFhir}</Button>
      </div>

      <div role="tablist" aria-label={t.recordsTitle} className="grid grid-cols-4 gap-1 rounded-[6px] bg-surface-2 p-1">
        {tabs.map(([k, label, Icon]) => (
          <button key={k} role="tab" aria-selected={tab === k} id={`rt-${k}`} aria-controls={`rp-${k}`} onClick={() => setTab(k)}
            className={`flex min-h-12 flex-col items-center justify-center gap-0.5 rounded-[8px] text-[0.8125rem] font-semibold sm:flex-row sm:gap-2 sm:text-[0.9375rem]
              ${tab === k ? "bg-surface text-brand shadow-[var(--e1)]" : "text-muted"}`}>
            <Icon aria-hidden className="h-5 w-5" />{label}</button>
        ))}
      </div>
      {msg && <Banner tone={msg === t.saved ? "success" : "warning"}>{msg}</Banner>}

      <section role="tabpanel" id={`rp-${tab}`} aria-labelledby={`rt-${tab}`} className="space-y-4">
        {tab === "timeline" && (
          <Card>
            {timeline === null ? <Skeleton className="h-24" /> : timeline.length === 0 ? <EmptyState icon={FileText} text="Your prescriptions, reports and readings will appear here." /> : (
              <ol className="relative space-y-3 border-l-2 border-border pl-5">
                {timeline.slice(0, 60).map((e, i) => (
                  <li key={i}>
                    <span aria-hidden className="absolute -left-[9px] mt-1.5 h-4 w-4 rounded-full border-2 border-surface bg-brand" />
                    <p className="text-[0.8125rem] text-muted">{new Date(e.at).toLocaleString()}</p>
                    <p>{e.summary}</p>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        )}

        {tab === "labs" && (
          <>
            <label className="flex min-h-20 cursor-pointer items-center gap-4 rounded-[6px] border-2 border-dashed border-border-input bg-surface p-4">
              <FlaskConical aria-hidden className="h-8 w-8 text-brand" /><span className="font-semibold">{t.labUpload}</span>
              <input type="file" accept="image/*,application/pdf" className="sr-only" onChange={(e) => e.target.files?.[0] && onLab(e.target.files[0])} />
            </label>
            {busy === "lab" && <Card role="status" className="animate-pulse">{t.labReading}</Card>}
            {lab && <Banner tone={lab.abnormal?.length ? "danger" : "success"} title={lab.abnormal?.length ? t.abnormal : t.flagNormal}>
              {lab.abnormal?.length ? lab.abnormal.join(" · ") : null}<p className="mt-1">{lab.notice}</p></Banner>}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2>{t.labResults}</h2>
              <label className="flex min-h-12 items-center gap-2 font-semibold"><input type="checkbox" className="h-5 w-5" checked={onlyAbn} onChange={(e) => setOnlyAbn(e.target.checked)} />{t.onlyAbnormal}</label>
            </div>
            {labs === null ? <Skeleton className="h-24" /> : panelList.length === 0 ? <EmptyState icon={FlaskConical} art="records" text="No lab results yet." /> :
              panelList.map((g) => (
                <Card key={g.name}>
                  <CardHeader icon={FlaskConical} title={g.name} level={3} action={g.abn > 0 &&
                    <span className="rounded-full bg-danger-soft px-2.5 py-1 text-[0.8125rem] font-semibold text-danger">{fill(t.abnormalCount, { n: g.abn })}</span>} />
                  <ul className="divide-y divide-border">
                    {g.tests.map((hist) => {
                      const l = hist[hist.length - 1];
                      const nums = hist.filter((h: any) => h.value != null).map((h: any) => ({ t: +new Date(h.observed_at), v: h.value }));
                      return (
                        <li key={l.id} className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 py-3 sm:grid-cols-[1.2fr_auto_1fr_auto]">
                          <div><p className="font-semibold">{l.test_name}</p><p className="text-[0.75rem] text-muted">{new Date(l.observed_at).toLocaleDateString()}{l.loinc_code ? ` · LOINC ${l.loinc_code}` : ""}</p>
                            <div className="mt-1"><AskAbout compact refId={`lab:${l.id}`} label={`${l.test_name} ${l.value ?? l.value_text ?? ""} ${l.unit ?? ""}`.trim()} text={t.explainThis} /></div></div>
                          <div className="text-right"><p className="font-mono text-[1.125rem] font-semibold">{l.value ?? l.value_text ?? "—"} <span className="text-[0.8125rem] font-normal text-muted">{l.unit}</span></p><FlagTag flag={l.flag} /></div>
                          <div className="col-span-2 sm:col-span-1 sm:self-center"><RangeBar value={l.value} lo={l.ref_low} hi={l.ref_high} unit={l.unit} flag={l.flag} /></div>
                          {nums.length > 1 && (
                            <div className="col-span-2 flex items-center gap-2 sm:col-span-1 sm:flex-col sm:items-end sm:gap-0.5 sm:self-center">
                              <Sparkline points={nums} lo={l.ref_low} hi={l.ref_high} label={l.test_name} />
                              <TrendWord trend={trendOf(nums.map((n: any) => n.v))} />
                            </div>)}
                        </li>
                      );
                    })}
                  </ul>
                </Card>
              ))}
          </>
        )}

        {tab === "images" && (
          <>
            <label className="flex min-h-20 cursor-pointer items-center gap-4 rounded-[6px] border-2 border-dashed border-border-input bg-surface p-4">
              <ImageUp aria-hidden className="h-8 w-8 text-brand" /><span className="font-semibold">{t.imageUpload}</span>
              <input type="file" accept="image/*" className="sr-only" onChange={(e) => e.target.files?.[0] && onImage(e.target.files[0])} />
            </label>
            {busy === "img" && <Card role="status" className="animate-pulse">{t.imageAnalyzing}</Card>}
            {imgRes && (
              <Card className="space-y-3">
                {imgRes.supported ? (
                  <>
                    <Banner tone="warning" title={t.radiologistNeeded} />
                    {imgRes.low_confidence_warning && <Banner tone="warning">{imgRes.low_confidence_warning}</Banner>}
                    <p className="text-[0.875rem] capitalize text-muted">{imgRes.modality.replace("_", " ")}</p>
                    <p className="whitespace-pre-wrap">{imgRes.description}</p>
                    <DisclaimerChip />
                  </>
                ) : <p>{imgRes.message}</p>}
              </Card>
            )}
          </>
        )}

        {tab === "vitals" && (
          <>
            <Card>
              <CardHeader icon={HeartPulse} title={t.addReading} />
              <form onSubmit={saveVitals} className="grid gap-3 sm:grid-cols-2">
                {VITALS.map(([k, label, unit]) => (
                  <label key={k} className="text-[0.9375rem] font-semibold">{label} <span className="font-normal text-muted">({unit})</span>
                    <input className="input mt-1 font-mono text-[1.25rem]" inputMode="decimal" value={vin[k] ?? ""}
                           onChange={(e) => setVin((v) => ({ ...v, [k]: e.target.value.replace(/[^\d.]/g, "") }))} /></label>
                ))}
                <Button type="submit" size="lg" className="sm:col-span-2">{t.save}</Button>
              </form>
            </Card>
            {charts.length === 0 ? <EmptyState icon={HeartPulse} text={t.noReadings} /> : (
              <>
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  {charts.map((c) => {
                    const latest = c.series.map((s) => s.points[s.points.length - 1].v);
                    const worst = c.series.map((s, i) => statusOf(latest[i], s.lo, s.hi))
                      .sort((a, b) => ["high", "low", "normal", null].indexOf(a) - ["high", "low", "normal", null].indexOf(b))[0];
                    return (
                      <Card key={c.key} className="min-w-0 space-y-2">
                        <div className="flex items-center justify-between gap-2"><h3 className="text-[1.0625rem] font-semibold">{c.title}</h3>
                          <AskAbout compact refId={`vital:${c.key === "bp" ? "bp_sys" : c.key}`} label={c.title} /></div>
                        <p className="flex flex-wrap items-baseline gap-2">
                          <span className="font-mono text-[1.75rem] font-bold leading-none">{latest.map((v) => +v.toFixed(1)).join("/")}</span>
                          <span className="text-muted">{c.unit}</span>
                          <StatusTag status={worst} />
                          <TrendWord trend={trendOf(c.series[0].points.map((p) => p.v))} />
                        </p>
                        <TrendChart title={c.title} unit={c.unit} series={c.series} />
                      </Card>
                    );
                  })}
                </div>
                <p className="text-[0.875rem] text-muted">{t.normalBand}. {t.rangesNote}</p>
              </>
            )}
          </>
        )}
      </section>
    </div>
  );
}

export default function Records() {
  return <Suspense><RecordsInner /></Suspense>;
}
