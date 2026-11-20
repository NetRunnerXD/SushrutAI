"use client";

import { Check, HelpCircle, ListChecks, RotateCcw, X } from "lucide-react";
import { useState } from "react";
import { UrgencyBanner, UrgencyPill, type Urgency } from "@/components/ui/Trust";
import { useSOS } from "@/components/Emergency";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { AnswerSection } from "./Section";

/** The step-by-step symptom check (yes / no / not sure questions), run inside the chat instead of on its own page. */
export function CheckInline({ complaint }: { complaint: string }) {
  const { t, lang } = useI18n();
  const { openSOS } = useSOS();
  const [s, setS] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function start() {
    setBusy(true); setErr(null);
    try {
      const r = await api<any>("/triage/sessions", { method: "POST", body: JSON.stringify({ complaint: complaint.slice(0, 500), lang }) });
      setS(r);
      if (r.red_flag?.kind === "emergency" || r.red_flag?.kind === "mental_health") openSOS();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function answer(a: "yes" | "no" | "unsure") {
    setBusy(true);
    try {
      setS(await api<any>(`/triage/sessions/${s.id}/answer`, { method: "POST", body: JSON.stringify({ finding_id: s.question.finding_id, answer: a }) }));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  const done = s && (s.status === "complete" || !s.question);
  const Btn = ({ onClick, icon: Icon, children, primary }: any) => (
    <button type="button" onClick={onClick} disabled={busy}
      className={`inline-flex min-h-12 flex-1 items-center justify-center gap-1.5 rounded-[14px] px-3 font-semibold disabled:opacity-60 ${primary ? "bg-brand text-brand-ink" : "border-[1.5px] border-border-input bg-surface"}`}>
      <Icon aria-hidden className="h-5 w-5" />{children}</button>
  );

  return (
    <AnswerSection id="check" icon={ListChecks} title={t.checkStepByStep} defaultOpen={false}
      summary={s?.question && !done ? fill(t.questionOf, { n: s.asked + 1 }) : undefined}>
      {!s && (
        <div className="space-y-2">
          <p className="text-[0.9375rem] text-muted">{t.checkStepByStepHelp}</p>
          <button type="button" onClick={start} disabled={busy}
            className="btn btn-outline disabled:opacity-60">
            <ListChecks aria-hidden className="h-5 w-5" />{busy ? t.thinking : t.startCheck}</button>
        </div>
      )}
      {err && <p role="alert" className="text-[0.9375rem] font-semibold text-danger">{err}</p>}
      {s?.question && !done && (
        <div className="space-y-3" aria-live="polite">
          <p className="text-[1.125rem] font-semibold leading-snug">{s.question.text}</p>
          <div className="flex flex-wrap gap-2">
            <Btn onClick={() => answer("yes")} icon={Check} primary>{t.yes}</Btn>
            <Btn onClick={() => answer("no")} icon={X}>{t.no}</Btn>
            <Btn onClick={() => answer("unsure")} icon={HelpCircle}>{t.unsure}</Btn>
          </div>
        </div>
      )}
      {done && s.possible_conditions && (
        <div className="space-y-3">
          {s.recommendation && <UrgencyBanner urgency={s.recommendation.urgency as Urgency} specialist={s.recommendation.specialist} onEmergency={openSOS} />}
          <ol className="space-y-2">
            {s.possible_conditions.map((c: any, i: number) => (
              <li key={c.name}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold">{i + 1}. {c.name}</span><UrgencyPill urgency={c.urgency} />
                </div>
                <div className="well mt-1 h-2 overflow-hidden rounded-full" role="img" aria-label={`${c.name}: ${t.likelihood[c.likelihood]}`}>
                  <div className="h-full rounded-full bg-text" style={{ width: `${Math.max(4, c.probability * 100)}%` }} />
                </div>
              </li>
            ))}
          </ol>
          <button type="button" onClick={() => setS(null)} className="inline-flex min-h-12 items-center gap-1.5 text-[0.875rem] font-semibold text-muted">
            <RotateCcw aria-hidden className="h-4 w-4" />{t.restart}</button>
        </div>
      )}
    </AnswerSection>
  );
}
