"use client";

import { FileText, IndianRupee, Mic, ShieldCheck, Stethoscope, Users, User, Compass, Volume2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { LanguageGrid } from "@/components/LanguagePicker";
import { Button } from "@/components/ui/Button";
import { Steps } from "@/components/ui/Medical";
import { LogoMark } from "@/components/Shell";
import { api, ensureSession } from "@/lib/api";
import { bcp47, useI18n } from "@/lib/i18n";
import { speak } from "@/lib/voice";

type Notice = { disclaimer: string; notice_version: string; purposes: { purpose: string; required: boolean; text: string }[] };

export default function Welcome() {
  const { t, lang, setLang } = useI18n();
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
    <div className="flex min-h-dvh flex-col">
      {/* The lid: a gunmetal band with the dabba, and on the first step the promise in three katoris */}
      <header className="bg-rail text-rail-text">
        <div className={`mx-auto max-w-2xl px-4 ${step === 0 ? "pb-8 pt-6 sm:pb-10 sm:pt-10" : "py-4"}`}>
          <div className="flex items-center gap-3">
            <LogoMark className={step === 0 ? "h-14 w-14" : "h-11 w-11"} />
            <span className="display text-[1.5rem] font-extrabold">SushrutAI</span>
          </div>
          {step === 0 && (
            <>
              <h1 id="w-title" className="mt-6 text-[2.375rem] leading-[1.08] sm:text-[3.25rem]">{t.welcomeHero}</h1>
              <ul className="mt-7 grid grid-cols-3 gap-3">
                {([[Mic, t.benefitVoice], [FileText, t.benefitRx], [IndianRupee, t.benefitCheaper]] as const).map(([Icon, label]) => (
                  <li key={label} className="flex flex-col items-center gap-2 text-center text-[0.9375rem] font-semibold leading-snug sm:text-[1.0625rem]">
                    <span aria-hidden className="grid h-16 w-16 place-items-center rounded-full bg-rail-2 shadow-[inset_0_3px_6px_rgb(0_0_0/35%)] sm:h-20 sm:w-20">
                      <Icon className="h-8 w-8 text-rail-text" strokeWidth={2.2} /></span>{label}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-6">
      <Steps steps={steps} current={step} />

      {step === 0 && (
        <section className="space-y-5" aria-labelledby="w-title">
          <h2>{t.chooseLanguage}</h2>
          <LanguageGrid />
          {/* Start confirms the language on screen: English is preselected, so people often press Start without
              tapping it, and an unconfirmed language made the app send them back to the start after step 3. */}
          <Button size="lg" className="w-full" onClick={() => { setLang(lang); setStep(1); }}>{t.start}</Button>
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
          <h1 id="c-title">{t.onboardTitle}</h1>
          <div className="rounded-[18px] border-[1.5px] border-warn/50 bg-warn-soft p-4 text-warn">
            <p className="flex gap-2 font-semibold"><Stethoscope aria-hidden className="mt-0.5 h-5 w-5 shrink-0" />{notice?.disclaimer ?? t.disclaimerLong}</p>
            <button onClick={() => speak(notice?.disclaimer ?? t.disclaimerLong, lang, bcp47(lang))}
                    className="mt-2 inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface px-3 text-[0.9375rem] font-semibold text-text">
              <Volume2 aria-hidden className="h-4 w-4" />{t.readAloud}</button>
          </div>
          <h2>{t.consentTitle}</h2>
          <ul className="space-y-2">
            {(notice?.purposes ?? []).map((p) => (
              <li key={p.purpose} className="rounded-[18px] border border-border bg-surface p-4 shadow-[var(--rim)]">
                <label className="flex cursor-pointer gap-3">
                  <input type="checkbox" className="mt-1 h-6 w-6 shrink-0 "
                         checked={p.required || !!chosen[p.purpose]} disabled={p.required}
                         onChange={(e) => setChosen((c) => ({ ...c, [p.purpose]: e.target.checked }))} />
                  <span>
                    <span className={`mb-1 inline-block rounded-full px-2.5 text-[0.8125rem] font-semibold
                      ${p.required ? "bg-text text-bg" : "bg-surface-2 text-muted"}`}>
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
          <h1 id="f-title">{t.whoFor}</h1>
          <div className="grid gap-3">
            {[[User, t.forMe, "/me"], [Users, t.forFamily, "/me"], [Compass, t.forExplore, "/"]].map(([Icon, label, href]: any) => (
              <button key={label} onClick={() => router.replace(href === "/me" ? "/?setup=1" : "/")}
                className="flex min-h-20 items-center gap-4 rounded-[20px] border border-border bg-surface px-4 py-3 text-start text-[1.1875rem] font-bold shadow-[var(--rim),var(--e1)] hover:border-border-input hover:bg-surface-2">
                <span aria-hidden className="well grid h-14 w-14 shrink-0 place-items-center rounded-full text-text"><Icon className="h-7 w-7" /></span>
                {label}
              </button>
            ))}
          </div>
        </section>
      )}
      </div>
    </div>
  );
}
