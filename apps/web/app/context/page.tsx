"use client";

import {
  Activity, AlertTriangle, CheckCircle2, ClipboardList, Clock, FileSearch, FlaskConical,
  HeartPulse, HelpCircle, Lock, MessageCircle, Pill, ShieldAlert, ShieldCheck,
  SlidersHorizontal, Sparkles, Stethoscope, Trash2, UserCheck,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { ContextGraphNavigator } from "@/components/ContextGraphNavigator";
import { useLiveTalk } from "@/components/LiveTalkProvider";
import { Card, CardHeader, PageTitle, Skeleton } from "@/components/ui/Card";
import { Sheet } from "@/components/ui/Sheet";
import { Banner, DisclaimerChip } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { bcp47, useI18n } from "@/lib/i18n";

const VER_META: Record<string, { label: string; cls: string; icon: any }> = {
  CLINICIAN_CONFIRMED: { label: "Clinician confirmed", cls: "bg-ok-soft text-ok border-ok/40", icon: ShieldCheck },
  MEASURED: { label: "Measured", cls: "bg-info-soft text-info border-info/40", icon: HeartPulse },
  IMPORTED_FROM_RECORD: { label: "Imported from record", cls: "bg-brand-soft text-brand border-brand/40", icon: FileSearch },
  PATIENT_REPORTED: { label: "Patient reported", cls: "bg-surface-2 text-text border-border", icon: UserCheck },
  AI_INFERRED: { label: "AI possibility (unconfirmed)", cls: "bg-warn-soft text-warn border-warn/40", icon: Sparkles },
};

function ProvenanceChip({ status }: { status?: string }) {
  const key = (status || "PATIENT_REPORTED").toUpperCase();
  const normKey = key.includes("CLINICIAN") || key === "CONFIRMED"
    ? "CLINICIAN_CONFIRMED"
    : key.includes("AI") || key === "UNVERIFIED"
      ? "AI_INFERRED"
      : VER_META[key]
        ? key
        : "PATIENT_REPORTED";
  const m = VER_META[normKey];
  const Icon = m.icon;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[0.75rem] font-semibold ${m.cls}`}>
      <Icon aria-hidden className="h-3.5 w-3.5" />
      <span>{m.label}</span>
    </span>
  );
}

/** Render transcript text highlighting exact extracted character span with accessible mark + context. */
function HighlightedTranscript({ text, start, end }: { text: string; start?: number | null; end?: number | null }) {
  if (!text) return null;
  if (start == null || end == null || start < 0 || end <= start || start >= text.length) {
    return <p className="rounded-[12px] bg-surface-2 p-3 text-[0.9375rem]">“{text}”</p>;
  }
  const pre = text.slice(0, start);
  const mid = text.slice(start, end);
  const post = text.slice(end);
  return (
    <p className="rounded-[12px] bg-surface-2 p-3 text-[0.9375rem] leading-relaxed">
      {pre}
      <mark className="rounded px-1 py-0.5 font-semibold bg-brand-soft text-text underline decoration-brand decoration-2">
        {mid}
      </mark>
      {post}
    </p>
  );
}

export default function PatientContextPage() {
  const { t, lang } = useI18n();
  const { live } = useLiveTalk();
  const [ctx, setCtx] = useState<any>(null);
  const [selectedFact, setSelectedFact] = useState<any | null>(null);
  const [editText, setEditText] = useState("");
  const [editReason, setEditReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [selCats, setSelCats] = useState<string[]>([]);
  const [selIds, setSelIds] = useState<Record<string, string[]>>({});
  const [clearing, setClearing] = useState(false);

  const load = () => {
    api(`/clinical/context?lang=${encodeURIComponent(lang)}`)
      .then(setCtx)
      .catch(() => setCtx({ empty: true }));
  };
  useEffect(load, [lang]);
  useEffect(() => {
    const onUpdated = () => load();
    window.addEventListener("sushrut:context-updated", onUpdated);
    const id = live.active ? setInterval(load, 4000) : null;
    return () => {
      window.removeEventListener("sushrut:context-updated", onUpdated);
      if (id) clearInterval(id);
    };
  }, [lang, live.active]);

  function openFact(fact: any) {
    setSelectedFact(fact);
    setEditText(typeof fact.value === "string" ? fact.value : JSON.stringify(fact.value));
    setEditReason("");
    setMsg(null);
  }

  async function saveFactCorrection() {
    if (!selectedFact) return;
    setBusy(true);
    setMsg(null);
    try {
      let parsed: Record<string, any> = { note: editText };
      try {
        const j = JSON.parse(editText);
        if (j && typeof j === "object") parsed = j;
      } catch {
        parsed = { ...selectedFact.value, corrected_text: editText };
      }
      await api(`/clinical/facts/${selectedFact.id}/correct`, {
        method: "POST",
        body: JSON.stringify({
          value: parsed,
          reason: editReason || "Patient correction from Context tab",
          actor_role: "patient",
          client_updated_at: new Date().toISOString(),
        }),
      });
      setSelectedFact(null);
      setMsg(t.saved);
      load();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function clearAllContext() {
    if (!window.confirm("Clear all context history (symptoms, facts, meds, relations, timeline, and assessments) for this profile?")) return;
    setClearing(true);
    setMsg(null);
    try {
      await api("/clinical/context", { method: "DELETE" });
      setMsg("All context history cleared.");
      load();
    } catch (e: any) {
      setMsg(e.message || "Could not clear context history.");
    } finally {
      setClearing(false);
    }
  }

  async function deleteSingleItem(cat: string, id: string) {
    setClearing(true);
    setMsg(null);
    try {
      await api("/clinical/context/delete", {
        method: "POST",
        body: JSON.stringify({ categories: [], item_ids: { [cat]: [id] } }),
      });
      setMsg("Item deleted from context.");
      load();
    } catch (e: any) {
      setMsg(e.message || "Could not delete item.");
    } finally {
      setClearing(false);
    }
  }

  async function submitCustomDelete() {
    if (selCats.length === 0 && Object.values(selIds).every((arr) => arr.length === 0)) return;
    setClearing(true);
    setMsg(null);
    try {
      await api("/clinical/context/delete", {
        method: "POST",
        body: JSON.stringify({ categories: selCats, item_ids: selIds }),
      });
      setDeleteOpen(false);
      setSelCats([]);
      setSelIds({});
      setMsg("Selected context items deleted.");
      load();
    } catch (e: any) {
      setMsg(e.message || "Could not delete selected items.");
    } finally {
      setClearing(false);
    }
  }

  function toggleCat(cat: string) {
    setSelCats((prev) => (prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat]));
  }

  function toggleItem(cat: string, id: string) {
    setSelIds((prev) => {
      const cur = prev[cat] ?? [];
      const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
      return { ...prev, [cat]: next };
    });
  }

  if (!ctx) return <div className="mx-auto max-w-5xl space-y-3"><Skeleton className="h-24" /><Skeleton className="h-44" /></div>;

  const facts: any[] = ctx.facts ?? [];
  const transcripts: Record<string, any> = ctx.transcripts ?? {};
  const complaints: any[] = ctx.current_complaints ?? [];
  const allSymptoms: any[] = ctx.symptom_details ?? [];
  const history: any[] = ctx.medical_history ?? [];
  const meds: any[] = ctx.medication_history ?? [];
  const allergies: any[] = ctx.allergies ?? [];
  const vitals: any[] = ctx.measurements_and_vitals ?? [];
  const functional: any[] = ctx.functional_impact ?? [];
  const timeline: any[] = ctx.temporal_timeline ?? [];
  const unknowns: any[] = ctx.unknown_information ?? [];
  const contradictions: any[] = ctx.contradictions ?? [];
  const assessment = ctx.clinical_assessment;
  const pendingQs: any[] = ctx.pending_questions ?? [];
  const investigations: any[] = ctx.recommended_next_steps?.investigations ?? [];

  const linkedTranscript = selectedFact?.transcript_id ? transcripts[selectedFact.transcript_id] : null;

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageTitle sub={t.contextPageSub}>{t.navContext}</PageTitle>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => { setSelCats([]); setSelIds({}); setDeleteOpen(true); }}
            className="btn btn-outline min-h-12 gap-2"
          >
            <SlidersHorizontal aria-hidden className="h-4 w-4" />Custom Delete
          </button>
          <button
            type="button"
            disabled={clearing}
            onClick={clearAllContext}
            className="btn btn-outline min-h-12 gap-2 border-danger/40 text-danger hover:bg-danger-soft/40"
          >
            <Trash2 aria-hidden className="h-4 w-4" />Clear All
          </button>
          <Link href="/ask" className="btn btn-primary min-h-12">
            <MessageCircle aria-hidden className="h-5 w-5" />{t.continueChat}
          </Link>
        </div>
      </div>

      <Banner tone="info">{t.clinicalDisclaimerBar}</Banner>
      {ctx.returning_patient && ctx.prior_encounter_summary && (
        <Banner tone="success" title={t.returningPatientNote}>{ctx.prior_encounter_summary}</Banner>
      )}
      {msg && <Banner tone={msg === t.saved ? "success" : "warning"}>{msg}</Banner>}

      <ContextGraphNavigator graph={ctx.neural_graph} liveActive={!!live.active} />

      {/* Extracted Facts with Clickable Source Transcript Highlighting */}
      {facts.length > 0 && (
        <Card>
          <CardHeader icon={FileSearch} title={t.ctxFacts} />
          <div className="space-y-2">
            {facts.slice(0, 20).map((f) => (
              <button
                key={f.id}
                type="button"
                onClick={() => openFact(f)}
                className="flex min-h-14 w-full flex-wrap items-center justify-between gap-2 rounded-[14px] border border-border bg-surface-2 p-3 text-start hover:border-brand"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[0.75rem] font-bold uppercase tracking-wide text-muted">{f.scope} · {f.category}</span>
                    <span className="font-semibold">{f.fact_key}</span>
                  </div>
                  {f.original_quote && <p className="truncate text-[0.875rem] text-muted">Source: “{f.original_quote}”</p>}
                </div>
                <div className="flex items-center gap-2">
                  <ProvenanceChip status={f.verification_status} />
                  <span
                    role="button"
                    tabIndex={0}
                    title="Delete fact"
                    aria-label={`Delete ${f.fact_key}`}
                    onClick={(e) => { e.stopPropagation(); deleteSingleItem("facts", f.id); }}
                    onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.stopPropagation(); deleteSingleItem("facts", f.id); } }}
                    className="rounded-lg p-1.5 text-muted hover:bg-danger-soft/40 hover:text-danger"
                  >
                    <Trash2 className="h-4 w-4" />
                  </span>
                </div>
              </button>
            ))}
          </div>
        </Card>
      )}

      {/* A & B: Current Complaints & Symptom Details */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader icon={Activity} title={t.ctxComplaints} />
          {complaints.length === 0 ? <p className="text-muted">{t.noActiveSymptoms}</p> : (
            <ul className="space-y-2">
              {complaints.map((s) => (
                <li key={s.id} className="rounded-[12px] border border-border bg-surface-2 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-bold capitalize">{s.name} ({s.status})</span>
                    <ProvenanceChip status={s.verification_status} />
                  </div>
                  {s.original_statement && <p className="mt-1 text-[0.875rem] text-muted">“{s.original_statement}”</p>}
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader icon={ClipboardList} title={t.ctxSymptomDetails} />
          {allSymptoms.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-2">
              {allSymptoms.map((s) => (
                <li key={s.id} className="rounded-[12px] bg-surface-2 p-3 text-[0.9375rem]">
                  <p className="font-semibold capitalize">{s.name} · {s.progression}</p>
                  <p className="text-[0.875rem] text-muted">
                    {[
                      s.duration_text && `Duration: ${s.duration_text}`,
                      s.severity && `Severity: ${s.severity}`,
                      s.frequency && `Frequency: ${s.frequency}`,
                      s.triggers?.length && `Triggers: ${s.triggers.join(", ")}`,
                    ].filter(Boolean).join(" · ") || "Details still being clarified"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* C, D, E: Medical History, Medications, Allergies (Patient-Level) */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader icon={Stethoscope} title={t.ctxHistory} />
          {history.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-1.5 text-[0.9375rem]">
              {history.map((h, i) => (
                <li key={h.id ?? i} className="rounded-[10px] bg-surface-2 p-2.5">
                  <p className="font-semibold">{h.description || h.display}</p>
                  <ProvenanceChip status={h.verification_status} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader icon={Pill} title={t.ctxMedications} />
          {meds.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-1.5 text-[0.9375rem]">
              {meds.map((m) => (
                <li key={m.id} className="rounded-[10px] bg-surface-2 p-2.5">
                  <p className="font-semibold">{m.name} {m.dose ? `(${m.dose})` : ""}</p>
                  <ProvenanceChip status={m.verification_status} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader icon={ShieldAlert} title={t.ctxAllergies} />
          {allergies.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-1.5 text-[0.9375rem]">
              {allergies.map((a) => (
                <li key={a.id} className="rounded-[10px] bg-danger-soft/40 p-2.5">
                  <p className="font-semibold text-danger">{a.substance}</p>
                  <ProvenanceChip status={a.verification_status} />
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* F & G: Measurements/Vitals & Functional Impact */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader icon={HeartPulse} title={t.ctxVitals} />
          {vitals.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-1.5">
              {vitals.slice(0, 10).map((v) => (
                <li key={v.id} className="flex flex-wrap items-center justify-between gap-2 rounded-[10px] bg-surface-2 p-2.5">
                  <span className="font-mono font-bold">{v.type}: {v.value} {v.unit}</span>
                  <ProvenanceChip status={v.is_estimate ? "PATIENT_REPORTED" : "MEASURED"} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader icon={Activity} title={t.ctxFunctional} />
          {functional.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-1.5 text-[0.9375rem]">
              {functional.map((fn, i) => (
                <li key={fn.id ?? i} className="rounded-[10px] bg-surface-2 p-2.5">
                  {fn.value?.impact || fn.impact} ({fn.value?.symptom || fn.symptom || t.ctxDailyLife})
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* H: Temporal Timeline */}
      <Card>
        <CardHeader icon={Clock} title={t.ctxTimeline} />
        {timeline.length === 0 ? <p className="text-muted">{t.timelineEmpty}</p> : (
          <ul className="divide-y divide-border text-[0.9375rem]">
            {timeline.slice(0, 20).map((ev) => (
              <li key={ev.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div>
                  <span className="font-semibold">{ev.event_type.replace(/_/g, " ")}</span>
                  <span className="ms-2 text-[0.875rem] text-muted">{ev.payload?.name || ev.entity_type}</span>
                </div>
                <span className="text-[0.8125rem] text-muted">
                  {ev.occurred_at ? new Date(ev.occurred_at).toLocaleString(bcp47(lang)) : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* I & J: Unknown Information & Contradictions */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader icon={HelpCircle} title={t.ctxUnknown} />
          {unknowns.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="list-disc space-y-1 ps-5 text-[0.9375rem]">
              {unknowns.slice(0, 10).map((u, i) => (
                <li key={i}>{typeof u === "string" ? u : `${u.fact_key}: ${u.uncertainty_note}`}</li>
              ))}
            </ul>
          )}
        </Card>

        <Card className={contradictions.length ? "border-warn/50 bg-warn-soft/20" : ""}>
          <CardHeader icon={AlertTriangle} title={t.ctxContradictions} />
          {contradictions.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-2 text-[0.9375rem]">
              {contradictions.map((c, i) => (
                <li key={c.id ?? i} className="rounded-[10px] border border-warn/40 bg-surface p-2.5">
                  {c.detail?.note || c.value?.note || c.topic}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* K: Clinical Assessment & Differential Considerations */}
      <Card>
        <CardHeader icon={Sparkles} title={t.ctxAssessment} />
        {!assessment ? <p className="text-muted">{t.none}</p> : (
          <div className="space-y-3">
            <p className="font-semibold">{assessment.summary}</p>
            <div className="grid gap-2.5 md:grid-cols-2">
              {(assessment.differentials ?? []).map((d: any) => (
                <div key={d.id} className="rounded-[14px] border border-border bg-surface-2 p-3 text-[0.875rem]">
                  <div className="flex flex-wrap items-center justify-between gap-1">
                    <span className="font-bold">{d.explanation}</span>
                    <ProvenanceChip status={d.review_status} />
                  </div>
                  {d.supporting_evidence?.length > 0 && <p className="mt-1 text-ok">{t.ctxFor}: {d.supporting_evidence.join("; ")}</p>}
                  {d.against_evidence?.length > 0 && <p className="text-warn">{t.ctxAgainst}: {d.against_evidence.join("; ")}</p>}
                  {d.missing_evidence?.length > 0 && <p className="text-muted">{t.ctxMissing}: {d.missing_evidence.join("; ")}</p>}
                  {d.next_assessment && <p className="mt-1 font-medium">{t.ctxNextCheck}: {d.next_assessment}</p>}
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>

      {/* L & M: Pending Questions & Patient-Dependent Investigations */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader icon={HelpCircle} title={t.ctxPending} />
          {pendingQs.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-2">
              {pendingQs.map((q) => (
                <li key={q.id} className="rounded-[12px] bg-surface-2 p-3">
                  <p className="font-semibold">{q.question}</p>
                  <p className="text-[0.8125rem] text-muted">{q.reason}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader icon={FlaskConical} title={t.ctxInvestigations} />
          {investigations.length === 0 ? <p className="text-muted">{t.none}</p> : (
            <ul className="space-y-2">
              {investigations.map((inv) => (
                <li key={inv.id} className="rounded-[12px] border border-border bg-surface-2 p-3 text-[0.875rem]">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-bold">{inv.name}</span>
                    <span className="rounded-full bg-surface px-2 py-0.5 text-[0.75rem] font-semibold uppercase">
                      {inv.purpose_category} · {inv.urgency}
                    </span>
                  </div>
                  <p className="mt-1">{inv.rationale}</p>
                  {inv.already_in_records && (
                    <p className="mt-1 font-semibold text-ok">{t.ctxAlreadyInRecords}</p>
                  )}
                  <p className="mt-0.5 text-[0.75rem] text-muted">{inv.limitations}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <DisclaimerChip />

      {/* Fact Source Inspector & Authorized Correction Sheet */}
      <Sheet open={!!selectedFact} onClose={() => setSelectedFact(null)} title={selectedFact?.fact_key || t.ctxClinicalFact}>
        {selectedFact && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <ProvenanceChip status={selectedFact.verification_status} />
              <span className="text-[0.8125rem] text-muted">
                {t.ctxSpeaker}: {selectedFact.speaker} · {selectedFact.observed_at ? new Date(selectedFact.observed_at).toLocaleString(bcp47(lang)) : ""}
              </span>
            </div>

            <div>
              <p className="mb-1 text-[0.875rem] font-bold text-muted">{t.rawTranscriptLabel}</p>
              <HighlightedTranscript
                text={linkedTranscript?.raw_text || selectedFact.original_quote}
                start={linkedTranscript ? selectedFact.span_start : null}
                end={linkedTranscript ? selectedFact.span_end : null}
              />
            </div>

            {linkedTranscript?.corrected_text && linkedTranscript.corrected_text !== linkedTranscript.raw_text && (
              <div>
                <p className="mb-1 text-[0.875rem] font-bold text-muted">{t.normalizedTranscriptLabel}</p>
                <p className="rounded-[12px] bg-surface-2 p-3 text-[0.9375rem]">{linkedTranscript.corrected_text}</p>
              </div>
            )}

            {selectedFact.verification_status === "CLINICIAN_CONFIRMED" ? (
              <Banner tone="info" icon={Lock}>
                This record is clinician-confirmed and locked against unauthorized edits.
              </Banner>
            ) : (
              <div className="space-y-2 border-t border-border pt-3">
                <label className="block text-[0.875rem] font-semibold">
                  Correct extracted value
                  <textarea rows={3} className="input mt-1 font-mono text-[0.875rem]" value={editText} onChange={(e) => setEditText(e.target.value)} />
                </label>
                <label className="block text-[0.875rem] font-semibold">
                  Reason for correction
                  <input className="input mt-1" value={editReason} onChange={(e) => setEditReason(e.target.value)} placeholder="e.g. Actually started five days ago" />
                </label>
                <Button size="lg" loading={busy} onClick={saveFactCorrection} icon={CheckCircle2}>{t.save}</Button>
              </div>
            )}
          </div>
        )}
      </Sheet>

      {/* Customized Context Deletion Sheet */}
      <Sheet open={deleteOpen} onClose={() => setDeleteOpen(false)} title="Custom Delete Context History">
        <div className="space-y-4">
          <p className="text-[0.875rem] text-muted">
            Select whole categories or pick individual items to remove from your clinical context and neural graph.
          </p>

          <div>
            <h3 className="mb-2 text-[0.8125rem] font-bold uppercase tracking-wide text-muted">Delete by Category</h3>
            <div className="grid gap-2 sm:grid-cols-2">
              {[
                { id: "facts", label: `Extracted Facts (${facts.length})` },
                { id: "relations", label: `Family & Friend Relations (${facts.filter((f) => f.category === "subject_relation").length})` },
                { id: "symptoms", label: `Symptoms (${allSymptoms.length})` },
                { id: "medications", label: `Medications (${meds.length})` },
                { id: "allergies", label: `Allergies (${allergies.length})` },
                { id: "vitals", label: `Vitals (${vitals.length})` },
                { id: "history", label: `Medical History (${history.length})` },
                { id: "timeline", label: `Timeline Events (${timeline.length})` },
                { id: "followups", label: `Pending Questions (${pendingQs.length})` },
                { id: "assessments", label: `Assessments (${assessment ? 1 : 0})` },
              ].map((cat) => (
                <label
                  key={cat.id}
                  className="flex cursor-pointer items-center gap-2.5 rounded-[12px] border border-border bg-surface-2 px-3 py-2.5 text-[0.875rem] font-medium hover:border-brand"
                >
                  <input
                    type="checkbox"
                    checked={selCats.includes(cat.id)}
                    onChange={() => toggleCat(cat.id)}
                    className="h-4 w-4 accent-brand"
                  />
                  <span>{cat.label}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="space-y-2.5">
            <h3 className="text-[0.8125rem] font-bold uppercase tracking-wide text-muted">Or Pick Individual Items</h3>
            <div className="max-h-60 space-y-2 overflow-y-auto pr-1">
              {allSymptoms.map((s) => (
                <label key={`sym-${s.id}`} className="flex cursor-pointer items-center gap-2.5 rounded-[10px] border border-border bg-surface px-3 py-2 text-[0.8125rem]">
                  <input type="checkbox" checked={(selIds.symptoms ?? []).includes(s.id)} onChange={() => toggleItem("symptoms", s.id)} className="h-4 w-4 accent-brand" />
                  <span className="font-semibold capitalize">Symptom: {s.name}</span>
                  <span className="text-muted">({s.status})</span>
                </label>
              ))}
              {meds.map((m) => (
                <label key={`med-${m.id}`} className="flex cursor-pointer items-center gap-2.5 rounded-[10px] border border-border bg-surface px-3 py-2 text-[0.8125rem]">
                  <input type="checkbox" checked={(selIds.medications ?? []).includes(m.id)} onChange={() => toggleItem("medications", m.id)} className="h-4 w-4 accent-brand" />
                  <span className="font-semibold">Med: {m.name}</span>
                </label>
              ))}
              {allergies.map((a) => (
                <label key={`all-${a.id}`} className="flex cursor-pointer items-center gap-2.5 rounded-[10px] border border-border bg-surface px-3 py-2 text-[0.8125rem]">
                  <input type="checkbox" checked={(selIds.allergies ?? []).includes(a.id)} onChange={() => toggleItem("allergies", a.id)} className="h-4 w-4 accent-brand" />
                  <span className="font-semibold text-danger">Allergy: {a.substance}</span>
                </label>
              ))}
              {vitals.map((v) => (
                <label key={`vit-${v.id}`} className="flex cursor-pointer items-center gap-2.5 rounded-[10px] border border-border bg-surface px-3 py-2 text-[0.8125rem]">
                  <input type="checkbox" checked={(selIds.vitals ?? []).includes(v.id)} onChange={() => toggleItem("vitals", v.id)} className="h-4 w-4 accent-brand" />
                  <span className="font-semibold">Vital: {v.type} {v.value} {v.unit}</span>
                </label>
              ))}
              {facts.slice(0, 20).map((f) => (
                <label key={`fact-${f.id}`} className="flex cursor-pointer items-center gap-2.5 rounded-[10px] border border-border bg-surface px-3 py-2 text-[0.8125rem]">
                  <input type="checkbox" checked={(selIds.facts ?? []).includes(f.id)} onChange={() => toggleItem("facts", f.id)} className="h-4 w-4 accent-brand" />
                  <span className="font-semibold">{f.category === "subject_relation" ? "Relation" : f.category}: {f.fact_key}</span>
                </label>
              ))}
            </div>
          </div>

          <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-3">
            <Button variant="secondary" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button
              loading={clearing}
              disabled={selCats.length === 0 && Object.values(selIds).every((arr) => arr.length === 0)}
              onClick={submitCustomDelete}
              icon={Trash2}
            >
              Delete Selected
            </Button>
          </div>
        </div>
      </Sheet>
    </div>
  );
}
