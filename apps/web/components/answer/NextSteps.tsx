"use client";

import { ClipboardList, FileText, FlaskConical, Stethoscope, Video } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { AnswerSection, SectionAction } from "./Section";

type Lab = { id: string; test_name: string; value: number | null; unit?: string; flag?: string; observed_at: string };

/** Words that name the same test in reports and in answers ("CBC" ~ "complete blood count"). */
const ALIASES: Record<string, string[]> = {
  cbc: ["complete blood count", "haemoglobin", "hemoglobin", "platelet", "wbc", "rbc"],
  hba1c: ["glycated", "a1c"], lft: ["liver function", "sgpt", "sgot", "alt", "ast", "bilirubin"],
  kft: ["kidney function", "creatinine", "urea"], rft: ["renal function", "creatinine", "urea"],
  tsh: ["thyroid", "t3", "t4"], lipid: ["cholesterol", "triglyceride", "hdl", "ldl"],
  sugar: ["glucose", "fasting", "ppbs", "rbs"], urine: ["urine routine", "urinalysis"], dengue: ["ns1", "igm"],
};

function words(s: string): string[] {
  const base = s.toLowerCase().match(/[a-z0-9]{2,}/g) ?? [];
  return [...base, ...base.flatMap((w) => ALIASES[w] ?? [])];
}

export function matchLabs(tests: string[], labs: Lab[]): Lab[] {
  const want = tests.flatMap(words).filter((w) => w.length >= 3 && !["test", "blood", "level", "levels", "check"].includes(w));
  const latest = new Map<string, Lab>();
  for (const l of labs) {
    const name = l.test_name.toLowerCase();
    if (want.some((w) => name.includes(w))) latest.set(name, l);  // labs come oldest first: keep the newest
  }
  return [...latest.values()].slice(0, 6);
}

export type SpecialistRouting = {
  qualification_tier: "mbbs" | "md_ms_dnb" | "dm_mch" | string;
  tier_label?: string;
  recommended_title: string;
  primary_specialty: string;
  secondary_specialties?: string[];
  why_appropriate?: string;
  escalation_triggers?: string[];
  credential_notice?: string | null;
};

export type ClinicalInvestigationItem = {
  investigation_name: string;
  investigation_group?: string;
  category?: string;
  why_relevant?: string;
  what_it_evaluates?: string;
  priority_context?: "urgent_evaluation" | "initial_evaluation" | "considered_later" | string;
  urgency?: string;
  clinician_decision_required?: boolean;
  safety_considerations?: string | null;
  limitations?: string;
  already_in_records?: boolean;
  existing_record_summary?: string | null;
};

/** Next steps: which doctor and how soon, suggested tests (add to the doctor list, compare with my own results),
 *  a free teleconsultation, and preparing the visit summary. */
