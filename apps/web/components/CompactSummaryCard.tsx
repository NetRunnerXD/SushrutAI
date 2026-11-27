"use client";

import { Check, Download, FileText, Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export type CompactPatientSummary = {
  session_id: string;
  generated_at?: string;
  patient_information: {
    patient_name: string;
    age: string | number;
    preferred_language: string;
  };
  chief_complaint: {
    main_problem: string;
    symptom_chronology?: string[];
  };
  symptom_details: {
    duration: string;
    location: string;
    severity: string;
    frequency: string;
    associated_symptoms: string;
  };
  medical_history: {
    previous_conditions: string;
    current_medications: string;
    known_allergies: string;
    relevant_family_history: string;
  };
  ai_assessment: {
    possible_explanations: string;
    important_warning_signs: string;
    assessment_limitations: string;
  };
  recommendations: {
    recommended_doctor: string;
    reason_for_referral: string;
    possible_investigations: string;
    general_food_guidance: string;
    lifestyle_guidance: string;
  };
  follow_up: {
    questions_requiring_clarification: string;
    recommended_next_steps: string;
  };
};

const LABELS: Record<string, Record<string, string>> = {
  bn: {
    title: "সংক্ষিপ্ত রোগী সারসংক্ষেপ",
    patientInfo: "রোগীর তথ্য",
    name: "রোগীর নাম",
    age: "বয়স",
    lang: "পছন্দের ভাষা",
    chief: "মূল সমস্যা",
    details: "উপসর্গের বিবরণ",
    history: "চিকিৎসা ইতিহাস",
    assessment: "AI মূল্যায়ন",
    recs: "পরামর্শ",
    followup: "ফলো-আপ",
    edit: "তথ্য সংশোধন",
    download: "ডাউনলোড",
    del: "মুছুন",
    save: "সংরক্ষণ করুন",
  },
  hi: {
    title: "संक्षिप्त रोगी सारांश",
    patientInfo: "रोगी की जानकारी",
    name: "रोगी का नाम",
    age: "उम्र",
    lang: "पसंदीदा भाषा",
    chief: "मुख्य समस्या",
    details: "लक्षण विवरण",
    history: "चिकित्सा इतिहास",
    assessment: "AI आकलन",
    recs: "सिफ़ारिशें",
    followup: "फ़ॉलो-अप",
    edit: "सुधारें",
    download: "डाउनलोड",
    del: "हटाएँ",
    save: "सहेजें",
  },
  en: {
    title: "Compact Patient Summary",
    patientInfo: "Patient Information",
    name: "Patient Name",
    age: "Age",
    lang: "Preferred Language",
    chief: "Chief Complaint",
    details: "Symptom Details",
    history: "Medical History",
    assessment: "AI Assessment",
    recs: "Recommendations",
    followup: "Follow-Up",
    edit: "Correct Details",
    download: "Download",
    del: "Delete",
    save: "Save",
  },
};

export function CompactSummaryCard({
  summary,
  onUpdate,
  onDelete,
}: {
  summary: CompactPatientSummary | null;
  onUpdate?: (next: CompactPatientSummary) => void;
  onDelete?: () => void;
}) {
  const { lang } = useI18n();
  const L = LABELS[lang] ?? LABELS.en;
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [nameInput, setNameInput] = useState("");
  const [ageInput, setAgeInput] = useState("");
  const [saving, setSaving] = useState(false);

  if (!summary) return null;

  const startEdit = () => {
    setNameInput(summary.patient_information.patient_name === "Not provided" ? "" : String(summary.patient_information.patient_name || ""));
    setAgeInput(summary.patient_information.age === "Not provided" ? "" : String(summary.patient_information.age ?? ""));
    setEditing(true);
    setOpen(true);
  };

  const saveEdit = async () => {
    setSaving(true);
    try {
      const body: Record<string, unknown> = { lang };
      if (nameInput.trim()) body.patient_name = nameInput.trim();
      const ageNum = Number(ageInput);
      if (ageInput.trim() && !Number.isNaN(ageNum) && ageNum >= 0 && ageNum <= 125) body.age = ageNum;
      const res = await api<{ compact_summary: CompactPatientSummary }>(
        `/clinical/sessions/${summary.session_id}/compact-summary`,
        { method: "PATCH", body: JSON.stringify(body) },
      );
      onUpdate?.(res.compact_summary);
      setEditing(false);
    } catch { /* ignore */ } finally {
      setSaving(false);
    }
  };

  const downloadSummary = () => {
    const lines = [
      "SUSHRUTAI - PATIENT CONSULTATION SUMMARY",
      `Generated: ${summary.generated_at ?? new Date().toISOString()}`,
      "",
      "PATIENT INFORMATION",
      `- Patient Name: ${summary.patient_information.patient_name}`,
      `- Age: ${summary.patient_information.age}`,
      `- Preferred Language: ${summary.patient_information.preferred_language}`,
      "",
      "CHIEF COMPLAINT",
      `- Main problem: ${summary.chief_complaint.main_problem}`,
      "",
      "SYMPTOM DETAILS",
      `- Duration: ${summary.symptom_details.duration}`,
      `- Location: ${summary.symptom_details.location}`,
      `- Severity: ${summary.symptom_details.severity}`,
      `- Frequency: ${summary.symptom_details.frequency}`,
      `- Associated symptoms: ${summary.symptom_details.associated_symptoms}`,
      "",
      "MEDICAL HISTORY",
      `- Previous conditions: ${summary.medical_history.previous_conditions}`,
      `- Current medications: ${summary.medical_history.current_medications}`,
      `- Known allergies: ${summary.medical_history.known_allergies}`,
      `- Relevant family history: ${summary.medical_history.relevant_family_history}`,
      "",
      "AI ASSESSMENT",
      `- Possible explanations: ${summary.ai_assessment.possible_explanations}`,
      `- Important warning signs: ${summary.ai_assessment.important_warning_signs}`,
      `- Limitations: ${summary.ai_assessment.assessment_limitations}`,
      "",
      "RECOMMENDATIONS",
      `- Recommended doctor: ${summary.recommendations.recommended_doctor}`,
      `- Reason for referral: ${summary.recommendations.reason_for_referral}`,
      `- Possible investigations: ${summary.recommendations.possible_investigations}`,
      `- General food guidance: ${summary.recommendations.general_food_guidance}`,
      `- Lifestyle guidance: ${summary.recommendations.lifestyle_guidance}`,
      "",
      "FOLLOW-UP",
      `- Questions requiring clarification: ${summary.follow_up.questions_requiring_clarification}`,
      `- Recommended next steps: ${summary.follow_up.recommended_next_steps}`,
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sushrutai-summary-${summary.session_id.slice(0, 8)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const removeSummary = async () => {
    try {
      await api(`/clinical/sessions/${summary.session_id}/compact-summary`, { method: "DELETE" });
      onDelete?.();
    } catch { /* ignore */ }
  };

  return (
    <section className="rounded-[16px] border border-border bg-surface p-3.5 shadow-[var(--e1)]" aria-label={L.title}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" onClick={() => setOpen((o) => !o)} className="flex items-center gap-2 text-start font-bold">
          <FileText aria-hidden className="h-4 w-4 text-brand" />
          <span>{L.title}</span>
          <span className="text-[0.8125rem] font-normal text-muted">
            · {summary.patient_information.patient_name} ({summary.patient_information.age})
          </span>
        </button>
        <div className="flex flex-wrap items-center gap-1.5">
          <button type="button" onClick={startEdit} className="inline-flex min-h-9 items-center gap-1 rounded-full bg-surface-2 px-2.5 text-[0.8125rem] font-semibold">
            <Pencil aria-hidden className="h-3.5 w-3.5" />{L.edit}
          </button>
          <button type="button" onClick={downloadSummary} className="inline-flex min-h-9 items-center gap-1 rounded-full bg-surface-2 px-2.5 text-[0.8125rem] font-semibold">
            <Download aria-hidden className="h-3.5 w-3.5" />{L.download}
          </button>
          <button type="button" onClick={removeSummary} className="inline-flex min-h-9 items-center gap-1 rounded-full bg-danger-soft px-2.5 text-[0.8125rem] font-semibold text-danger">
            <Trash2 aria-hidden className="h-3.5 w-3.5" />{L.del}
          </button>
        </div>
      </div>

      {editing && (
        <div className="mt-3 flex flex-wrap items-end gap-2 rounded-[12px] border border-border bg-surface-2 p-2.5">
          <label className="text-[0.8125rem] font-semibold">
            {L.name}
            <input className="input mt-1 min-h-10" value={nameInput} onChange={(e) => setNameInput(e.target.value)} placeholder="e.g. Rahim" />
          </label>
          <label className="w-24 text-[0.8125rem] font-semibold">
            {L.age}
            <input className="input mt-1 min-h-10" inputMode="numeric" value={ageInput} onChange={(e) => setAgeInput(e.target.value.replace(/\D/g, ""))} placeholder="e.g. 42" />
          </label>
          <button type="button" disabled={saving} onClick={saveEdit} className="inline-flex min-h-10 items-center gap-1 rounded-[10px] bg-brand px-3 font-semibold text-brand-ink">
            <Check aria-hidden className="h-4 w-4" />{L.save}
          </button>
          <button type="button" onClick={() => setEditing(false)} className="inline-flex min-h-10 items-center gap-1 rounded-[10px] bg-surface px-2.5 text-muted">
            <X aria-hidden className="h-4 w-4" />
          </button>
        </div>
      )}

      {open && (
        <div className="mt-3 grid gap-2.5 text-[0.875rem] sm:grid-cols-2">
          <div className="rounded-[12px] bg-surface-2 p-2.5">
            <p className="font-bold text-muted">{L.patientInfo}</p>
            <p>{L.name}: {summary.patient_information.patient_name}</p>
            <p>{L.age}: {summary.patient_information.age}</p>
            <p>{L.lang}: {summary.patient_information.preferred_language}</p>
          </div>
          <div className="rounded-[12px] bg-surface-2 p-2.5">
            <p className="font-bold text-muted">{L.chief}</p>
            <p>{summary.chief_complaint.main_problem}</p>
          </div>
          <div className="rounded-[12px] bg-surface-2 p-2.5">
            <p className="font-bold text-muted">{L.details}</p>
            <p>Duration: {summary.symptom_details.duration} · Location: {summary.symptom_details.location}</p>
            <p>Severity: {summary.symptom_details.severity} · Associated: {summary.symptom_details.associated_symptoms}</p>
          </div>
          <div className="rounded-[12px] bg-surface-2 p-2.5">
            <p className="font-bold text-muted">{L.history}</p>
            <p>Conditions: {summary.medical_history.previous_conditions}</p>
            <p>Meds: {summary.medical_history.current_medications} · Allergies: {summary.medical_history.known_allergies}</p>
          </div>
          <div className="rounded-[12px] bg-surface-2 p-2.5">
            <p className="font-bold text-muted">{L.assessment}</p>
            <p>{summary.ai_assessment.possible_explanations}</p>
            <p className="mt-1 text-[0.8125rem] text-muted">{summary.ai_assessment.assessment_limitations}</p>
          </div>
          <div className="rounded-[12px] bg-surface-2 p-2.5">
            <p className="font-bold text-muted">{L.recs}</p>
            <p>Doctor: {summary.recommendations.recommended_doctor} ({summary.recommendations.reason_for_referral})</p>
            <p>Tests: {summary.recommendations.possible_investigations}</p>
            <p>Food: {summary.recommendations.general_food_guidance}</p>
          </div>
        </div>
      )}
    </section>
  );
}
