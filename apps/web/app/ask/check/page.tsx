"use client";

import { Check, HelpCircle, RotateCcw, Share2, Stethoscope, Volume2, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { RedFlagBanner } from "@/components/AnswerCard";
import { useSOS } from "@/components/Emergency";
import { Button } from "@/components/ui/Button";
import { Card, PageTitle } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Banner, DisclaimerChip, UrgencyBanner, UrgencyPill, type Urgency } from "@/components/ui/Trust";
import { VoiceOrb } from "@/components/Voice";
import { api } from "@/lib/api";
import { bcp47, fill, useI18n } from "@/lib/i18n";
import { SYMPTOM_CHIPS } from "@/lib/symptoms";
import { speak } from "@/lib/voice";
import { AskAbout } from "@/components/AskAbout";

function CheckInner() {
  const { t, lang } = useI18n();
  const params = useSearchParams();
  const { openSOS } = useSOS();
  const [complaint, setComplaint] = useState("");
  const [s, setS] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const started = useRef(false);

  async function start(c: string) {
    if (!c.trim()) return;
    setBusy(true); setErr(null);
    try {
      const r = await api<any>("/triage/sessions", { method: "POST", body: JSON.stringify({ complaint: c, lang }) });
      setS(r);
      if (r.red_flag?.kind === "emergency" || r.red_flag?.kind === "mental_health") openSOS();
      if (r.question) speak(r.question.text, lang, bcp47(lang));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  useEffect(() => {
    const c = params.get("c");
    if (c && !started.current) { started.current = true; setComplaint(c); start(c); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  async function answer(a: "yes" | "no" | "unsure") {
    setBusy(true);
    try {
      const r = await api<any>(`/triage/sessions/${s.id}/answer`, { method: "POST", body: JSON.stringify({ finding_id: s.question.finding_id, answer: a }) });
      setS(r);
      if (r.question) speak(r.question.text, lang, bcp47(lang));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  const reset = () => { setS(null); setComplaint(""); started.current = true; };
  const done = s && s.status === "complete";

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <PageTitle sub={t.disclaimerShort}>{t.triageTitle}</PageTitle>

      {!s && (
        <Card className="space-y-4">
          <h2>{t.triageStart}</h2>
          <div className="flex flex-wrap gap-2">
            {SYMPTOM_CHIPS.map((c) => (
              <Chip key={c.key} icon={c.icon} selected={complaint === c.complaint} onClick={() => setComplaint(c.complaint)}>{t.symptoms[c.key]}</Chip>
            ))}
          </div>
          <label htmlFor="cmp" className="sr-only">{t.triageStart}</label>
          <textarea id="cmp" className="input" rows={3} value={complaint} onChange={(e) => setComplaint(e.target.value)} placeholder={t.askPlaceholder} />
          <div className="flex flex-wrap items-center gap-4">
            <Button size="lg" loading={busy} disabled={complaint.trim().length < 3} onClick={() => start(complaint)}>{t.triageBegin}</Button>
            <VoiceOrb size="md" onText={(txt) => { setComplaint(txt); start(txt); }} />
          </div>
          {busy && <p role="status" className="text-muted">{t.thinking} (≈ 30–60 s)</p>}
        </Card>
      )}

      {err && <Banner tone="warning">{err}</Banner>}
      {s?.red_flag && <RedFlagBanner rf={s.red_flag} />}

      {s?.question && !done && (
        <Card className="space-y-5">
          <div className="flex items-center justify-between text-[0.9375rem] text-muted">
            <span>{fill(t.questionOf, { n: s.asked + 1 })}</span>
            <div aria-hidden className="well h-2.5 w-40 overflow-hidden rounded-full">
              <div className="h-full rounded-full bg-text" style={{ width: `${Math.min(100, ((s.asked + 1) / 10) * 100)}%` }} />
            </div>
          </div>
          <p className="display text-[1.625rem] font-bold leading-snug" aria-live="polite">{s.question.text}</p>
          <button onClick={() => speak(s.question.text, lang, bcp47(lang))} className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3.5 font-semibold hover:bg-border">
            <Volume2 aria-hidden className="h-4 w-4" />{t.readAloud}</button>
          <div className="grid gap-3 sm:grid-cols-3">
            <Button size="lg" icon={Check} disabled={busy} onClick={() => answer("yes")}>{t.yes}</Button>
            <Button size="lg" variant="secondary" icon={X} disabled={busy} onClick={() => answer("no")}>{t.no}</Button>
            <Button size="lg" variant="ghost" icon={HelpCircle} disabled={busy} onClick={() => answer("unsure")}>{t.unsure}</Button>
          </div>
        </Card>
      )}

      {s?.possible_conditions && (done || !s.question) && (
        <div className="space-y-4">
          {s.recommendation && <UrgencyBanner urgency={s.recommendation.urgency as Urgency} specialist={s.recommendation.specialist} onEmergency={openSOS} />}
          {s.also_consider_note && <Banner tone="warning" title={t.alsoConsider}>{s.also_consider_note}</Banner>}
          <Card>
            <h2 className="mb-3">{t.possibleConditions}</h2>
            <ol className="space-y-3">
              {s.possible_conditions.map((c: any, i: number) => (
                <li key={c.name}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-semibold">{i + 1}. {c.name}</span><UrgencyPill urgency={c.urgency} />
                  </div>
                  <div className="well mt-1.5 h-3 overflow-hidden rounded-full" role="img" aria-label={`${c.name}: ${t.likelihood[c.likelihood]}`}>
                    <div className="h-full rounded-full bg-text" style={{ width: `${Math.max(4, c.probability * 100)}%` }} />
                  </div>
                  <p className="mt-0.5 text-[0.875rem] text-muted">{t.likelihood[c.likelihood]} · {c.specialist}</p>
                </li>
              ))}
            </ol>
          </Card>
          <div className="flex flex-wrap gap-2">
            <a href="https://esanjeevani.mohfw.gov.in" target="_blank" rel="noreferrer" className="btn btn-primary"><Stethoscope aria-hidden className="h-5 w-5" />{t.findDoctor}</a>
            {typeof navigator !== "undefined" && "share" in navigator && (
              <Button variant="secondary" icon={Share2} onClick={() => navigator.share({ title: t.triageTitle,
                text: `${t.triageTitle}: ${s.possible_conditions.map((c: any) => c.name).join(", ")}\n${t.disclaimerShort}` }).catch(() => {})}>{t.share}</Button>
            )}
            {s.id && <AskAbout refId={`triage:${s.id}`} label={`${t.triageTitle}: ${complaint}`.slice(0, 80)} text={t.askFollowUp} />}
            <Button variant="ghost" icon={RotateCcw} onClick={reset}>{t.restart}</Button>
          </div>
          <DisclaimerChip />
        </div>
      )}
    </div>
  );
}

export default function SymptomCheck() {
  return <Suspense><CheckInner /></Suspense>;
}
