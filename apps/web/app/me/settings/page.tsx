"use client";

import { BellRing, CalendarPlus, Cpu, Download, HelpCircle, Languages, Lock, MonitorSmartphone, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { LanguageGrid } from "@/components/LanguagePicker";
import { CHAT_MODEL_KEY } from "@/components/ModelSwitch";
import { VoicePicker } from "@/components/VoicePicker";
import { useVoiceLoop } from "@/components/VoiceLoop";
import { setVoicePrefs, spokenRoute, voicePrefs } from "@/lib/voice";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, PageTitle } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Banner } from "@/components/ui/Trust";
import { api, downloadFile, setTokens } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";
import { useUiMode } from "@/lib/ui-mode";
import { clearOfflineData, notificationsOn, setNotifications, swSupported } from "@/components/DoseNotifier";

type Reminder = { medication_id: string; name: string; slot: string; time: string | null; timing: string | null };

function Toggle({ label, help, checked, onChange, disabled }: { label: string; help?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="flex min-h-14 cursor-pointer items-start justify-between gap-4 py-2">
      <span><span className="block font-semibold">{label}</span>{help && <span className="block text-[0.9375rem] text-muted">{help}</span>}</span>
      <input type="checkbox" role="switch" aria-checked={checked} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)}
        className="mt-1 h-7 w-12 shrink-0 cursor-pointer appearance-none rounded-full bg-border-input shadow-[var(--well)] transition-colors before:block before:h-6 before:w-6
          before:translate-x-0.5 before:rounded-full before:bg-white before:transition-transform checked:bg-brand checked:before:translate-x-[22px]" />
    </label>
  );
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return <div role="radiogroup" aria-label={label}><p className="mb-1.5 font-semibold">{label}</p><div className="flex flex-wrap gap-2">{children}</div></div>;
}


const CONSENT_LABEL: Record<string, string> = {
  history_storage: "Save my chats, prescriptions and reports", cloud_speech: "Use India-hosted voice services when needed",
  cloud_llm_deidentified: "Send de-identified hard questions to a cloud AI", model_improvement: "Help improve prescription reading",
  cloud_llm_full: "Gemini answers: send questions, health context and photos to Google",
  cloud_voice: "Cloud voice (Sarvam / ElevenLabs): send my voice and read-aloud text",
  cloud_llm_live: "Gemini Live: send my microphone audio, and for Talk live the health context needed to answer, to Google",
};

