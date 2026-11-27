"use client";

import { Activity, ChevronDown, ChevronRight, ClipboardCheck, Download, FileText, FlaskConical, FolderOpen, HeartPulse, History, ImageUp, NotebookPen, Pill, Plus, ScanLine, Sparkles, Users, Volume2 } from "lucide-react";
import { ClinicalStatusPanel } from "@/components/records/ClinicalStatusPanel";
import { ClinicalTimelinePanel } from "@/components/records/ClinicalTimelinePanel";
import { EncountersPanel } from "@/components/records/EncountersPanel";
import { FamilyNetworkPanel, InsightsAndSummariesPanel } from "@/components/records/IntelligenceHub";
import { Library } from "@/components/records/Library";
import { ReportReview } from "@/components/records/ReportReview";
import { speak } from "@/lib/voice";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, EmptyState, PageTitle, Skeleton } from "@/components/ui/Card";
import { FlagTag, RangeBar } from "@/components/ui/Medical";
import { PANEL_ORDER, Sparkline, StatusTag, TrendChart, TrendWord, VITAL_META, panelOf, statusOf, trendOf, type Series } from "@/components/Trends";
import { Banner, DisclaimerChip } from "@/components/ui/Trust";
import { api, downloadFile } from "@/lib/api";
import { fill, useI18n, bcp47 } from "@/lib/i18n";
import { AskAbout } from "@/components/AskAbout";
import { ScanViewer } from "@/components/ScanViewer";
import { ScreeningCard } from "@/components/ScreeningCard";
import { discardDoc, ImageCheckBanner, KindMismatchBanner, mismatch, READER_HREF, uploadDoc, type ImageIssue, type UploadResult } from "@/components/ImageCheck";

type Tab = "status" | "library" | "labs" | "timeline" | "encounters" | "images" | "vitals" | "family" | "insights";
const TABS: Tab[] = ["status", "library", "labs", "timeline", "encounters", "images", "vitals", "family", "insights"];
type EvType = "all" | "lab" | "prescription" | "vital" | "health";
const RANGES = [7, 30, 90, 0] as const;
const VITALS = [["bp_sys", "BP — upper (systolic)", "mmHg"], ["bp_dia", "BP — lower (diastolic)", "mmHg"], ["glucose_f", "Fasting sugar", "mg/dL"],
  ["glucose_pp", "Sugar after meal", "mg/dL"], ["hr", "Pulse", "bpm"], ["spo2", "Oxygen (SpO₂)", "%"], ["weight", "Weight", "kg"], ["temp", "Temperature", "°C"]];
const EVENT_ICON: Record<string, any> = { prescription: Pill, lab: FlaskConical, vital: HeartPulse, episode: Activity, health: NotebookPen };

/** A big, obvious file picker: the main action of the Labs and Images tabs. */
function UploadTile({ icon: Icon, title, help, accept, onFile, busy }: {
  icon: any; title: string; help: string; accept: string; onFile: (f: File) => void; busy?: boolean;
}) {
  return (
    <label className={`flex min-h-24 cursor-pointer items-center gap-4 rounded-[20px] border-[1.5px] border-dashed border-border-input bg-surface p-4 shadow-[var(--rim)] transition-colors hover:border-brand hover:bg-surface-2 ${busy ? "pointer-events-none opacity-60" : ""}`}>
      <span aria-hidden className="grid h-14 w-14 shrink-0 place-items-center rounded-full bg-brand-soft text-brand"><Icon className="h-7 w-7" /></span>
      <span className="min-w-0">
        <span className="display block text-[1.125rem] font-bold">{title}</span>
        <span className="block text-[0.9375rem] text-muted">{help}</span>
      </span>
      <input type="file" accept={accept} className="sr-only" disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) onFile(f); }} />
    </label>
  );
}

function dayLabel(d: Date, t: any, lang: string): string {
  const today = new Date(); const y = new Date(); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return t.today;
  if (d.toDateString() === y.toDateString()) return t.yesterday;
  return d.toLocaleDateString(bcp47(lang), { weekday: "short", day: "numeric", month: "short", year: "numeric" });
}