export function NextSteps({ specialist, tests, urgent, defaultOpen, routing, investigations }: {
  specialist?: string; tests?: string[]; urgent: boolean; defaultOpen?: boolean;
  routing?: SpecialistRouting | null;
  investigations?: ClinicalInvestigationItem[] | null;
}) {
  const { t, lang } = useI18n();
  const [added, setAdded] = useState(false);
  const [labs, setLabs] = useState<Lab[] | null | undefined>(undefined);
  const invList = investigations?.length ? investigations : null;
  const testNames = invList ? invList.map((x) => x.investigation_name) : (tests ?? []);
  const headerSummary = routing?.recommended_title || specialist || undefined;
  if (!headerSummary && !testNames.length) return null;
  const addTests = () => Promise.all(testNames.slice(0, 5).map((x) =>
    api("/me/visit-questions", { method: "POST", body: JSON.stringify({ text: fill(t.shouldIGetTest, { test: x }).slice(0, 300), source: "chat" }) })))
    .then(() => setAdded(true)).catch(() => {});
  const showLabs = () => api<Lab[]>("/reports/labs").then((l) => setLabs(matchLabs(testNames, l))).catch(() => setLabs(null));

  const tierTone = routing?.qualification_tier === "dm_mch"
    ? "border-warn/40 bg-warn-soft/50 text-warn"
    : routing?.qualification_tier === "md_ms_dnb"
      ? "border-info/35 bg-info-soft/50 text-info"
      : "border-brand/30 bg-brand-soft/45 text-brand";

  const priorityLabel = (p?: string) => {
    if (p === "urgent_evaluation") return lang === "hi" ? "तुरंत जांच" : lang === "bn" ? "জরুরি পরীক্ষা" : "Urgent";
    if (p === "considered_later") return lang === "hi" ? "बाद में विचार" : lang === "bn" ? "পরে বিবেচ্য" : "Consider later";
    return lang === "hi" ? "प्रारंभिक जांच" : lang === "bn" ? "প্রাথমিক পরীক্ষা" : "Initial evaluation";
  };

  return (
    <AnswerSection id="next" icon={Stethoscope} title={t.nextSteps} defaultOpen={defaultOpen ?? urgent}
      summary={headerSummary}>
      {routing && (
        <div className="space-y-1.5 rounded-[12px] border border-border/80 bg-surface-2/60 p-3 text-[0.9375rem]">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[0.75rem] font-bold ${tierTone}`}>
              {routing.tier_label || routing.qualification_tier.toUpperCase()}
            </span>
            <span className="font-bold text-text">{routing.recommended_title}</span>
          </div>
          <div className="flex flex-wrap gap-1.5 text-[0.8125rem] text-muted">
            <span>{lang === "hi" ? "मुख्य विभाग:" : lang === "bn" ? "প্রধান বিভাগ:" : "Primary:"} <strong className="text-text">{routing.primary_specialty}</strong></span>
            {!!routing.secondary_specialties?.length && (
              <span>· {lang === "hi" ? "सहयोगी विभाग:" : lang === "bn" ? "সহযোগী বিভাগ:" : "Multidisciplinary:"} <strong className="text-text">{routing.secondary_specialties.join(", ")}</strong></span>
            )}
          </div>
          {routing.why_appropriate && <p className="text-[0.875rem] leading-snug text-text/90">{routing.why_appropriate}</p>}
          {routing.credential_notice && <p className="text-[0.8125rem] text-muted">{routing.credential_notice}</p>}
        </div>
      )}

      {invList ? (
        <div className="space-y-2">
          <p className="flex items-center gap-1.5 font-semibold"><FlaskConical aria-hidden className="h-4 w-4" />{t.tests}</p>
          <ul className="space-y-2">
            {invList.map((inv, i) => (
              <li key={i} className="rounded-[12px] border border-border/70 bg-surface-2/40 p-2.5 text-[0.9375rem]">
                <div className="flex flex-wrap items-center justify-between gap-1.5">
                  <span className="font-semibold text-text">{inv.investigation_name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[0.72rem] font-semibold ${
                    inv.priority_context === "urgent_evaluation" ? "bg-danger-soft text-danger"
                    : inv.priority_context === "considered_later" ? "bg-surface-2 text-muted"
                    : "bg-brand-soft/60 text-brand"
                  }`}>{priorityLabel(inv.priority_context)}</span>
                </div>
                {inv.why_relevant && <p className="mt-1 text-[0.875rem] text-text/90">{inv.why_relevant}</p>}
                {inv.what_it_evaluates && (
                  <p className="mt-0.5 text-[0.8125rem] text-muted">
                    <span className="font-semibold">{lang === "hi" ? "क्या जांचता है:" : lang === "bn" ? "কী পরীক্ষা করে:" : "Evaluates:"}</span> {inv.what_it_evaluates}
                  </p>
                )}
                {inv.safety_considerations && (
                  <p className="mt-0.5 text-[0.8125rem] font-medium text-warn">{inv.safety_considerations}</p>
                )}
                {inv.already_in_records && inv.existing_record_summary && (
                  <p className="mt-0.5 text-[0.8125rem] text-info">In your records: {inv.existing_record_summary}</p>
                )}
              </li>
            ))}
          </ul>
          <p className="text-[0.8125rem] text-muted">
            {lang === "hi"
              ? "जांच की आवश्यकता का अंतिम निर्णय आपके डॉक्टर शारीरिक परीक्षण के बाद करेंगे।"
              : lang === "bn"
                ? "পরীক্ষার প্রয়োজন আছে কি না তা আপনার চিকিৎসক শারীরিক পরীক্ষার পর সিদ্ধান্ত নেবেন।"
                : "Your treating doctor will decide which tests are actually necessary after clinical examination."}
          </p>
        </div>
      ) : !!tests?.length && (
        <div>
          <p className="mb-1 flex items-center gap-1.5 font-semibold"><FlaskConical aria-hidden className="h-4 w-4" />{t.tests}</p>
          <ul className="list-disc space-y-0.5 ps-6">{tests.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <SectionAction icon={Video} href="https://esanjeevani.mohfw.gov.in">{t.findDoctor}</SectionAction>
        {!!testNames.length && <SectionAction icon={ClipboardList} done={added} onClick={addTests}>{added ? t.testsAdded : t.addTestsToList}</SectionAction>}
        {!!testNames.length && <SectionAction icon={FileText} onClick={showLabs} disabled={labs !== undefined}>{t.showMyResults}</SectionAction>}
        <Link href="/me/visit" className="inline-flex min-h-12 items-center gap-1.5 rounded-full border-[1.5px] border-border-input bg-surface px-3 text-[0.875rem] font-semibold hover:bg-surface-2">
          <Stethoscope aria-hidden className="h-4 w-4" />{t.prepareVisit}</Link>
      </div>
      {labs !== undefined && (
        labs?.length ? (
          <ul className="space-y-1 rounded-[12px] border border-border bg-surface p-2.5 text-[0.9375rem]">
            {labs.map((l) => (
              <li key={l.id} className="flex flex-wrap justify-between gap-2">
                <span>{l.test_name}</span>
                <span className={l.flag === "H" || l.flag === "L" ? "font-semibold text-danger" : ""}>
                  {l.value ?? "—"} {l.unit ?? ""}{l.flag === "H" ? ` (${t.flagHigh})` : l.flag === "L" ? ` (${t.flagLow})` : ""}
                  <span className="ms-2 text-muted">{new Date(l.observed_at).toLocaleDateString()}</span></span>
              </li>
            ))}
          </ul>
        ) : <p className="text-[0.875rem] text-muted">{t.noMatchingResults}</p>
      )}
    </AnswerSection>
  );
}
