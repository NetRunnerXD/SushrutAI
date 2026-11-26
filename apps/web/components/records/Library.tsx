"use client";

import { ArrowDownUp, CheckCircle2, CircleAlert, FileText, FlaskConical, MessageCircle, Pill, ScanLine, Search, Trash2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { askHref } from "@/components/AskAbout";
import { EmptyState, Skeleton } from "@/components/ui/Card";
import { API, api, authFetch } from "@/lib/api";
import { fill, useI18n, bcp47 } from "@/lib/i18n";
import { useUiMode } from "@/lib/ui-mode";

export type Doc = { id: string; kind: string; mime: string; uploaded_at: string; report_date: string | null; title: string | null;
  reviewed: boolean; values: number; abnormal: number; abnormal_names: string[]; in_prescriptions: boolean };
type Filter = "all" | "reports" | "scans" | "rx" | "review" | "abnormal";
const SCAN = new Set(["xray", "ct", "mri", "skin", "other"]);

function Thumb({ d }: { d: Doc }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!d.mime.startsWith("image/")) return;
    let u = "";
    const io = new IntersectionObserver(() => {});
    authFetch(`${API}/documents/${d.id}/content`).then((r) => (r.ok ? r.blob() : null))
      .then((b) => { if (b) { u = URL.createObjectURL(b); setUrl(u); } }).catch(() => {});
    return () => { io.disconnect(); if (u) URL.revokeObjectURL(u); };
  }, [d.id, d.mime]);
  const Icon = d.kind === "prescription" ? Pill : SCAN.has(d.kind) ? ScanLine : FlaskConical;
  return url ? <img src={url} alt="" loading="lazy" className="h-20 w-16 shrink-0 rounded-[10px] object-cover object-top shadow-[var(--rim)]" />
    : <span aria-hidden className="grid h-20 w-16 shrink-0 place-items-center rounded-[10px] bg-surface-2 shadow-[var(--well)]"><Icon className="h-6 w-6 text-muted" /></span>;
}