function RecordsInner() {
  const { t, lang } = useI18n();
  const params = useSearchParams();
  const router = useRouter();
  const startedDoc = useRef<string | null>(null);
  const [tab, setTabState] = useState<Tab>(TABS.includes(params.get("tab") as Tab) ? (params.get("tab") as Tab) : "library");
  const [review, setReview] = useState<string | null>(null);   // a report being checked against its document
  const [libKey, setLibKey] = useState(0);
  const [evType, setEvType] = useState<EvType>("all");
  const [range, setRange] = useState<(typeof RANGES)[number]>(90);
  const setTab = (k: Tab) => { setTabState(k); router.replace(`/records?tab=${k}`, { scroll: false }); };  // back/share keep the tab
  const [timeline, setTimeline] = useState<any[] | null>(null);
  const [labs, setLabs] = useState<any[] | null>(null);
  const [onlyAbn, setOnlyAbn] = useState(false);
  const [vitals, setVitals] = useState<any[]>([]);
  const [vin, setVin] = useState<Record<string, string>>({});
  const [addOpen, setAddOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [lab, setLab] = useState<any>(null);
  const [imgRes, setImgRes] = useState<any>(null);
  const [imgDoc, setImgDoc] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => {
    api<any[]>("/me/timeline").then(setTimeline).catch(() => setTimeline([]));
    api<any[]>("/reports/labs").then(setLabs).catch(() => setLabs([]));
    api<any[]>("/me/vitals").then(setVitals).catch(() => {});
  };
  useEffect(load, []);

  async function readLab(documentId: string) {
    setBusy("lab"); setMsg(null);
    try {
      const r = await api<any>("/reports/parse", { method: "POST", body: JSON.stringify({ document_id: documentId }) });
      setLab(null); setReview(r.document_id ?? documentId); setLibKey((k) => k + 1);
    }
    catch (e: any) { setMsg(e.message); } finally { setBusy(null); }
  }
  async function describeImage(documentId: string) {
    setBusy("img"); setMsg(null); setImgRes(null); setImgDoc(documentId);
    try { setImgRes(await api("/images/analyze", { method: "POST", body: JSON.stringify({ document_id: documentId, lang }) })); }
    catch (e: any) { setMsg(e.message); } finally { setBusy(null); }
  }
  // uploads are checked first (blur, darkness, size, health-related?); if something looks off, ask before reading
  const [check, setCheck] = useState<{ u: UploadResult; issue: ImageIssue; kind: "lab" | "image" } | null>(null);
  type Found = "prescription" | "lab_report" | "scan";
  const [wrong, setWrong] = useState<{ u: UploadResult; found: Found; kind: "lab" | "image" } | null>(null);
  const run = (id: string, kind: "lab" | "image") => (kind === "lab" ? readLab(id) : describeImage(id));
  // uploaded in the wrong place (a prescription as a lab report, a report as an X-ray...): offer the right reader
  const proceed = async (u: UploadResult, kind: "lab" | "image") => {
    const found = mismatch(kind, u);
    if (found) { setWrong({ u, found, kind }); return; }
    await run(u.id, kind);
  };
  const onUpload = async (f: File, kind: "lab" | "image") => {
    setCheck(null);
    setWrong(null);
    try {
      const d = await uploadDoc(f, kind === "lab" ? "lab_report" : "xray");
      if (d.issue) { setCheck({ u: d, issue: d.issue, kind }); return; }
      await proceed(d, kind);
    } catch (e: any) { setMsg(e.message); }
  };
  const switchTo = (w: { u: UploadResult; found: Found }) => {
    setWrong(null);
    if (w.found === "prescription") { location.href = READER_HREF.prescription(w.u.id); return; }
    const kind = w.found === "lab_report" ? "lab" : "image";
    setTab(kind === "lab" ? "labs" : "images");
    run(w.u.id, kind).catch((e: any) => setMsg(e.message));
  };
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
    try { await api("/me/vitals", { method: "POST", body: JSON.stringify(body) }); setVin({}); setAddOpen(false); setMsg(t.saved); load(); }
    catch (e: any) { setMsg(e.message); }
  }

  // labs: one row per test (latest result + its history), grouped into panels, panels with problems first
  const isAbn = (l: any) => l.flag === "H" || l.flag === "L";
  const byTest = new Map<string, any[]>();
  for (const l of labs ?? []) {
    const k = l.test_name.trim().toLowerCase();
    byTest.set(k, [...(byTest.get(k) ?? []), l]);
  }
  const latestAbn = [...byTest.values()].filter((h) => isAbn([...h].sort((a, b) => +new Date(a.observed_at) - +new Date(b.observed_at)).at(-1))).length;
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
  const since = range ? Date.now() - range * 86_400_000 : 0;
  const pts = (type: string) => vitals.filter((v) => v.type === type && +new Date(v.measured_at) >= since)
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

  // timeline: grouped by day, newest first
  const days: { label: string; items: any[] }[] = [];
  for (const e of (timeline ?? []).filter((x) => evType === "all" || x.type === evType || (evType === "health" && x.type === "episode")).slice(0, 120)) {
    const label = dayLabel(new Date(e.at), t, lang);
    if (days.at(-1)?.label !== label) days.push({ label, items: [] });
    days.at(-1)!.items.push(e);
  }
  // readings arrive as "bp_dia 86 mmHg": show the measure's name instead of its code
  const eventText = (e: any): string => {
    if (e.type !== "vital" || !e.summary) return e.summary || "—";
    const [code, ...rest] = e.summary.split(" ");
    return `${VITAL_META[code]?.label ?? code} ${rest.join(" ")}`;
  };
  const eventHref = (e: any): string | Tab =>
    e.type === "prescription" && e.id ? `/medicines/rx?id=${e.id}` : e.type === "lab" ? "labs"
      : e.type === "episode" || e.type === "health" ? "/ask" : "vitals";

  const tabs: [Tab, string, any, number?][] = [
    ["status", t.clinicalStatusTab, ClipboardCheck],
    ["library", t.library, FolderOpen],
    ["labs", t.labResults, FlaskConical, latestAbn],
    ["timeline", t.timeline, FileText],
    ["encounters", t.encountersTab, History],
    ["images", t.images, ScanLine],
    ["vitals", t.vitals, HeartPulse],
    ["family", "Family", Users],
    ["insights", "Insights", Sparkles],
  ];
  const readTest = (hist: any[]) => {
    const l = hist[hist.length - 1];
    const word = l.flag === "H" ? t.flagHigh : l.flag === "L" ? t.flagLow : l.flag === "N" ? t.flagNormal : "";
    const prev = hist.length > 1 ? hist[hist.length - 2] : null;
    speak(`${l.test_name}: ${l.value ?? l.value_text ?? ""} ${l.unit ?? ""}. ${word}.` +
      (prev ? ` ${t.history}: ${prev.value ?? prev.value_text} ${new Date(prev.observed_at).toLocaleDateString(bcp47(lang))}.` : ""), lang);
  };

  const vitalsForm = (
    <form onSubmit={saveVitals} className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-1">
      {VITALS.map(([k, label, unit]) => (
        <label key={k} className="text-[0.9375rem] font-semibold">{label} <span className="font-normal text-muted">({unit})</span>
          <input className="input mt-1 font-mono text-[1.25rem]" inputMode="decimal" value={vin[k] ?? ""}
                 onChange={(e) => setVin((v) => ({ ...v, [k]: e.target.value.replace(/[^\d.]/g, "") }))} /></label>
      ))}
      <Button type="submit" size="lg" className="sm:col-span-2 lg:col-span-1">{t.save}</Button>
    </form>
  );

  return (
    <div className="mx-auto max-w-6xl space-y-4 desk:max-w-none">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageTitle sub={t.recordsHelp}>{t.recordsTitle}</PageTitle>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" icon={Download} onClick={() => downloadFile("/records/report.pdf", "sushrutai-medical-report.pdf")}>
            <span className="max-sm:sr-only">PDF Report</span></Button>
          <Button variant="secondary" icon={Download} onClick={() => downloadFile("/me/export/fhir", "sushrutai-health-record.json")}>
            <span className="max-sm:sr-only">{t.exportFhir}</span></Button>
        </div>
      </div>

      <div role="tablist" aria-label={t.recordsTitle} className="well grid grid-cols-5 gap-1 rounded-[20px] p-1.5 sm:grid-cols-9 lg:max-w-6xl">
        {tabs.map(([k, label, Icon, count]) => (
          <button key={k} role="tab" aria-selected={tab === k} id={`rt-${k}`} aria-controls={`rp-${k}`} onClick={() => setTab(k)}
            className={`relative flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-[12px] text-[0.8125rem] font-semibold sm:flex-row sm:gap-2 sm:text-[0.9375rem]
              ${tab === k ? "bg-surface font-bold text-text shadow-[var(--rim),var(--e1)]" : "text-muted hover:text-text"}`}>
            <Icon aria-hidden className="h-5 w-5" />{label}
            {!!count && <span aria-label={fill(t.abnormalCount, { n: count })}
              className="absolute end-1.5 top-1 grid h-5 min-w-5 place-items-center rounded-full bg-danger px-1 text-[0.6875rem] font-bold text-on-tone sm:static">{count}</span>}
          </button>
        ))}
      </div>
      {msg && <Banner tone={[t.saved, t.valuesSaved, t.reportDeleted].includes(msg) ? "success" : "warning"}>{msg}</Banner>}
      {check && (
        <ImageCheckBanner issue={check.issue} accept={check.kind === "lab" ? "image/*,application/pdf" : "image/*"}
          onSend={() => { const c = check; setCheck(null); proceed(c.u, c.kind).catch((e: any) => setMsg(e.message)); }}
          onRetake={(f) => { discardDoc(check.u.id); onUpload(f, check.kind); }} />
      )}
      {wrong && (
        <KindMismatchBanner found={wrong.found} onSwitch={() => switchTo(wrong)}
          onKeep={() => { const w = wrong; setWrong(null); run(w.u.id, w.kind).catch((e: any) => setMsg(e.message)); }} />
      )}

      {review && (
        <section aria-label={t.reviewTitle} className="sheet-in rounded-[22px] border border-border bg-surface p-3 shadow-[var(--rim),var(--e2)] sm:p-4">
          <ReportReview docId={review} onDone={(r) => { setReview(null); setLibKey((k) => k + 1); load();
            setMsg(r === "saved" ? t.valuesSaved : r === "deleted" ? t.reportDeleted : null); }} />
        </section>
      )}

      <section role="tabpanel" id={`rp-${tab}`} aria-labelledby={`rt-${tab}`} className={`space-y-4 ${review ? "hidden" : ""}`}>
        {tab === "status" && <ClinicalStatusPanel />}
        {tab === "encounters" && <EncountersPanel />}
        {tab === "family" && <FamilyNetworkPanel />}
        {tab === "insights" && <InsightsAndSummariesPanel />}
        {tab === "library" && (
          <>
            <UploadTile icon={FlaskConical} title={t.labUpload} help={t.labUploadHelp} accept="image/*,application/pdf" onFile={(f) => onUpload(f, "lab")} busy={busy === "lab"} />
            {busy === "lab" && <Card role="status" className="animate-pulse">{t.labReading}</Card>}
            <Library onReview={setReview} refreshKey={libKey} />
          </>
        )}
        {tab === "timeline" && (
          <div role="group" aria-label={t.filterEvents} className="no-scrollbar flex gap-2 overflow-x-auto pb-1">
            {([["all", t.filterAll], ["lab", t.event_lab ?? t.filterReports], ["prescription", t.event_prescription ?? t.filterRx],
               ["vital", t.event_vital ?? t.vitals], ["health", t.event_health]] as [EvType, string][]).map(([k, l]) => (
              <button key={k} type="button" aria-pressed={evType === k} onClick={() => setEvType(k)}
                className={`inline-flex min-h-11 shrink-0 items-center rounded-full border-[1.5px] px-3.5 font-semibold ${evType === k ? "border-brand bg-brand-soft" : "border-border-input bg-surface"}`}>{l}</button>
            ))}
          </div>
        )}
        {tab === "timeline" && (
          <>
          <ClinicalTimelinePanel />
          {timeline === null ? <Skeleton className="h-40" /> : timeline.length === 0
            ? <EmptyState icon={FileText} art="records" text={t.timelineEmpty} />
            : (
              <div className="space-y-5 lg:max-w-3xl">
                {days.map((d) => (
                  <section key={d.label} aria-label={d.label}>
                    <h2 className="mb-2 text-[0.9375rem] font-bold uppercase tracking-wide text-muted">{d.label}</h2>
                    <ul className="overflow-hidden rounded-[18px] border border-border bg-surface shadow-[var(--rim),var(--e1)]">
                      {d.items.map((e, i) => {
                        const Icon = EVENT_ICON[e.type] ?? FileText;
                        const href = eventHref(e);
                        const body = (
                          <>
                            <span aria-hidden className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-surface-2 shadow-[var(--well)]"><Icon className="h-5 w-5" /></span>
                            <span className="min-w-0 flex-1">
                              <span className="block text-[0.8125rem] font-semibold text-muted">
                                {(t as any)[`event_${e.type}`] ?? e.type} · {new Date(e.at).toLocaleTimeString(bcp47(lang), { hour: "numeric", minute: "2-digit" })}
                              </span>
                              <span className="block truncate">{eventText(e)}</span>
                            </span>
                            <ChevronRight aria-hidden className="h-5 w-5 shrink-0 text-muted" />
                          </>
                        );
                        const cls = "flex min-h-16 w-full items-center gap-3 px-3 py-2 text-start hover:bg-surface-2 sm:px-4";
                        return (
                          <li key={i} className="border-b border-border last:border-0">
                            {href.startsWith("/") ? <Link href={href} className={cls}>{body}</Link>
                              : <button type="button" className={cls} onClick={() => setTab(href as Tab)}>{body}</button>}
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                ))}
              </div>
            )
          }
          </>
        )}

        {tab === "labs" && (
          <>
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <UploadTile icon={FlaskConical} title={t.labUpload} help={t.labUploadHelp} accept="image/*,application/pdf" onFile={(f) => onUpload(f, "lab")} busy={busy === "lab"} />
              {labs && labs.length > 0 && (
                <div className="flex items-center gap-4 rounded-[20px] border border-border bg-surface p-4 shadow-[var(--rim)]">
                  <div className="min-w-0 flex-1">
                    <p className="font-mono text-[1.75rem] font-bold leading-none">{byTest.size}</p>
                    <p className="text-[0.9375rem] text-muted">{t.labsTracked}</p>
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className={`font-mono text-[1.75rem] font-bold leading-none ${latestAbn ? "text-danger" : "text-ok"}`}>{latestAbn}</p>
                    <p className="text-[0.9375rem] text-muted">{t.abnormal}</p>
                  </div>
                </div>
              )}
            </div>
            {busy === "lab" && <Card role="status" className="animate-pulse">{t.labReading}</Card>}
            {lab && <Banner tone={lab.abnormal?.length ? "danger" : "success"} title={lab.abnormal?.length ? t.abnormal : t.flagNormal}>
              {lab.abnormal?.length ? lab.abnormal.join(" · ") : null}<p className="mt-1">{lab.notice}</p></Banner>}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2>{t.labResults}</h2>
              <label className="flex min-h-12 items-center gap-2 font-semibold"><input type="checkbox" className="h-5 w-5" checked={onlyAbn} onChange={(e) => setOnlyAbn(e.target.checked)} />{t.onlyAbnormal}</label>
            </div>
            {labs === null ? <Skeleton className="h-24" /> : panelList.length === 0 ? <EmptyState icon={FlaskConical} art="records" text={t.labsEmpty} /> : (
              <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
                {panelList.map((g) => (
                  <Card key={g.name} className="min-w-0">
                    <CardHeader icon={FlaskConical} title={g.name} level={3} action={g.abn > 0 &&
                      <span className="rounded-full bg-danger-soft px-2.5 py-1 text-[0.8125rem] font-semibold text-danger">{fill(t.abnormalCount, { n: g.abn })}</span>} />
                    <ul className="divide-y divide-border">
                      {g.tests.map((hist) => {
                        const l = hist[hist.length - 1];
                        const nums = hist.filter((h: any) => h.value != null).map((h: any) => ({ t: +new Date(h.observed_at), v: h.value }));
                        return (
                          <li key={l.id} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 py-3">
                            <div className="min-w-0"><p className="font-semibold">{l.test_name}</p>
                              <p className="text-[0.75rem] text-muted">{new Date(l.observed_at).toLocaleDateString()}{l.loinc_code ? ` · LOINC ${l.loinc_code}` : ""}</p></div>
                            <div className="text-end"><p className="font-mono text-[1.125rem] font-semibold">{l.value ?? l.value_text ?? "—"} <span className="text-[0.8125rem] font-normal text-muted">{l.unit}</span></p><FlagTag flag={l.flag} /></div>
                            <div className="col-span-2"><RangeBar value={l.value} lo={l.ref_low} hi={l.ref_high} unit={l.unit} flag={l.flag} /></div>
                            <div className="col-span-2 flex flex-wrap items-center justify-between gap-2">
                              <button type="button" onClick={() => readTest(hist)} aria-label={`${t.readTrend}: ${l.test_name}`}
                                className="grid h-11 w-11 place-items-center rounded-full bg-surface-2 hover:bg-border"><Volume2 aria-hidden className="h-4 w-4" /></button>
                              <AskAbout compact refId={`lab:${l.id}`} label={`${l.test_name} ${l.value ?? l.value_text ?? ""} ${l.unit ?? ""}`.trim()} text={nums.length > 1 ? t.explainTrend : t.explainThis} />
                              {nums.length > 1 && (
                                <span className="flex items-center gap-2"><Sparkline points={nums} lo={l.ref_low} hi={l.ref_high} label={l.test_name} />
                                  <TrendWord trend={trendOf(nums.map((n: any) => n.v))} /></span>)}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  </Card>
                ))}
              </div>
            )}
          </>
        )}

        {tab === "images" && (
          <>
            <UploadTile icon={ImageUp} title={t.imageUpload} help={t.imageUploadHelp} accept="image/*" onFile={(f) => onUpload(f, "image")} busy={busy === "img"} />
            {busy === "img" && (
              <Card role="status" className="flex items-center gap-3"><ScanLine aria-hidden className="h-6 w-6 animate-pulse text-muted" />
                <div><p className="font-semibold">{t.imageAnalyzing}</p><p className="text-[0.9375rem] text-muted">{t.imageAnalyzingHelp}</p></div></Card>
            )}
            {imgRes && (
              imgRes.supported ? (
                <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
                  <Card className="min-w-0 lg:sticky lg:top-24">
                    {imgDoc && <ScanViewer docId={imgDoc} findings={imgRes.screening?.flagged ?? []}
                      label={`${t.images}: ${imgRes.modality.replace("_", " ")}`} />}
                  </Card>
                  <div className="min-w-0 space-y-3">
                    <Banner tone="warning" title={t.radiologistNeeded} />
                    {imgRes.low_confidence_warning && <Banner tone="warning">{imgRes.low_confidence_warning}</Banner>}
                    {imgRes.screening && <ScreeningCard s={imgRes.screening} />}
                    <Card className="space-y-2">
                      <p className="text-[0.8125rem] font-semibold uppercase tracking-wide text-muted">{imgRes.modality.replace("_", " ")}</p>
                      <h2 className="text-[1.1875rem]">{t.imageResultTitle}</h2>
                      <p className="max-w-prose whitespace-pre-wrap">{imgRes.description}</p>
                      <DisclaimerChip />
                    </Card>
                  </div>
                </div>
              ) : <Banner tone="info">{imgRes.message}</Banner>
            )}
          </>
        )}

        {tab === "vitals" && (
          <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="min-w-0 space-y-4 lg:order-1">
              <div className="lg:hidden">
                <Button variant={addOpen ? "secondary" : "primary"} icon={addOpen ? ChevronDown : Plus} className="w-full"
                  aria-expanded={addOpen} onClick={() => setAddOpen((o) => !o)}>{t.addReading}</Button>
                {addOpen && <Card className="mt-3">{vitalsForm}</Card>}
              </div>
              <div role="group" aria-label={t.filterEvents} className="flex flex-wrap gap-2">
                {RANGES.map((d) => (
                  <button key={d} type="button" aria-pressed={range === d} onClick={() => setRange(d)}
                    className={`inline-flex min-h-11 items-center rounded-full border-[1.5px] px-3.5 font-semibold ${range === d ? "border-brand bg-brand-soft" : "border-border-input bg-surface"}`}>
                    {d ? fill(t.rangeDays, { n: d }) : t.rangeAll}</button>
                ))}
              </div>
              {charts.length === 0 ? <EmptyState icon={HeartPulse} text={t.noReadings} /> : (
                <>
                  <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
                    {charts.map((c) => {
                      const latest = c.series.map((s) => s.points[s.points.length - 1].v);
                      const worst = c.series.map((s, i) => statusOf(latest[i], s.lo, s.hi))
                        .sort((a, b) => ["high", "low", "normal", null].indexOf(a) - ["high", "low", "normal", null].indexOf(b))[0];
                      return (
                        <Card key={c.key} className="min-w-0 space-y-2">
                          <div className="flex items-center justify-between gap-2"><h3 className="text-[1.0625rem] font-semibold">{c.title}</h3>
                            <AskAbout compact refId={`vital:${c.key === "bp" ? "bp_sys" : c.key}`} label={c.title}
                              text={c.series[0].points.length > 1 ? t.explainTrend : undefined} /></div>
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
            </div>
            <Card className="hidden lg:sticky lg:top-24 lg:order-2 lg:block">
              <CardHeader icon={HeartPulse} title={t.addReading} level={2} />
              {vitalsForm}
            </Card>
          </div>
        )}
      </section>
    </div>
  );
}

export default function Records() {
  return <Suspense><RecordsInner /></Suspense>;
}
