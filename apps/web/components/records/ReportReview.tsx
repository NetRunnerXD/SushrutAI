"use client";

import { AlertTriangle, Check, FileText, Minus, Plus, RotateCcw, Save, Trash2, X, ZoomIn } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { FlagTag } from "@/components/ui/Medical";
import { Banner } from "@/components/ui/Trust";
import { API, api, authFetch } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

export type Row = { test_name: string; loinc_code?: string | null; value: number | null; value_text?: string | null;
  unit?: string | null; ref_low?: number | null; ref_high?: number | null; flag?: string | null; ok?: boolean };

const num = (s: string): number | null => { const v = parseFloat(s.replace(",", ".")); return Number.isFinite(v) ? v : null; };
export const flagOf = (r: Row): string | null =>
  r.value == null || (r.ref_low == null && r.ref_high == null) ? null
    : r.ref_low != null && r.value < r.ref_low ? "L" : r.ref_high != null && r.value > r.ref_high ? "H" : "N";

/** Why a value deserves a second look: no unit or range on it, or a number far outside its own range. */
function doubt(r: Row, t: any): string | null {
  if (r.value == null && !r.value_text) return t.reviewNoValue;
  if (!r.unit) return t.reviewNoUnit;
  if (r.ref_low == null && r.ref_high == null) return t.reviewNoRange;
  const hi = r.ref_high ?? r.ref_low ?? 0;
  if (r.value != null && hi > 0 && (r.value > hi * 10 || r.value < (r.ref_low ?? hi) / 10)) return t.reviewOdd;
  return null;
}

/** Check the values read from a report against the document itself, correct them, then save — or discard or delete
 *  the report. The document is shown beside the values (stacked on phones, with Document / Values switch). */
