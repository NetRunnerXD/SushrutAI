"use client";

import { AlertTriangle, FlaskConical, HeartPulse, Stethoscope, UserRound, Utensils } from "lucide-react";

export type FamilyContextSummary = {
  member_id?: string;
  name?: string;
  relation?: string;
  age?: number | null;
  sex?: string | null;
  conditions?: string[];
  medications?: string[];
  allergies?: string[];
  latest_bp?: string | null;
};

export type AssessmentPanelData = {
  emergency?: boolean;
  emergency_messages?: string[];
  symptoms?: string[];
  differentials?: { condition: string; confidence?: string; confirmed?: boolean; next_step?: string }[];
  followup_questions?: string[];
  investigations?: { investigation_name: string; why_relevant?: string; urgency_tier?: string; clinician_decision_required?: boolean }[];
  routing?: {
    primary_department?: string;
    primary_department_local?: string;
    qualification?: string;
    primary_reason?: string;
    initial_vs_specialist?: string;
  };
  diet_lifestyle?: {
    helpful_foods?: string[];
    foods_to_limit?: string[];
    precaution_note?: string;
  };
};

export function MedicalAssessmentPanel({
  familyContext,
  assessment,
  chiefComplaint,
}: {
  familyContext?: FamilyContextSummary | null;
  assessment?: AssessmentPanelData | null;
  chiefComplaint?: string;
}) {
  if (!familyContext && !assessment) return null;

  return (
    <section
      aria-label="Automatic Medical Assessment"
      className="mt-2 space-y-2.5 rounded-[14px] border border-border bg-surface p-3 text-[0.875rem]"
    >
      {assessment?.emergency && (
        <div role="alert" className="flex items-start gap-2 rounded-[10px] bg-danger-soft p-2.5 text-danger">
          <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="space-y-1">
            <p className="font-bold">Emergency Warning — Seek Immediate Care (108 / 112)</p>
            {(assessment.emergency_messages || []).map((m, i) => (
              <p key={i} className="text-[0.8125rem]">{m}</p>
            ))}
          </div>
        </div>
      )}

      {familyContext && (
        <div className="flex flex-wrap items-center gap-2 rounded-[10px] bg-surface-2 px-2.5 py-2">
          <UserRound aria-hidden className="h-4 w-4 text-brand" />
          <span className="font-semibold">
            {familyContext.name || "Family Member"} ({familyContext.relation || "family"})
          </span>
          {familyContext.age != null && <span className="text-muted">· {familyContext.age} yrs</span>}
          {familyContext.latest_bp && <span className="text-muted">· BP {familyContext.latest_bp}</span>}
          {(familyContext.conditions || []).length > 0 && (
            <span className="text-muted">· {familyContext.conditions!.join(", ")}</span>
          )}
        </div>
      )}

      {(chiefComplaint || (assessment?.symptoms || []).length > 0) && (
        <div>
          <p className="font-semibold text-muted">Current Problem</p>
          {chiefComplaint && <p className="text-text">{chiefComplaint}</p>}
          {(assessment?.symptoms || []).length > 0 && (
            <p className="text-[0.8125rem] text-muted">Reported: {assessment!.symptoms!.join(", ")}</p>
          )}
        </div>
      )}

      {(assessment?.differentials || []).length > 0 && (
        <div>
          <p className="flex items-center gap-1.5 font-semibold text-muted">
            <HeartPulse aria-hidden className="h-3.5 w-3.5" />
            Possible Causes (Not a Confirmed Diagnosis)
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {assessment!.differentials!.map((d, i) => (
              <li key={i}>
                <span className="font-medium">{d.condition}</span>
                {d.confidence && <span className="text-muted"> ({d.confidence} preliminary)</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {(assessment?.investigations || []).length > 0 && (
        <div>
          <p className="flex items-center gap-1.5 font-semibold text-muted">
            <FlaskConical aria-hidden className="h-3.5 w-3.5" />
            Recommended Tests (Clinician Confirmation Required)
          </p>
          <ul className="mt-1 space-y-1">
            {assessment!.investigations!.map((inv, i) => (
              <li key={i} className="text-[0.8125rem]">
                <span className="font-medium">{inv.investigation_name}</span>
                {inv.urgency_tier && <span className="text-muted"> [{inv.urgency_tier}]</span>}
                {inv.why_relevant && <span className="text-muted"> — {inv.why_relevant}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {assessment?.routing?.primary_department && (
        <div>
          <p className="flex items-center gap-1.5 font-semibold text-muted">
            <Stethoscope aria-hidden className="h-3.5 w-3.5" />
            Doctor Recommendation
          </p>
          <p className="text-[0.8125rem]">
            <span className="font-medium">{assessment.routing.primary_department}</span>
            {assessment.routing.qualification && ` (${assessment.routing.qualification})`}
            {assessment.routing.primary_reason ? ` — ${assessment.routing.primary_reason}` : ""}
          </p>
        </div>
      )}

      {assessment?.diet_lifestyle && (
        <div>
          <p className="flex items-center gap-1.5 font-semibold text-muted">
            <Utensils aria-hidden className="h-3.5 w-3.5" />
            Diet & Precautions
          </p>
          {(assessment.diet_lifestyle.helpful_foods || []).length > 0 && (
            <p className="text-[0.8125rem]">Helpful: {assessment.diet_lifestyle.helpful_foods!.join("; ")}</p>
          )}
          {(assessment.diet_lifestyle.foods_to_limit || []).length > 0 && (
            <p className="text-[0.8125rem] text-muted">Limit: {assessment.diet_lifestyle.foods_to_limit!.join("; ")}</p>
          )}
        </div>
      )}

      {(assessment?.followup_questions || []).length > 0 && (
        <div>
          <p className="font-semibold text-muted">Next Steps / Follow-up</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[0.8125rem]">
            {assessment!.followup_questions!.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
