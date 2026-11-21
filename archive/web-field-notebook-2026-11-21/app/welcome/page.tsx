"use client";

import { FileText, IndianRupee, Mic, ShieldCheck, Stethoscope, Users, User, Compass, Volume2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { LanguageGrid } from "@/components/LanguagePicker";
import { Button } from "@/components/ui/Button";
import { Steps } from "@/components/ui/Medical";
import { Scribble } from "@/components/ui/Card";
import { LogoMark } from "@/components/Shell";
import { api, ensureSession } from "@/lib/api";
import { bcp47, useI18n } from "@/lib/i18n";
import { speak } from "@/lib/voice";

type Notice = { disclaimer: string; notice_version: string; purposes: { purpose: string; required: boolean; text: string }[] };

export default function Welcome() {
  const { t, lang } = useI18n();
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [chosen, setChosen] = useState<Record<string, boolean>>({ core_service: true, history_storage: true });
  const [busy, setBusy] = useState(false);

  useEffect(() => { ensureSession().catch(() => {}); }, []);
  useEffect(() => {
    if (step === 1) api<Notice>(`/privacy/notice?lang=${lang === "hi" ? "hi" : "en"}`).then(setNotice).catch(() => {});
  }, [step, lang]);

  async function acceptConsent() {
    setBusy(true);
    try {
      await api("/consents", { method: "POST", body: JSON.stringify({ purposes: Object.keys(chosen).filter((k) => chosen[k]) }) });
      setStep(2);
    } finally { setBusy(false); }
  }

  const steps = [t.chooseLanguage, t.onboardTitle, t.whoFor];
  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col gap-6 px-4 py-6">
      <div className="flex items-center gap-3">
        <LogoMark className="h-11 w-11" />
        <span className="serif text-[1.375rem] font-extrabold">SushrutAI</span>
      </div>
      <Steps steps={steps} current={step} />

      {step === 0 && (
        <section className="space-y-5" aria-labelledby="w-title">
          <div>
            <h1 id="w-title" className="text-[2.125rem] leading-[1.15] sm:text-[2.75rem]">{t.welcomeHero}</h1>
            <Scribble className="mt-1" />
            <ul className="note relative mt-6 grid gap-1 rounded-[6px] border border-border bg-surface p-4 pt-5 shadow-[var(--e1)] sm:grid-cols-3 sm:gap-3">
              <span aria-hidden className="absolute -top-2.5 left-1/2 h-5 w-20 -translate-x-1/2 -rotate-2 rounded-[2px] bg-accent/70" />
              {[[Mic, t.benefitVoice], [FileText, t.benefitRx], [IndianRupee, t.benefitCheaper]].map(([Icon, label]: any) => (
                <li key={label} className="flex items-center gap-3 py-1.5 font-semibold">
                  <span aria-hidden className="grid h-10 w-10 shrink-0 place-items-center rounded-[46%_54%_50%_50%/52%_48%_52%_48%] bg-brand-soft text-brand">
                    <Icon className="h-5 w-5" /></span>{label}
                </li>
              ))}
            </ul>
          </div>
          <h2>{t.chooseLanguage}</h2>
          <LanguageGrid />
          <Button size="lg" className="w-full" onClick={() => setStep(1)}>{t.start}</Button>
          <ul className="space-y-1.5 text-[0.9375rem] text-muted">
            <li className="flex gap-2"><ShieldCheck aria-hidden className="h-5 w-5 shrink-0 text-ok" />{t.trustLocal}</li>
            <li className="flex gap-2"><ShieldCheck aria-hidden className="h-5 w-5 shrink-0 text-ok" />{t.trustSources}</li>
            <li className="flex gap-2"><Stethoscope aria-hidden className="h-5 w-5 shrink-0 text-warn" />{t.trustDoctor}</li>
          </ul>
          <p className="font-mono text-[0.875rem] text-muted">108 · 112 · Tele-MANAS 14416</p>
        </section>
      )}

      {step === 1 && (
        <section className="space-y-4" aria-labelledby="c-title">
          <div><h1 id="c-title">{t.onboardTitle}</h1><Scribble /></div>
          <div className="rounded-[3px_8px_8px_3px] border-l-[5px] border-warn bg-warn-soft p-4 text-warn">
            <p className="flex gap-2 font-semibold"><Stethoscope aria-hidden className="mt-0.5 h-5 w-5 shrink-0" />{notice?.disclaimer ?? t.disclaimerLong}</p>
            <button onClick={() => speak(notice?.disclaimer ?? t.disclaimerLong, lang, bcp47(lang))}
                    className="mt-2 inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface px-3 text-[0.9375rem] font-semibold text-text">
              <Volume2 aria-hidden className="h-4 w-4" />{t.readAloud}</button>
          </div>
          <h2>{t.consentTitle}</h2>
          <ul className="space-y-2">
            {(notice?.purposes ?? []).map((p) => (
              <li key={p.purpose} className="rounded-[6px] border border-border bg-surface p-3">
                <label className="flex cursor-pointer gap-3">
                  <input type="checkbox" className="mt-1 h-6 w-6 shrink-0 accent-[var(--brand)]"
                         checked={p.required || !!chosen[p.purpose]} disabled={p.required}
                         onChange={(e) => setChosen((c) => ({ ...c, [p.purpose]: e.target.checked }))} />
                  <span>
                    <span className={`mb-1 inline-block rounded-full px-2.5 text-[0.8125rem] font-semibold
                      ${p.required ? "bg-brand-soft text-brand" : "bg-surface-2 text-muted"}`}>
                      {p.required ? t.consentRequired : t.consentOptional}</span>
                    <span className="block">{p.text}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <p className="text-[0.875rem] text-muted">Notice {notice?.notice_version}. You can change these any time in Me → Settings.</p>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setStep(0)}>{t.back}</Button>
            <Button size="lg" className="flex-1" loading={busy} disabled={!notice} onClick={acceptConsent}>{t.onboardAccept}</Button>
          </div>
        </section>
      )}

      {step === 2 && (
        <section className="space-y-4" aria-labelledby="f-title">
          <div><h1 id="f-title">{t.whoFor}</h1><Scribble /></div>
          <div className="grid gap-3">
            {[[User, t.forMe, "/me"], [Users, t.forFamily, "/me"], [Compass, t.forExplore, "/"]].map(([Icon, label, href]: any) => (
              <button key={label} onClick={() => router.replace(href === "/me" ? "/?setup=1" : "/")}
                className="note flex min-h-16 items-center gap-4 rounded-[6px] border border-border bg-surface px-4 py-2 text-left text-[1.125rem] font-semibold shadow-[var(--e1)] hover:border-brand">
                <span aria-hidden className="grid h-12 w-12 place-items-center rounded-full bg-brand-soft text-brand"><Icon className="h-6 w-6" /></span>
                {label}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