export function ReportReview({ docId, onDone }: { docId: string; onDone: (result: "saved" | "discarded" | "deleted") => void }) {
  const { t } = useI18n();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [img, setImg] = useState<string | null>(null);
  const [mime, setMime] = useState<string>("image/png");
  const [zoom, setZoom] = useState(1);
  const [view, setView] = useState<"doc" | "values">("values");
  const [date, setDate] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    let url = "";
    api<any>(`/reports/${docId}`).then((r) => {
      setRows((r.values ?? []).map((v: Row) => ({ ...v, ok: !!r.reviewed })));
      setDate(r.report_date ?? new Date(r.uploaded_at).toISOString().slice(0, 10));
      setTitle(r.title ?? "");
      setMime(r.mime);
    }).catch((e) => setErr(e.message));
    authFetch(`${API}/documents/${docId}/content`)
      .then((r) => (r.ok ? r.blob() : null)).then((b) => { if (b) { url = URL.createObjectURL(b); setImg(url); } }).catch(() => {});
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [docId]);

  const set = (i: number, patch: Partial<Row>) => setRows((rs) => rs && rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const checked = rows?.filter((r) => r.ok).length ?? 0;
  const abnormal = useMemo(() => rows?.filter((r) => ["H", "L"].includes(flagOf(r) ?? "")).length ?? 0, [rows]);

  async function save() {
    if (!rows) return;
    setBusy(true); setErr(null);
    try {
      await api(`/reports/${docId}/confirm`, { method: "POST", body: JSON.stringify({
        report_date: date || null, title: title || null,
        results: rows.filter((r) => r.test_name.trim()).map(({ ok, flag, ...r }) => r) }) });
      window.dispatchEvent(new Event("sushrut:data-changed"));
      onDone("saved");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function del() {
    setBusy(true);
    try { await api(`/reports/${docId}`, { method: "DELETE" }); window.dispatchEvent(new Event("sushrut:data-changed")); onDone("deleted"); }
    catch (e: any) { setErr(e.message); setBusy(false); }
  }

  const docPane = (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 font-semibold"><FileText aria-hidden className="h-5 w-5" />{t.reviewDocument}</p>
        <div className="flex items-center gap-1" role="group" aria-label={t.zoom}>
          <button type="button" aria-label={t.zoomOut} onClick={() => setZoom((z) => Math.max(1, z - 0.5))} className="grid h-12 w-12 place-items-center rounded-full bg-surface-2"><Minus aria-hidden className="h-4 w-4" /></button>
          <span className="w-12 text-center font-mono text-[0.875rem]">{Math.round(zoom * 100)}%</span>
          <button type="button" aria-label={t.zoomIn} onClick={() => setZoom((z) => Math.min(4, z + 0.5))} className="grid h-12 w-12 place-items-center rounded-full bg-surface-2"><Plus aria-hidden className="h-4 w-4" /></button>
        </div>
      </div>
      <div className="h-[60vh] overflow-auto rounded-[14px] border border-border bg-surface-2 lg:h-[calc(100dvh-14rem)]">
        {!img ? <div className="skeleton h-full" /> : mime === "application/pdf"
          ? <iframe title={t.reviewDocument} src={img} className="h-full w-full" />
          : <img src={img} alt={t.reviewDocument} style={{ width: `${zoom * 100}%` }} className="max-w-none origin-top-left"
              onDoubleClick={() => setZoom((z) => (z >= 2.5 ? 1 : z + 1))} />}
      </div>
      <p className="flex items-center gap-1.5 text-[0.8125rem] text-muted"><ZoomIn aria-hidden className="h-4 w-4" />{t.reviewZoomHelp}</p>
    </div>
  );

  const valuesPane = (
    <div className="min-w-0 space-y-3">
      <Banner tone="info" title={t.reviewTitle}>{t.reviewHelp}</Banner>
      <div className="grid gap-2 sm:grid-cols-2">
        <label className="text-[0.9375rem] font-semibold">{t.reportDate}
          <input type="date" className="input mt-1" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="text-[0.9375rem] font-semibold">{t.reportName}
          <input className="input mt-1" value={title} maxLength={120} placeholder={t.reportNameHint} onChange={(e) => setTitle(e.target.value)} /></label>
      </div>
      {rows === null ? <div className="skeleton h-40" /> : rows.length === 0 ? <p className="text-muted">{t.reviewNothingRead}</p> : (
        <>
          <p aria-live="polite" className="text-[0.9375rem] font-semibold">{fill(t.reviewProgress, { n: checked, m: rows.length })}
            {abnormal > 0 && <span className="ms-2 text-danger">· {fill(t.abnormalCount, { n: abnormal })}</span>}</p>
          <ul className="space-y-2">
            {rows.map((r, i) => {
              const why = doubt(r, t);
              const f = flagOf(r);
              return (
                <li key={i} className={`rounded-[14px] border-[1.5px] p-2.5 ${r.ok ? "border-ok/50 bg-ok-soft/40" : why ? "border-warn/60 bg-warn-soft/50" : "border-border bg-surface"}`}>
                  <div className="flex flex-wrap items-center gap-2">
                    <input aria-label={t.testName} className="input min-h-12 min-w-[9rem] flex-1 font-semibold" value={r.test_name}
                      onChange={(e) => set(i, { test_name: e.target.value, ok: false })} />
                    <FlagTag flag={f} />
                    <button type="button" aria-pressed={!!r.ok} onClick={() => set(i, { ok: !r.ok })}
                      className={`inline-flex min-h-12 items-center gap-1 rounded-full px-3 text-[0.875rem] font-semibold ${r.ok ? "bg-ok text-on-tone" : "bg-surface-2"}`}>
                      <Check aria-hidden className="h-4 w-4" />{t.looksRight}</button>
                    <button type="button" aria-label={`${t.removeRow}: ${r.test_name}`} onClick={() => setRows((rs) => rs && rs.filter((_, j) => j !== i))}
                      className="grid h-12 w-12 place-items-center rounded-full hover:bg-surface-2"><X aria-hidden className="h-4 w-4" /></button>
                  </div>
                  <div className="mt-2 grid grid-cols-4 gap-2">
                    {([["value", t.value], ["unit", t.unit], ["ref_low", t.rangeLow], ["ref_high", t.rangeHigh]] as const).map(([k, label]) => (
                      <label key={k} className="text-[0.75rem] font-semibold text-muted">{label}
                        <input className="input mt-0.5 min-h-12 px-2 font-mono text-[1rem]" inputMode={k === "unit" ? "text" : "decimal"}
                          value={(r as any)[k] ?? ""} onChange={(e) => set(i, { ok: false, [k]: k === "unit" ? e.target.value : num(e.target.value),
                            ...(k === "value" ? { value_text: e.target.value } : {}) })} />
                      </label>
                    ))}
                  </div>
                  {why && !r.ok && <p className="mt-1.5 flex items-center gap-1.5 text-[0.8125rem] font-semibold text-warn"><AlertTriangle aria-hidden className="h-4 w-4" />{why}</p>}
                </li>
              );
            })}
          </ul>
          <button type="button" onClick={() => setRows((rs) => [...(rs ?? []), { test_name: "", value: null, unit: "", ok: false }])}
            className="inline-flex min-h-12 items-center gap-1.5 font-semibold text-info"><Plus aria-hidden className="h-4 w-4" />{t.addValue}</button>
        </>
      )}
      {err && <Banner tone="warning">{err}</Banner>}
      <div className="sticky bottom-0 -mx-1 flex flex-wrap gap-2 border-t border-border bg-surface px-1 py-3">
        <button type="button" onClick={save} disabled={busy || !rows?.length} className="btn btn-primary"><Save aria-hidden className="h-5 w-5" />{t.saveValues}</button>
        <button type="button" onClick={() => setRows((rs) => rs && rs.map((r) => ({ ...r, ok: true })))} disabled={!rows?.length} className="btn btn-outline">
          <Check aria-hidden className="h-5 w-5" />{t.allLookRight}</button>
        <button type="button" onClick={() => onDone("discarded")} className="btn btn-ghost"><RotateCcw aria-hidden className="h-5 w-5" />{t.decideLater}</button>
        {!confirmDelete ? (
          <button type="button" onClick={() => setConfirmDelete(true)} className="btn btn-ghost ms-auto text-danger"><Trash2 aria-hidden className="h-5 w-5" />{t.deleteReport}</button>
        ) : (
          <span role="alertdialog" aria-label={t.deleteReportQ} className="ms-auto flex flex-wrap items-center gap-2 rounded-[14px] bg-danger-soft px-3 py-1.5 text-danger">
            <span className="font-semibold">{t.deleteReportQ}</span>
            <button type="button" onClick={del} disabled={busy} className="btn btn-danger min-h-12">{t.delete}</button>
            <button type="button" onClick={() => setConfirmDelete(false)} className="btn btn-ghost min-h-12">{t.cancel}</button>
          </span>
        )}
      </div>
    </div>
  );

  return (
    <div className="space-y-3">
      <div role="tablist" aria-label={t.reviewTitle} className="well grid grid-cols-2 gap-1 rounded-[16px] p-1 lg:hidden">
        {(["values", "doc"] as const).map((k) => (
          <button key={k} role="tab" aria-selected={view === k} onClick={() => setView(k)}
            className={`min-h-12 rounded-[12px] font-semibold ${view === k ? "bg-surface shadow-[var(--rim)]" : "text-muted"}`}>
            {k === "doc" ? t.reviewDocument : t.reviewValues}</button>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className={view === "doc" ? "" : "max-lg:hidden"}>{docPane}</div>
        <div className={view === "values" ? "" : "max-lg:hidden"}>{valuesPane}</div>
      </div>
    </div>
  );
}
