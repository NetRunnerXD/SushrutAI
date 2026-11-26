"use client";

import {
  Activity, AlertTriangle, CheckCircle2, ClipboardList, Clock, FileSearch, FlaskConical,
  HeartPulse, HelpCircle, Lock, MessageCircle, Pill, ShieldAlert, ShieldCheck, Sparkles, Stethoscope, UserCheck,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
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
  const [ctx, setCtx] = useState<any>(null);
  const [selectedFact, setSelectedFact] = useState<any | null>(null);
  const [editText, setEditText] = useState("");
  const [editReason, setEditReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    api(`/clinical/context?lang=${encodeURIComponent(lang)}`)
      .then(setCtx)
      .catch(() => setCtx({ empty: true }));
  };
  useEffect(load, [lang]);

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
        <Link href="/ask" className="btn btn-primary min-h-12">
          <MessageCircle aria-hidden className="h-5 w-5" />{t.continueChat}
        </Link>
      </div>

      <Banner tone="info">{t.clinicalDisclaimerBar}</Banner>
      {ctx.returning_patient && ctx.prior_encounter_summary && (
        <Banner tone="success" title={t.returningPatientNote}>{ctx.prior_encounter_summary}</Banner>
      )}
      {msg && <Banner tone={msg === t.saved ? "success" : "warning"}>{msg}</Banner>}

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
                <ProvenanceChip status={f.verification_status} />
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
    </div>
  );
}
