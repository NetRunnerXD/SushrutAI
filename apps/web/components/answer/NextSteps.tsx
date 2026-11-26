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

/** Next steps: which doctor and how soon, suggested tests (add to the doctor list, compare with my own results),
 *  a free teleconsultation, and preparing the visit summary. */
export function NextSteps({ specialist, tests, urgent, defaultOpen }: {
  specialist?: string; tests?: string[]; urgent: boolean; defaultOpen?: boolean;
}) {
  const { t } = useI18n();
  const [added, setAdded] = useState(false);
  const [labs, setLabs] = useState<Lab[] | null | undefined>(undefined);
  if (!specialist && !tests?.length) return null;
  const addTests = () => Promise.all((tests ?? []).slice(0, 5).map((x) =>
    api("/me/visit-questions", { method: "POST", body: JSON.stringify({ text: fill(t.shouldIGetTest, { test: x }).slice(0, 300), source: "chat" }) })))
    .then(() => setAdded(true)).catch(() => {});
  const showLabs = () => api<Lab[]>("/reports/labs").then((l) => setLabs(matchLabs(tests ?? [], l))).catch(() => setLabs(null));
  return (
    <AnswerSection id="next" icon={Stethoscope} title={t.nextSteps} defaultOpen={defaultOpen ?? urgent}
      summary={specialist || undefined}>
      {!!tests?.length && (
        <div>
          <p className="mb-1 flex items-center gap-1.5 font-semibold"><FlaskConical aria-hidden className="h-4 w-4" />{t.tests}</p>
          <ul className="list-disc space-y-0.5 ps-6">{tests.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <SectionAction icon={Video} href="https://esanjeevani.mohfw.gov.in">{t.findDoctor}</SectionAction>
        {!!tests?.length && <SectionAction icon={ClipboardList} done={added} onClick={addTests}>{added ? t.testsAdded : t.addTestsToList}</SectionAction>}
        {!!tests?.length && <SectionAction icon={FileText} onClick={showLabs} disabled={labs !== undefined}>{t.showMyResults}</SectionAction>}
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
