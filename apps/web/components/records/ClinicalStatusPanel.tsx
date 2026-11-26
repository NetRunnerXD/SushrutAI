"use client";

import { Activity, AlertTriangle, CheckCircle2, Clock, HelpCircle, MessageCircle, Pill, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, EmptyState, Skeleton } from "@/components/ui/Card";
import { Banner, DisclaimerChip } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { bcp47, useI18n } from "@/lib/i18n";

const STATUS_STYLE: Record<string, string> = {
  active: "bg-warn-soft text-warn border-warn/40",
  worsening: "bg-danger-soft text-danger border-danger/50",
  improving: "bg-ok-soft text-ok border-ok/40",
  resolved: "bg-surface-2 text-muted border-border",
  unknown: "bg-surface-2 text-muted border-border",
};

function ProvenanceBadge({ status, source, t }: { status?: string; source?: string; t: any }) {
  const confirmed = status === "clinician_confirmed" || status === "confirmed";
  const unverified = status === "unverified" || status === "ai_hypothesis";
  const cls = confirmed
    ? "bg-ok-soft text-ok"
    : unverified
      ? "bg-warn-soft text-warn"
      : "bg-surface-2 text-muted";
  const label = confirmed
    ? t.verifiedConfirmed
    : unverified
      ? t.unverifiedTag
      : t.patientReportedTag;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[0.75rem] font-semibold ${cls}`}>
      {confirmed && <ShieldCheck aria-hidden className="h-3.5 w-3.5" />}
      {label}
      {source ? ` · ${source.replace(/_/g, " ")}` : ""}
    </span>
  );
}

export function ClinicalStatusPanel() {
  const { t, lang } = useI18n();
  const [data, setData] = useState<any>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () => {
    api(`/clinical/status?lang=${encodeURIComponent(lang)}`)
      .then(setData)
      .catch(() => setData({ active_symptoms: [], resolved_symptoms: [], pending_followups: [], medications: [], discrepancies: [] }));
  };
  useEffect(load, [lang]);

  async function updateRecord(
    entity_type: "symptom" | "medication" | "condition" | "allergy" | "medical_history",
    entity_id: string,
    action: "confirm" | "correct" | "resolve_discrepancy",
    updates: Record<string, unknown> = {},
    reason = "",
  ) {
    setBusyId(entity_id);
    setNotice(null);
    try {
      await api("/clinical/records/confirm", {
        method: "POST",
        body: JSON.stringify({ entity_type, entity_id, action, updates, reason }),
      });
      setNotice(t.saved);
      load();
    } catch (e: any) {
      setNotice(e.message);
    } finally {
      setBusyId(null);
    }
  }

  if (data === null) return <Skeleton className="h-44" />;

  const active: any[] = data.active_symptoms ?? [];
  const resolved: any[] = data.resolved_symptoms ?? [];
  const pending: any[] = data.pending_followups ?? [];
  const discrepancies: any[] = data.discrepancies ?? [];
  const meds: any[] = data.medications ?? [];

  return (
    <div className="space-y-4 lg:max-w-4xl">
      <Banner tone="info">{t.clinicalDisclaimerBar}</Banner>

      {data.returning_patient && (
        <Banner tone="success" title={t.returningPatientNote}>
          {data.prior_encounter_summary && <p className="mt-0.5 text-[0.9375rem]">{data.prior_encounter_summary}</p>}
        </Banner>
      )}

      {notice && <Banner tone={notice === t.saved ? "success" : "warning"}>{notice}</Banner>}

      {discrepancies.length > 0 && (
        <Card className="border-warn/50 bg-warn-soft/30">
          <CardHeader icon={AlertTriangle} title={t.discrepancyWarning} />
          <ul className="space-y-2">
            {discrepancies.map((d: any) => (
              <li key={d.id} className="flex flex-wrap items-center justify-between gap-2 rounded-[14px] border border-warn/40 bg-surface p-3">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{d.detail?.symptom || d.topic}</p>
                  <p className="text-[0.9375rem] text-muted">{d.detail?.note}</p>
                </div>
                {d.detail?.symptom_id && (
                  <Button
                    size="md"
                    variant="secondary"
                    disabled={busyId === d.detail.symptom_id}
                    onClick={() => updateRecord("symptom", d.detail.symptom_id, "resolve_discrepancy", {}, "User reviewed discrepancy")}
                  >
                    {t.resolveDiscrepancy}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}

      {pending.length > 0 && (
        <Card>
          <CardHeader icon={HelpCircle} title={t.prioritizedQuestion} />
          <ul className="space-y-2">
            {pending.map((fq: any) => (
              <li key={fq.id} className="flex flex-wrap items-center justify-between gap-3 rounded-[14px] border border-border bg-surface-2 p-3">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{fq.question}</p>
                  {fq.reason && <p className="text-[0.8125rem] text-muted">{fq.reason}</p>}
                </div>
                <Link href="/ask" className="btn btn-primary min-h-12 shrink-0">
                  <MessageCircle aria-hidden className="h-4 w-4" />{t.replyToQuestion}
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card>
        <CardHeader icon={Activity} title={t.activeSymptomsTitle} />
        {active.length === 0 ? (
          <EmptyState icon={Activity} art="records" text={t.noActiveSymptoms} />
        ) : (
          <ul className="space-y-3">
            {active.map((sx: any) => (
              <li key={sx.id} className="space-y-2 rounded-[16px] border border-border bg-surface-2 p-3.5">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="display text-[1.125rem] font-bold capitalize">{sx.name}</span>
                      <span className={`rounded-full border px-2.5 py-0.5 text-[0.8125rem] font-semibold capitalize ${STATUS_STYLE[sx.status] ?? STATUS_STYLE.unknown}`}>
                        {sx.status}
                      </span>
                      {sx.severity && sx.severity !== "unknown" && (
                        <span className="rounded-full bg-surface px-2.5 py-0.5 text-[0.8125rem] font-medium capitalize">{sx.severity}</span>
                      )}
                    </div>
                    {sx.original_statement && (
                      <p className="mt-1 text-[0.9375rem] text-muted">“{sx.original_statement}”</p>
                    )}
                  </div>
                  <ProvenanceBadge status={sx.verification_status} source={sx.source} t={t} />
                </div>

                <div className="flex flex-wrap gap-x-4 gap-y-1 text-[0.875rem] text-muted">
                  {(sx.duration_text || sx.onset_text) && (
                    <span className="inline-flex items-center gap-1">
                      <Clock aria-hidden className="h-4 w-4" />
                      {[sx.duration_text, sx.onset_text].filter(Boolean).join(" · ")}
                    </span>
                  )}
                  {sx.triggers?.length > 0 && <span>Triggers: {sx.triggers.join(", ")}</span>}
                  {sx.functional_impact && <span>Impact: {sx.functional_impact}</span>}
                  {sx.last_reported_at && (
                    <span>Updated: {new Date(sx.last_reported_at).toLocaleDateString(bcp47(lang))}</span>
                  )}
                </div>

                <div className="flex flex-wrap gap-2 pt-1">
                  {sx.verification_status !== "clinician_confirmed" && (
                    <Button
                      size="md"
                      variant="secondary"
                      icon={CheckCircle2}
                      disabled={busyId === sx.id}
                      onClick={() => updateRecord("symptom", sx.id, "confirm", {}, "Patient confirmed symptom details")}
                    >
                      {t.confirmAccuracy}
                    </Button>
                  )}
                  <Button
                    size="md"
                    variant="ghost"
                    disabled={busyId === sx.id}
                    onClick={() => updateRecord("symptom", sx.id, "correct", { status: "resolved" }, "Marked resolved by user")}
                  >
                    {t.markResolved}
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {meds.some((m) => m.unidentified) && (
        <Card>
          <CardHeader icon={Pill} title={t.medicinesLabel} />
          <ul className="space-y-2">
            {meds.filter((m) => m.unidentified).map((m) => (
              <li key={m.id} className="flex flex-wrap items-center justify-between gap-2 rounded-[14px] border border-warn/40 bg-warn-soft/30 p-3">
                <div>
                  <p className="font-semibold">{m.name}</p>
                  <p className="text-[0.875rem] text-warn">{t.unidentifiedMedBadge}</p>
                </div>
                <Link href="/medicines/rx" className="btn btn-secondary min-h-12">{t.rxUpload}</Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {resolved.length > 0 && (
        <Card>
          <CardHeader icon={CheckCircle2} title={t.resolvedSymptomsTitle} />
          <ul className="divide-y divide-border">
            {resolved.map((sx: any) => (
              <li key={sx.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                <div>
                  <span className="font-semibold capitalize">{sx.name}</span>
                  {sx.original_statement && <p className="text-[0.875rem] text-muted">“{sx.original_statement}”</p>}
                </div>
                <span className="text-[0.8125rem] text-muted">
                  {sx.resolved_at ? new Date(sx.resolved_at).toLocaleDateString(bcp47(lang)) : "resolved"}
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <DisclaimerChip />
    </div>
  );
}