/** Every uploaded document in one place: filter, search, sort; check values, ask about it, delete. */
export function Library({ onReview, refreshKey }: { onReview: (id: string) => void; refreshKey: number }) {
  const { t, lang } = useI18n();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [oldest, setOldest] = useState(false);
  const [confirm, setConfirm] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const desktop = useUiMode() === "desktop";
  const [sel, setSel] = useState<string | null>(null);
  const load = () => api<Doc[]>("/reports/library").then(setDocs).catch(() => setDocs([]));
  useEffect(() => { load(); }, [refreshKey]);

  const shown = useMemo(() => {
    let l = docs ?? [];
    if (filter === "reports") l = l.filter((d) => d.kind === "lab_report");
    if (filter === "scans") l = l.filter((d) => SCAN.has(d.kind));
    if (filter === "rx") l = l.filter((d) => d.kind === "prescription");
    if (filter === "review") l = l.filter((d) => d.kind === "lab_report" && !d.reviewed);
    if (filter === "abnormal") l = l.filter((d) => d.abnormal > 0);
    const s = q.trim().toLowerCase();
    if (s) l = l.filter((d) => [d.title, d.kind, ...d.abnormal_names].join(" ").toLowerCase().includes(s));
    const at = (d: Doc) => +new Date(d.report_date ?? d.uploaded_at);
    return [...l].sort((a, b) => (oldest ? at(a) - at(b) : at(b) - at(a)));
  }, [docs, filter, q, oldest]);
  const toCheck = (docs ?? []).filter((d) => d.kind === "lab_report" && !d.reviewed).length;

  async function remove(id: string) {
    try { await api(`/reports/${id}`, { method: "DELETE" }); setMsg(t.reportDeleted); setConfirm(null); load(); window.dispatchEvent(new Event("sushrut:data-changed")); }
    catch (e: any) { setMsg(e.message); }
  }
  const kindLabel = (k: string) => k === "lab_report" ? t.filterReports : k === "prescription" ? t.filterRx : t.filterScans;
  const FILTERS: [Filter, string, number?][] = [["all", t.filterAll], ["reports", t.filterReports], ["scans", t.filterScans],
    ["rx", t.filterRx], ["review", t.filterNeedsReview, toCheck], ["abnormal", t.filterAbnormal]];

  const search = (
        <label className="relative min-w-[12rem] flex-1">
          <span className="sr-only">{t.searchReports}</span>
          <Search aria-hidden className="pointer-events-none absolute start-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
          <input className="input min-h-12 ps-9" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.searchReports} />
        </label>);
  const sort = (
        <button type="button" onClick={() => setOldest(!oldest)} aria-pressed={oldest}
          className="inline-flex min-h-12 items-center gap-1.5 rounded-[14px] border-[1.5px] border-border-input bg-surface px-3 font-semibold">
          <ArrowDownUp aria-hidden className="h-4 w-4" />{oldest ? t.sortOldest : t.sortNewest}</button>);
  const filters = (
      <div role="group" aria-label={t.filterEvents} className={desktop ? "flex flex-col gap-1" : "no-scrollbar -mx-1 flex gap-2 overflow-x-auto px-1 pb-1"}>
        {FILTERS.map(([k, label, n]) => (
          <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)}
            className={desktop
              ? `flex h-11 items-center justify-between gap-2 rounded-[12px] px-3 text-start font-semibold transition-colors duration-[120ms] ${filter === k ? "bg-brand-soft text-text" : "text-muted hover:bg-[var(--d-tile-hover)] hover:text-text"}`
              : `inline-flex min-h-12 shrink-0 items-center gap-1.5 rounded-full border-[1.5px] px-3.5 text-[0.9375rem] font-semibold transition-colors
              ${filter === k ? "border-brand bg-brand-soft" : "border-border-input bg-surface hover:bg-surface-2"}`}>
            {label}{!!n && <span className="grid h-5 min-w-5 place-items-center rounded-full bg-warn px-1 text-[0.6875rem] font-bold text-on-tone">{n}</span>}
          </button>
        ))}
      </div>);
  const list = docs === null ? <Skeleton className="h-40" /> : shown.length === 0 ? <EmptyState icon={FileText} art="records" text={t.libraryEmpty} /> : (
        <ul className={desktop ? "d-stagger grid grid-cols-1 gap-3 min-[1280px]:grid-cols-2 min-[1920px]:grid-cols-3" : "grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3"}>
          {shown.map((d) => (
            <li key={d.id} onClick={desktop ? (e) => { if (!(e.target as HTMLElement).closest("a,button")) setSel(d.id); } : undefined}
              className={`flex gap-3 rounded-[18px] border bg-surface p-3 shadow-[var(--rim),var(--e1)] ${desktop ? `d-tile-hover cursor-pointer ${sel === d.id ? "border-[var(--d-accent-500)]" : "border-border"}` : "border-border"}`}>
              <Thumb d={d} />
              <div className="min-w-0 flex-1 space-y-1.5">
                <p className="text-[0.8125rem] font-semibold text-muted">{kindLabel(d.kind)} · {new Date(d.report_date ?? d.uploaded_at)
                  .toLocaleDateString(bcp47(lang), { day: "numeric", month: "short", year: "numeric" })}</p>
                <p className="truncate font-semibold">{d.title || kindLabel(d.kind)}</p>
                <div className="flex flex-wrap items-center gap-1.5 text-[0.8125rem]">
                  {d.kind === "lab_report" && (d.reviewed
                    ? <span className="inline-flex items-center gap-1 rounded-full bg-ok-soft px-2 py-0.5 font-semibold text-ok"><CheckCircle2 aria-hidden className="h-3.5 w-3.5" />{t.checked}</span>
                    : <span className="inline-flex items-center gap-1 rounded-full bg-warn-soft px-2 py-0.5 font-semibold text-warn"><CircleAlert aria-hidden className="h-3.5 w-3.5" />{t.needsReview}</span>)}
                  {d.values > 0 && <span className="text-muted">{fill(t.valuesCount, { n: d.values })}</span>}
                  {d.abnormal > 0 && <span className="font-semibold text-danger">· {fill(t.abnormalCount, { n: d.abnormal })}</span>}
                </div>
                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  {d.kind === "lab_report" && (
                    <button type="button" onClick={() => onReview(d.id)} className={`inline-flex min-h-12 items-center rounded-full px-3 text-[0.875rem] font-semibold ${d.reviewed ? "bg-surface-2 hover:bg-border" : "border-[1.5px] border-warn/60 bg-warn-soft text-warn"}`}>
                      {d.reviewed ? t.openReport : t.reviewAction}</button>)}
                  {d.kind === "prescription" && <Link href="/medicines/rx" className="inline-flex min-h-12 items-center rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">{t.openReport}</Link>}
                  {SCAN.has(d.kind) && <Link href={`/records?tab=images&doc=${d.id}`} className="inline-flex min-h-12 items-center rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">{t.openReport}</Link>}
                  {d.reviewed && d.abnormal_names[0] && (
                    <Link href={askHref(`doc:${d.id}`, `${d.title || kindLabel(d.kind)}: ${d.abnormal_names.join(", ")}`)}
                      className="inline-flex min-h-12 items-center gap-1 rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">
                      <MessageCircle aria-hidden className="h-4 w-4" />{t.explainThis}</Link>)}
                  {!d.in_prescriptions && (confirm === d.id ? (
                    <span className="inline-flex items-center gap-1">
                      <button type="button" onClick={() => remove(d.id)} className="btn-danger inline-flex min-h-12 items-center rounded-full border-[1.5px] border-danger px-3 text-[0.875rem] font-semibold">{t.delete}</button>
                      <button type="button" onClick={() => setConfirm(null)} className="inline-flex min-h-12 items-center rounded-full px-2 text-[0.875rem] font-semibold">{t.cancel}</button>
                    </span>
                  ) : (
                    <button type="button" aria-label={`${t.deleteReport}: ${d.title || kindLabel(d.kind)}`} onClick={() => setConfirm(d.id)}
                      className="grid h-12 w-12 place-items-center rounded-full text-muted hover:bg-danger-soft hover:text-danger"><Trash2 aria-hidden className="h-4 w-4" /></button>
                  ))}
                </div>
              </div>
            </li>
          ))}
        </ul>
      );

  if (desktop) {
    const picked = (docs ?? []).find((d) => d.id === sel) ?? shown[0];
    return (
      <div className="grid grid-cols-[220px_minmax(0,1fr)] gap-5 min-[1280px]:grid-cols-[220px_minmax(0,1fr)_340px] min-[1600px]:grid-cols-[240px_minmax(0,1fr)_380px]">
        <aside aria-label={t.filterEvents} className="space-y-3">{search}{filters}{sort}</aside>
        <div className="min-w-0 space-y-3">{msg && <p role="status" className="font-semibold text-ok">{msg}</p>}{list}</div>
        <div className="hidden min-[1280px]:block">{picked && <Preview d={picked} onReview={onReview} kindLabel={kindLabel} />}</div>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">{search}{sort}</div>
      {filters}
      {msg && <p role="status" className="font-semibold text-ok">{msg}</p>}
      {list}
    </div>
  );
}