export default function SettingsPage() {
  const { t } = useI18n();
  const { prefs, setPrefs } = usePrefs();
  const desktop = useUiMode() === "desktop";
  const SECTIONS: [string, string][] = [["s-lang", t.langVoice], ["s-display", t.display], ["reminders", t.reminders],
    ["s-ai", "AI Backend"], ["s-privacy", t.privacy], ["s-help", t.help]];
  const [current, setCurrent] = useState("s-lang");
  useEffect(() => {  // the section in view is marked in the desktop section list
    if (!desktop) return;
    const io = new IntersectionObserver((es) => { const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]; if (v) setCurrent(v.target.id); },
      { rootMargin: "-120px 0px -55% 0px" });
    SECTIONS.forEach(([id]) => { const el = document.getElementById(id); if (el) io.observe(el); });
    return () => io.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [desktop]);
  const [s, setS] = useState<any>(null);
  const [rems, setRems] = useState<Reminder[]>([]);
  const [consents, setConsents] = useState<string[]>([]);
  const [notify, setNotify] = useState(false);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    api<any>("/me/settings").then((res) => {
      setS(res);
      setVoicePrefs({
        gender: res?.tts_voice_gender === "male" ? "male" : "female",
        localTts: res?.tts_engine || "auto",
      });
    });
    api<Reminder[]>("/me/reminders").then(setRems).catch(() => {});
    api<any>("/consents").then((c) => setConsents(c.active));
    return () => timers.current.forEach(clearTimeout);
  }, []);

  const update = async (patch: any) => {
    const res = await api<any>("/me/settings", { method: "PUT", body: JSON.stringify(patch) });
    setS(res);
    if (patch.tts_voice_gender !== undefined || patch.tts_engine !== undefined) {
      setVoicePrefs({
        gender: res?.tts_voice_gender === "male" ? "male" : "female",
        localTts: res?.tts_engine || "auto",
        ...(patch.tts_voice_gender !== undefined ? { voices: {} } : {}),
      });
    }
  };
  async function toggleConsent(purpose: string, on: boolean) {
    const r = on ? await api<any>("/consents", { method: "POST", body: JSON.stringify({ purposes: [purpose] }) })
                 : await api<any>(`/consents/${purpose}`, { method: "DELETE" });
    setConsents(r.active);
  }
  // Reminders are scheduled by <DoseNotifier> (service worker, with a "Taken" button) while the app is open.
  useEffect(() => { setNotify(notificationsOn() && typeof Notification !== "undefined" && Notification.permission === "granted"); }, []);
  async function enableNotify() {
    setNotify(await setNotifications(!notify));
  }
  async function erase() {
    if (!confirm(t.deleteConfirm)) return;
    await api("/me", { method: "DELETE" });
    await clearOfflineData();
    setTokens(null);
    try { localStorage.clear(); } catch { /* ignore */ }
    location.href = "/welcome";
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4 desk:max-w-[1120px]">
      <PageTitle>{t.settingsTitle}</PageTitle>
      <div className="desk:grid desk:grid-cols-[240px_minmax(0,1fr)] desk:gap-8">
      {desktop && (
        <nav aria-label={t.settingsTitle} className="sticky top-28 h-fit">
          <ul className="space-y-1">
            {SECTIONS.map(([id, label]) => (
              <li key={id}><a href={`#${id}`} aria-current={current === id ? "true" : undefined}
                className={`flex h-11 items-center rounded-[12px] px-3 font-semibold transition-colors duration-[120ms] ${current === id ? "bg-brand-soft text-text" : "text-muted hover:bg-[var(--d-tile-hover)] hover:text-text"}`}>{label}</a></li>
            ))}
          </ul>
        </nav>
      )}
      <div className="min-w-0 space-y-4">

      <Card id="s-lang" className="scroll-mt-28">
        <CardHeader icon={Languages} title={t.langVoice} />
        <LanguageGrid />
        {s && <Toggle label="Read answers aloud automatically" help="When you ask by voice, the first sentence of the answer is spoken."
                      checked={s.voice_autoplay} onChange={(v) => update({ voice_autoplay: v })} />}
        <div className="pt-2"><Group label={t.speechSpeed}>
          {([["slow", t.speedSlow], ["normal", t.speedNormal], ["fast", t.speedFast]] as const).map(([v, l]) => (
            <Chip key={v} selected={prefs.rate === v} onClick={() => setPrefs({ rate: v })}>{l}</Chip>
          ))}
        </Group></div>
        <VoiceLoopSettings />
        <div className="pt-3"><VoicePicker /></div>
      </Card>

      <Card id="s-display" className="scroll-mt-28 space-y-4">
        <CardHeader icon={MonitorSmartphone} title={t.display} />
        <div className="space-y-1.5">
          <Group label={t.layoutTitle}>
            {([["auto", t.layoutAuto], ["desktop", t.layoutDesktop], ["mobile", t.layoutMobile]] as const).map(([v, l]) => (
              <Chip key={v} selected={prefs.ui === v} onClick={() => setPrefs({ ui: v })}>{l}</Chip>
            ))}
          </Group>
          <p className="text-[0.9375rem] text-muted">{t.layoutHelp}</p>
        </div>
        <Toggle label={t.simpleMode} help={t.simpleModeHelp} checked={prefs.simple} onChange={(v) => setPrefs({ simple: v })} />
        <Group label={t.textSize}>
          {([["md", "A"], ["lg", "A+"], ["xl", "A++"]] as const).map(([v, l]) => (
            <Chip key={v} selected={prefs.text === v} onClick={() => setPrefs({ text: v })} className={v === "md" ? "" : v === "lg" ? "text-[1.15rem]" : "text-[1.3rem]"}>{l}</Chip>
          ))}
        </Group>
        <Group label={t.theme}>
          {([["auto", t.themeAuto], ["light", t.themeLight], ["dark", t.themeDark]] as const).map(([v, l]) => (
            <Chip key={v} selected={prefs.theme === v} onClick={() => setPrefs({ theme: v })}>{l}</Chip>
          ))}
        </Group>
        <Toggle label={t.highContrast} help="Black text and borders for low vision." checked={prefs.contrast === "high"} onChange={(v) => setPrefs({ contrast: v ? "high" : "normal" })} />
        <Toggle label={t.reduceMotion} help="Turns off pulsing and sliding animations." checked={prefs.motion === "reduce"} onChange={(v) => setPrefs({ motion: v ? "reduce" : "auto" })} />
        <div className="space-y-1.5 border-t border-border pt-3">
          <Group label="Chat Response Layout">
            {([["v1", "Chat v1 (Classic)"], ["v2", "Chat v2 (Modular Cards)"]] as const).map(([v, l]) => (
              <Chip key={v} selected={(prefs.chatUi ?? "v2") === v} onClick={() => setPrefs({ chatUi: v })}>{l}</Chip>
            ))}
          </Group>
          <p className="text-[0.875rem] text-muted">
            v2 displays Doctor to Visit and Specific Tests side-by-side as clickable cards with modular Causes, Food & Lifestyle, and Action sections.
          </p>
        </div>
      </Card>

      <Card id="reminders" className="scroll-mt-28 space-y-3">
        <CardHeader icon={BellRing} title={t.reminders} />
        {rems.length === 0 ? <p className="text-muted">{t.noReminders}</p> : (
          <ul className="divide-y divide-border">{rems.map((r) => (
            <li key={r.medication_id + r.slot} className="flex justify-between py-2"><span>{r.name}</span><span className="font-mono font-semibold">{r.time ?? "SOS"}</span></li>))}</ul>
        )}
        {s && (
          <div className="grid gap-2 sm:grid-cols-3">
            {(["morning", "afternoon", "night"] as const).map((slot) => (
              <label key={slot} className="font-semibold">{t[slot]}
                <input type="time" className="input mt-1 font-mono" value={s.reminder_times?.[slot] ?? ""} onChange={(e) => update({ reminder_times: { [slot]: e.target.value } })} /></label>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button icon={CalendarPlus} disabled={!rems.length} onClick={() => downloadFile("/me/reminders.ics", "sushrutai-reminders.ics")}>{t.remindersCalendar}</Button>
          <Button variant="secondary" icon={BellRing} disabled={!rems.length && !notify} onClick={enableNotify}
            aria-pressed={notify}>{notify ? t.remindersNotifyOff : t.remindersNotify}</Button>
          {!swSupported() && <p className="w-full text-[0.875rem] text-muted">{t.remindersNeedsSecure}</p>}
        </div>
      </Card>

      {s && (
        <Card id="s-ai" className="scroll-mt-28 space-y-5">
          <CardHeader icon={Cpu} title="AI Backend" sub="Configure answer models, translation models, local & cloud speech engines, and voice preferences." />

          {/* 1. Answer AI Model */}
          <div className="space-y-2">
            <div>
              <p className="font-semibold">{t.answerSpeed}</p>
              <p className="text-[0.875rem] text-muted">{t.answerSpeedHelp}</p>
            </div>
            <div role="radiogroup" aria-label={t.answerSpeed} className="grid gap-2 sm:grid-cols-3">
              {([["auto", t.modelAuto, t.modelAutoHelp], ["balanced", t.modelBalanced, t.modelBalancedHelp], ["fast", t.modelFast, t.modelFastHelp],
                 ["instant", t.modelInstant, t.modelInstantHelp],
                 ["cloud", t.modelCloud, t.modelCloudHelp],
                 ["gemini", t.modelGemini, t.modelGeminiHelp], ["gemini_fast", t.modelGeminiFast, t.modelGeminiFastHelp]] as const)
                .filter(([k]) => (s.model_options ?? ["balanced"]).includes(k))
                .map(([k, label, help]) => {
                  const offlineBlocked = s.offline_mode && (k === "cloud" || k.startsWith("gemini"));
                  const on = (s.model_pref ?? "auto") === k;
                  return (
                    <button key={k} type="button" role="radio" aria-checked={on} disabled={offlineBlocked}
                      onClick={async () => {
                        if (k === "cloud" && !consents.includes("cloud_llm_deidentified")) {
                          if (!window.confirm(t.cloudConsent)) return;
                          await toggleConsent("cloud_llm_deidentified", true);
                        }
                        if (k.startsWith("gemini") && !consents.includes("cloud_llm_full")) {
                          if (!window.confirm(t.geminiConsent)) return;
                          await toggleConsent("cloud_llm_full", true);
                        }
                        try { localStorage.setItem(CHAT_MODEL_KEY, k); } catch { /* ignore */ }
                        update({ model_pref: k });
                      }}
                      className={`flex min-h-24 flex-col items-start gap-1 rounded-[16px] border-2 p-3 text-start transition-colors disabled:cursor-not-allowed disabled:opacity-60
                        ${on ? "border-brand bg-brand-soft" : "border-border bg-surface hover:border-border-input"}`}>
                      <span className="display text-[1.125rem] font-bold">{label}</span>
                      <span className="text-[0.875rem] leading-snug text-muted">{offlineBlocked ? t.modelOfflineNeeds : help}</span>
                    </button>
                  );
                })}
            </div>
          </div>

          {/* 2. Translation Model */}
          <div className="space-y-2 border-t border-border pt-4">
            <Group label="Preferred Translation Model">
              {([
                ["auto", "Auto (Recommended)"],
                ["indictrans2", "IndicTrans2 (Local)"],
                ["llm", "Local LLM (Ollama)"],
                ["gemini", "Gemini API"],
              ] as const).map(([k, label]) => {
                const blocked = s.offline_mode && k === "gemini";
                return (
                  <Chip key={k} selected={(s.translation_model ?? "auto") === k}
                    onClick={async () => {
                      if (blocked) return;
                      if (k === "gemini" && !consents.includes("cloud_llm_full")) {
                        if (!window.confirm(t.geminiConsent)) return;
                        await toggleConsent("cloud_llm_full", true);
                      }
                      update({ translation_model: k });
                    }}>
                    {label}
                  </Chip>
                );
              })}
            </Group>
            <p className="text-[0.875rem] text-muted">
              Auto uses IndicTrans2 locally for native scripts and local LLM for Hinglish/Banglish; Gemini API translates remotely when consented.
            </p>
          </div>

          {/* 3. Local TTS Voice Gender & Engine */}
          <div className="space-y-3 border-t border-border pt-4">
            <Group label="Consultation & Speech Mode">
              {([
                ["general", "General (Standard Pace)"],
                ["elderly", "Elderly & Patient-Friendly (Longer Pauses, Simpler Words)"],
              ] as const).map(([k, label]) => (
                <Chip key={k} selected={(s.speech_mode ?? "general") === k}
                  onClick={() => {
                    update({ speech_mode: k });
                    try { localStorage.setItem("sushrut.speechMode", k); } catch { /* ignore */ }
                  }}>
                  {label}
                </Chip>
              ))}
            </Group>
            <Group label="Response Style">
              {([
                ["direct", "Direct (Natural & Concise)"],
                ["detailed", "Detailed (Full Rationale)"],
              ] as const).map(([k, label]) => (
                <Chip key={k} selected={(s.response_style ?? "direct") === k}
                  onClick={() => {
                    update({ response_style: k });
                    try { localStorage.setItem("sushrut.responseStyle", k); } catch { /* ignore */ }
                  }}>
                  {label}
                </Chip>
              ))}
            </Group>
            <Group label="Local TTS Voice Gender">
              {([["female", "Female (Default)"], ["male", "Male"]] as const).map(([k, label]) => (
                <Chip key={k} selected={(s.tts_voice_gender ?? "female") === k}
                  onClick={() => update({ tts_voice_gender: k })}>
                  {label}
                </Chip>
              ))}
            </Group>
            <Group label="Local TTS Engine">
              {([
                ["auto", "Auto (Kokoro + Parler / MMS)"],
                ["kokoro", "Kokoro-82M (Fast HI/EN)"],
                ["parler", "Indic Parler-TTS"],
                ["mms", "MMS-TTS (Lightweight)"],
              ] as const).map(([k, label]) => (
                <Chip key={k} selected={(s.tts_engine ?? "auto") === k}
                  onClick={() => update({ tts_engine: k })}>
                  {label}
                </Chip>
              ))}
            </Group>
            <p className="text-[0.875rem] text-muted">
              Kokoro-82M, Indic Parler-TTS, Gemini TTS, and device voices follow Female/Male selection; MMS-TTS uses one built-in voice per language.
            </p>
          </div>

          {/* 4. Cloud Voice & Spoken Routing */}
          <div className="border-t border-border pt-3">
            <p className="text-[0.875rem] leading-snug text-muted">{t.spokenAnswerHelp}</p>
            <SpokenAnswerSetting consents={consents} onConsent={(p) => toggleConsent(p, true)} offline={!!s.offline_mode} />
            <VoiceEngineSetting consents={consents} onConsent={(p) => toggleConsent(p, true)} offline={!!s.offline_mode} />
          </div>

          {/* 5. Hardware & Loaded Models */}
          <DeviceInfo />
        </Card>
      )}

      <Card id="s-privacy" className="scroll-mt-28 space-y-1">
        <CardHeader icon={Lock} title={t.privacy} />
        {s && <Toggle label="SOS Emergency Trigger (108 / 112)" help="Automatically interrupt chat and Talk Live with emergency 108 / 112 guidance when critical red-flag symptoms are detected. Turn off to continue conversing without stopping." checked={s.sos_enabled !== false} onChange={(v) => update({ sos_enabled: v })} />}
        {s && <Toggle label="Neural Correlation Engine (GNN + RNN + CNN)" help="Use GPU/CPU neural graph & temporal models to connect symptoms, labs, medications, and family signals for personalized responses." checked={s.neural_correlation_enabled !== false} onChange={(v) => update({ neural_correlation_enabled: v })} />}
        {s && <Toggle label={t.offlineMode} help={t.offlineModeHelp} checked={s.offline_mode} disabled={s.offline_forced_by_server} onChange={(v) => update({ offline_mode: v })} />}
        {Object.entries(CONSENT_LABEL).map(([k, l]) => <Toggle key={k} label={l} checked={consents.includes(k)} onChange={(v) => toggleConsent(k, v)} />)}
        <div className="flex flex-wrap gap-2 pt-3">
          <Button variant="secondary" icon={Download} onClick={() => downloadFile("/me/export", "sushrutai-my-data.json")}>{t.exportData}</Button>
          <Button variant="danger" icon={Trash2} onClick={erase}>{t.deleteData}</Button>
        </div>
      </Card>

      <Card id="s-help" className="scroll-mt-28 space-y-3">
        <CardHeader icon={HelpCircle} title={t.help} />
        <Banner tone="info" title={t.aboutAi}>
          <ul className="list-disc space-y-1 ps-5">
            <li>The assistant runs on small AI models on this device. It can be wrong — it is not a doctor.</li>
            <li>It reads about 9 in 10 printed lab-report values correctly, and about half of handwritten prescriptions — always check.</li>
            <li>Emergency words (like chest pain or difficulty breathing) are detected by fixed rules, before the AI answers.</li>
            <li>For X-rays it only describes what it sees; a radiologist must review every image.</li>
          </ul>
        </Banner>
        <p className="font-semibold">Ambulance 108 · Emergency 112 · Tele-MANAS 14416 · eSanjeevani (free teleconsultation)</p>
      </Card>
      </div>
      </div>
    </div>
  );
}

/** Hands-free (on by default) and spoken-language detection (off by default: the chosen language is used). */
function VoiceLoopSettings() {
  const { t } = useI18n();
  const loop = useVoiceLoop();
  const [autoLang, setAutoLang] = useState(false);
  const [care, setCare] = useState(false);
  useEffect(() => { setAutoLang(!!voicePrefs().autoLang); setCare(!!voicePrefs().voiceCare); }, []);
  return (
    <div className="pt-1">
      <Toggle label={t.handsFreeSetting} help={t.handsFreeHelp} checked={!!loop?.on} onChange={() => { loop?.toggle(); }} />
      <Toggle label={t.autoLangSetting} help={t.autoLangHelp} checked={autoLang}
        onChange={(v) => { setAutoLang(v); setVoicePrefs({ autoLang: v }); }} />
      <Toggle label={t.voiceCareSetting} help={t.voiceCareHelp} checked={care}
        onChange={(v) => { setCare(v); setVoicePrefs({ voiceCare: v }); }} />
    </div>
  );
}

/** Spoken questions: Transcribe Live, the answer model chosen above, then playback in the heard language. */
function SpokenAnswerSetting({ consents, onConsent, offline }: { consents: string[]; onConsent: (p: string) => Promise<void>; offline: boolean }) {
  const { t } = useI18n();
  const [on, setOn] = useState(spokenRoute());
  const [playback, setPlayback] = useState(voicePrefs().playback ?? (spokenRoute() ? "gemini_tts" : "device"));
  const enable = async (v: boolean) => {
    if (v && !offline) {
      if (!consents.includes("cloud_llm_live")) {
        if (!window.confirm(t.liveConsent)) return;
        await onConsent("cloud_llm_live");
      }
      if (!consents.includes("cloud_llm_full")) {
        if (!window.confirm(t.geminiConsent)) return;
        await onConsent("cloud_llm_full");
      }
    }
    setOn(v);
    setVoicePrefs({ spoken: v, playback: v ? "gemini_tts" : "device" });
    if (v) setPlayback("gemini_tts");
  };
  const pickPlayback = async (p: "device" | "gemini_tts") => {
    if (p === "gemini_tts" && !consents.includes("cloud_llm_live")) {
      if (!window.confirm(t.liveConsent)) return;
      await onConsent("cloud_llm_live");
    }
    setPlayback(p);
    setVoicePrefs({ playback: p });
  };
  return (
    <div className="pt-2">
      <Toggle label={t.spokenAnswer} help={t.spokenAnswerHelp} checked={on} disabled={offline} onChange={enable} />
      <p className="mb-2 mt-2 font-semibold">{t.playbackVoice}</p>
      <div className="flex flex-wrap gap-2">
        <Chip selected={playback === "device"} onClick={() => pickPlayback("device")}>{t.playbackDevice}</Chip>
        <Chip selected={playback === "gemini_tts"} onClick={() => { if (!offline) pickPlayback("gemini_tts"); }}>{t.playbackGemini}</Chip>
      </div>
    </div>
  );
}

/** Read-aloud and listening engine: this device / local voices, or a cloud voice the person chooses. */
function VoiceEngineSetting({ consents, onConsent, offline }: { consents: string[]; onConsent: (p: string) => Promise<void>; offline: boolean }) {
  const { t } = useI18n();
  const [engine, setEngine] = useState<string>("local");
  const [cloud, setCloud] = useState<string[]>([]);
  const [live, setLive] = useState<string[]>([]);
  const [talk, setTalk] = useState<string>("off");
  useEffect(() => {
    setEngine(voicePrefs().engine ?? "local");
    setTalk(voicePrefs().talk ?? "off");
    api<{ cloud_voices?: string[]; live_voices?: string[] }>("/voice/engines").then((r) => {
      setCloud(r.cloud_voices ?? []);
      setLive(r.live_voices ?? []);
    }).catch(() => {});
  }, []);
  const pick = async (e: string) => {
    const gemini = e.startsWith("gemini_");
    const purpose = gemini ? "cloud_llm_live" : "cloud_voice";
    if (e !== "local" && !consents.includes(purpose)) {
      if (!window.confirm(gemini ? t.liveConsent : t.cloudVoiceConsent)) return;
      await onConsent(purpose);
    }
    setEngine(e);
    setVoicePrefs({ engine: e as any });
  };
  const pickTalk = async (v: "off" | "gemini_talk" | "gemini_flash") => {
    if (v !== "off" && !consents.includes("cloud_llm_live")) {
      if (!window.confirm(t.liveConsent)) return;
      await onConsent("cloud_llm_live");
    }
    setTalk(v);
    setVoicePrefs({ talk: v });
  };
  const name: Record<string, string> = {
    sarvam: "Sarvam AI", elevenlabs: "ElevenLabs", gemini_transcribe: t.geminiTranscribe,
  };
  const opts: [string, string][] = [
    ["local", t.voiceEngineLocal],
    ...cloud.map((c) => [c, name[c] || c] as [string, string]),
    ...(live.includes("gemini_transcribe") ? [["gemini_transcribe", t.geminiTranscribe] as [string, string]] : []),
  ];
  return (
    <div className="pt-4">
      <p className="mb-2 font-semibold">{t.voiceEngine}</p>
      <div className="flex flex-wrap gap-2">
        {opts.map(([k, l]) => <Chip key={k} selected={engine === k} onClick={() => { if (k === "local" || !offline) pick(k); }}>{l}</Chip>)}
      </div>
      {cloud.length === 0 && !live.length && <p className="mt-1 text-[0.875rem] text-muted">{t.voiceEngineNoKeys}</p>}
      {live.includes("gemini_talk") && (
        <div className="pt-4">
          <p className="mb-2 font-semibold">{t.liveTalk}</p>
          <div className="flex flex-wrap gap-2">
            {(["off", "gemini_talk", "gemini_flash"] as const).map((k) => (
              <Chip key={k} selected={talk === k} onClick={() => { if (k === "off" || !offline) pickTalk(k); }}>
                {k === "off" ? t.voiceEngineLocal : k === "gemini_flash" ? t.liveFlash : t.liveTalk}
              </Chip>
            ))}
          </div>
          <p className="mt-1 text-[0.875rem] text-muted">{talk === "gemini_flash" ? t.liveFlashHelp : t.liveTalkHelp}</p>
        </div>
      )}
    </div>
  );
}

/** This computer: memory, graphics memory, and which models are loaded right now. */
// model names as people know them (product names, not translated)
const SPEECH_MODEL_NAMES: Record<string, string> = {
  it2: "IndicTrans2 (translation)", conformer: "IndicConformer (listening)", whisper: "Whisper (listening)",
  lid: "Whisper (language detection)", mms: "MMS (read-aloud)", parler: "Indic Parler (read-aloud)",
  kokoro: "Kokoro-82M (read-aloud)",
};

function DeviceInfo() {
  const { t } = useI18n();
  const [d, setD] = useState<any>(null);
  useEffect(() => { api("/system/device").then(setD).catch(() => {}); }, []);
  if (!d) return null;
  const loaded = [...(d.ollama_loaded ?? []).map((m: any) => `${m.name} (${m.vram_gb} GB)`),
                  ...Object.keys(d.speech_models_loaded ?? {}).map((k) => SPEECH_MODEL_NAMES[k] ?? k)];
  return (
    <div className="mt-4 rounded-[14px] bg-surface-2 p-3 text-[0.9375rem]">
      <p className="font-semibold">{t.thisComputer}</p>
      <p className="text-muted">{[d.ram_gb && `${d.ram_gb} GB RAM (${d.ram_free_pct}% free)`, d.gpu && `${d.gpu} · ${d.vram_gb} GB`, `${d.cpu_cores} CPU threads`].filter(Boolean).join(" · ")}</p>
      <p className="text-muted">{t.modelsLoaded}: {loaded.join(", ") || t.none}</p>
    </div>
  );
}
