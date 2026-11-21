"use client";

import { Printer, Share2, Stethoscope } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Banner } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

const SLOT = (f: any) => (f && f.per_day != null ? `${f.morning ?? 0}-${f.afternoon ?? 0}-${f.night ?? 0}` : f?.label ?? "");
const day = (d: string) => new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function Section({ title, children, empty }: { title: string; children?: ReactNode; empty?: string | false }) {
  return (
    <section className="print-avoid-break border-t border-border pt-3">
      <h2 className="mb-1.5 text-[1.0625rem] font-bold">{title}</h2>
      {empty ? <p className="text-muted">{empty}</p> : children}
    </section>
  );
}

/** Plain-text version for WhatsApp / SMS sharing. */
function asText(s: any, t: any): string {
  const p = s.patient;
  const L: string[] = [`${t.visitTitle}${p.name ? ` — ${p.name}` : ""}`,
    [p.age != null && `${p.age} y`, p.sex, p.blood_group].filter(Boolean).join(", ")];
  const list = (title: string, xs: string[]) => xs.length && L.push("", `${title}:`, ...xs.map((x) => `• ${x}`));
  list(t.visitAllergies, s.allergies);
  list(t.visitConditions, s.conditions);
  list(t.visitMeds, s.medicines.map((m: any) => [m.name, m.dose, SLOT(m.frequency), m.timing?.replace(/_/g, " ")].filter(Boolean).join(" ")));
  list(t.visitLabs, s.abnormal_labs.map((l: any) => `${l.test} ${l.value} ${l.unit ?? ""} (${l.flag === "H" ? "high" : "low"})`));
  list(t.visitQuestions, s.questions_to_ask);
  L.push("", s.note);
  return L.join("\n");
}