/** Desktop preview pane: the selected report's values with flags, and its actions. */
function Preview({ d, onReview, kindLabel }: { d: Doc; onReview: (id: string) => void; kindLabel: (k: string) => string }) {
  const { t, lang } = useI18n();
  const [vals, setVals] = useState<any[] | null>(null);
  useEffect(() => {
    setVals(null);
    if (d.kind !== "lab_report") { setVals([]); return; }
    api<any>(`/reports/${d.id}`).then((r) => setVals(r.values ?? [])).catch(() => setVals([]));
  }, [d.id, d.kind]);
  return (
    <aside aria-label={t.openReport} className="d-tile sticky top-28 space-y-3 p-4">
      <div><p className="text-[0.8125rem] font-semibold text-muted">{kindLabel(d.kind)} · {new Date(d.report_date ?? d.uploaded_at)
        .toLocaleDateString(bcp47(lang), { day: "numeric", month: "long", year: "numeric" })}</p>
        <h2 className="text-[1.25rem] font-bold">{d.title || kindLabel(d.kind)}</h2></div>
      {vals === null ? <Skeleton className="h-32" /> : vals.length > 0 && (
        <ul className="max-h-[50vh] divide-y divide-[var(--d-tile-line)] overflow-y-auto">
          {vals.map((v, i) => (
            <li key={i} className="flex items-baseline justify-between gap-3 py-2 text-[0.9375rem]">
              <span className="min-w-0 truncate">{v.test_name}</span>
              <span className={`shrink-0 font-mono font-semibold ${v.flag === "H" || v.flag === "L" ? "text-danger" : ""}`}>
                {v.value ?? v.value_text ?? "—"} <span className="font-normal text-muted">{v.unit}</span>
                {v.flag === "H" ? ` ↑ ${t.flagHigh}` : v.flag === "L" ? ` ↓ ${t.flagLow}` : ""}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        {d.kind === "lab_report" && <button type="button" onClick={() => onReview(d.id)} className="btn btn-primary">{d.reviewed ? t.openReport : t.reviewAction}</button>}
        {d.reviewed && <Link href={askHref(`doc:${d.id}`, d.title || kindLabel(d.kind))} className="btn btn-outline"><MessageCircle aria-hidden className="h-4 w-4" />{t.explainThis}</Link>}
      </div>
    </aside>
  );
}