export default function VisitSummary() {
  const { t } = useI18n();
  const [days, setDays] = useState(90);
  const [s, setS] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setS(null);
    api(`/me/visit-summary?days=${days}`).then(setS).catch((e) => setErr(e.message));
  }, [days]);

  const p = s?.patient;
  const none = t.visitNoneRecorded;
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="space-y-3 print:hidden">
        <h1 className="flex items-center gap-2"><Stethoscope aria-hidden className="h-7 w-7 text-brand" />{t.visitTitle}</h1>
        <p className="text-muted">{t.visitHelp}</p>
        <div role="group" aria-label={t.period} className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{t.period}:</span>
          {([[30, t.days30], [90, t.days90], [365, t.days365]] as const).map(([d, l]) => (
            <Chip key={d} selected={days === d} onClick={() => setDays(d)}>{l}</Chip>))}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button icon={Printer} disabled={!s} onClick={() => window.print()}>{t.visitPrint}</Button>
          {typeof navigator !== "undefined" && "share" in navigator && (
            <Button variant="secondary" icon={Share2} disabled={!s}
              onClick={() => navigator.share({ title: t.visitTitle, text: asText(s, t) }).catch(() => {})}>{t.visitShare}</Button>)}
        </div>
      </div>
      {err && <Banner tone="warning">{err}</Banner>}

      {!s ? <Skeleton className="h-96" /> : (
        <article aria-label={t.visitTitle} className="space-y-3 rounded-[6px] border border-border bg-surface p-5 print:border-0 print:p-0">
          <header className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <p className="text-[0.875rem] font-semibold text-muted">{t.visitTitle}</p>
              <p className="text-[1.375rem] font-bold">{p.name ?? t.visitPatient}</p>
              <p>{[p.age != null && fill(t.yearsOld, { n: p.age }), p.sex && ((t as any)[p.sex] ?? p.sex), p.blood_group && `Blood group ${p.blood_group}`,
                   p.height_cm && `${p.height_cm} cm`, p.weight_kg && `${p.weight_kg} kg`, p.bmi && `BMI ${p.bmi} (${p.bmi_category})`]
                   .filter(Boolean).join(" · ")}</p>
              {(p.pregnancy_status === "pregnant" || p.pregnancy_status === "possibly_pregnant") &&
                <p className="font-bold text-danger">Pregnancy: {p.pregnancy_status.replace("_", " ")}</p>}
            </div>
            <p className="text-[0.875rem] text-muted">{fill(t.visitGenerated, { date: day(s.generated_at) })}</p>
          </header>

          <Section title={t.visitAllergies} empty={!s.allergies.length && none}>
            <p className="font-semibold text-danger">{s.allergies.join(", ")}</p>
          </Section>

          <Section title={t.visitConditions} empty={!s.conditions.length && none}>
            <p>{s.conditions.join(", ")}</p>
            {p.family_history?.length > 0 && <p className="text-muted">Family history: {p.family_history.join(", ")}</p>}
          </Section>

          <Section title={t.visitMeds} empty={!s.medicines.length && none}>
            <div className="overflow-x-auto"><table className="w-full min-w-[18rem] text-left text-[0.9375rem]">
              <tbody className="divide-y divide-border">
                {s.medicines.map((m: any, i: number) => (
                  <tr key={i}><td className="py-1.5 pr-2 font-semibold">{m.name}{m.dose ? ` ${m.dose}` : ""}</td>
                    <td className="py-1.5 pr-2 font-mono">{SLOT(m.frequency)}</td>
                    <td className="py-1.5 text-muted">{m.timing?.replace(/_/g, " ")}{m.since ? ` · since ${day(m.since)}` : ""}</td></tr>))}
              </tbody>
            </table></div>
          </Section>

          <Section title={t.visitLabs} empty={!s.abnormal_labs.length && none}>
            <ul className="space-y-1">{s.abnormal_labs.map((l: any, i: number) => (
              <li key={i}><span className="font-semibold">{l.test}</span>{" "}
                <span className="font-mono font-semibold">{l.value} {l.unit}</span>{" "}
                <span className={l.flag === "H" ? "text-danger" : "text-warn"}>{l.flag === "H" ? "↑ High" : "↓ Low"}</span>
                <span className="text-muted">{l.range ? ` · normal ${l.range}` : ""} · {day(l.at)}</span></li>))}</ul>
          </Section>

          <Section title={t.visitVitals} empty={!s.vitals.length && none}>
            <p className="font-mono">{s.vitals.map((v: any) => `${v.label} ${v.value} ${v.unit}`).join(" · ")}</p>
            {s.bp_readings.length > 1 && (
              <p className="mt-1"><span className="font-semibold">{t.visitBp}: </span>
                <span className="font-mono">{s.bp_readings.map((b: any) => `${b.sys}/${b.dia}`).join(", ")}</span></p>)}
          </Section>

          {s.checkins && s.checkins.good + s.checkins.okay + s.checkins.unwell > 0 && (
            <Section title={t.checkinsLabel}>
              <p>{t.moodGood}: {s.checkins.good} · {t.moodOkay}: {s.checkins.okay} · <span className="font-semibold">{t.moodUnwell}: {s.checkins.unwell}</span></p>
              {s.checkins.unwell_days.length > 0 && <p className="text-[0.9375rem] text-muted">{t.unwellDays}: {s.checkins.unwell_days.map(day).join(", ")}</p>}
            </Section>)}

          {s.symptom_checks.length > 0 && (
            <Section title={t.visitChecks}>
              <ul className="space-y-2">{s.symptom_checks.map((c: any, i: number) => (
                <li key={i}><span className="text-muted">{day(c.at)} — </span><span className="font-semibold">{c.complaint}</span>
                  {c.yes_answers.length > 0 && <span className="block text-[0.9375rem]">Said yes to: {c.yes_answers.join("; ")}</span>}
                  {c.possible.length > 0 && <span className="block text-[0.9375rem] text-muted">App suggested discussing: {c.possible.join(", ")}</span>}
                </li>))}</ul>
            </Section>)}

          {s.questions_asked.length > 0 && (
            <Section title={t.visitAsked}>
              <ul className="list-disc space-y-0.5 pl-5 text-[0.9375rem]">{s.questions_asked.map((q: any, i: number) => <li key={i}>{q.text}</li>)}</ul>
            </Section>)}

          <Section title={t.visitQuestions}>
            <ol className="list-decimal space-y-1 pl-5">{s.questions_to_ask.map((q: string, i: number) => <li key={i}>{q}</li>)}</ol>
          </Section>

          <p className="border-t border-border pt-3 text-[0.8125rem] text-muted">{s.note}</p>
        </article>
      )}
    </div>
  );
}
