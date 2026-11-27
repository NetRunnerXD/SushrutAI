"use client";

import { Mic, MicOff, Pause } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { bcp47, useI18n } from "@/lib/i18n";
import {
  readPage, repeatLast, setHeardLang, setVoicePrefs, speak, startHandsFree, stopSpeaking, voicePrefs,
  type HandsFree, type HandsFreeState, type VoiceFeatures,
} from "@/lib/voice";
import { matchIntent, type VoiceIntent } from "@/lib/voiceIntents";
import { useSOS } from "./Emergency";
import { ActionCard, useVoiceActions, type VoiceActions } from "./Voice";

type Loop = {
  on: boolean; paused: boolean; state: HandsFreeState; partialText: string;
  toggle: () => Promise<void>; setPaused: (p: boolean) => void;
  finishSpeaking: () => void;
  setQuestionHandler: (fn: ((text: string) => void) | null) => void;
  setBargeInHandler: (fn: ((spokenSoFar: string) => void) | null) => void;
  actions: VoiceActions;
};
const Ctx = createContext<Loop | null>(null);
export const useVoiceLoop = () => useContext(Ctx);

/** Ask page listens for "explain simply" / "tell me more" through this event. */
export const VOICE_INTENT_EVENT = "sushrut:voice-intent";
/** DoseNotifier listens: "snooze" (again in 15 minutes) or "cancelReminder" (no more for that dose today). */
export const REMINDER_EVENT = "sushrut:reminder";

export function VoiceLoopProvider({ children }: { children: ReactNode }) {
  const { t, lang } = useI18n();
  const router = useRouter();
  const path = usePathname();
  const { openSOS } = useSOS();
  const [on, setOn] = useState(false);
  const [paused, setPausedState] = useState(false);
  const [state, setState] = useState<HandsFreeState>("idle");
  const [partialText, setPartialText] = useState("");
  const hf = useRef<HandsFree | null>(null);
  const question = useRef<((text: string) => void) | null>(null);
  const bargeInCb = useRef<((spokenSoFar: string) => void) | null>(null);
  const pausedRef = useRef(false);

  const setPaused = useCallback((p: boolean) => { pausedRef.current = p; setPausedState(p); if (p) stopSpeaking(); }, []);

  const control = useCallback((intent: VoiceIntent) => {
    switch (intent) {
      case "emergency": stopSpeaking(); openSOS(); break;
      case "stop": stopSpeaking(); break;
      case "pause": setPaused(true); break;
      case "resume": setPaused(false); break;
      case "repeat": repeatLast(); break;
      case "read": readPage(lang); break;
      case "home": router.push("/"); break;
      case "summary": router.push("/me/visit?speak=1"); break;
      case "snooze": case "cancelReminder":
        window.dispatchEvent(new CustomEvent(REMINDER_EVENT, { detail: intent }));
        break;
      case "simpler": case "more":
        window.dispatchEvent(new CustomEvent(VOICE_INTENT_EVENT, { detail: intent }));
        break;
      default: break;  // yes / no with nothing pending: nothing to do
    }
  }, [lang, openSOS, router, setPaused]);

  const actions = useVoiceActions(control);
  const actionsRef = useRef(actions);
  actionsRef.current = actions;

  const onUtterance = useCallback(async (text: string, f?: VoiceFeatures) => {
    if (f && voicePrefs().voiceCare) {
      const msg = voiceCare(f);
      if (msg) speak(t.voiceTired, lang, bcp47(lang));
    }
    const intent = matchIntent(text);
    if (pausedRef.current) {  // paused: only "continue" (or an emergency) wakes the conversation
      if (intent === "resume") setPaused(false);
      else if (intent === "emergency") control("emergency");
      return;
    }
    if (intent && !(actionsRef.current.pending && (intent === "yes" || intent === "no"))) { control(intent); return; }
    if (await actionsRef.current.handle(text)) return;  // yes / no for a pending action, readings, navigation, …
    if (question.current) { question.current(text); return; }
    try { sessionStorage.setItem("sushrut.pendingAsk", text); } catch { /* ignore */ }
    router.push("/ask");
  }, [control, router, setPaused, t, lang]);
  const utterRef = useRef(onUtterance);
  utterRef.current = onUtterance;

  const start = useCallback(async () => {
    if (hf.current) return;
    hf.current = await startHandsFree(lang, {
      onUtterance: (s, heardLang, f) => {
        setPartialText("");
        if (heardLang) setHeardLang(heardLang);
        utterRef.current(s, f);
      },
      onPartial: (_frag, merged) => setPartialText(merged),
      onBargeIn: (spoken) => bargeInCb.current?.(spoken),
      onState: (s) => {
        setState(s);
        if (s === "session_paused") setPaused(true);
      },
      onError: () => setState("idle"),
    });
    setOn(true);
  }, [lang, setPaused]);
  const stop = useCallback(() => { hf.current?.stop(); hf.current = null; setOn(false); setPartialText(""); setState("idle"); }, []);

  const toggle = useCallback(async () => {
    if (hf.current) { stop(); setVoicePrefs({ handsFree: false }); return; }
    try { await start(); setVoicePrefs({ handsFree: true }); setPaused(false); } catch { /* mic blocked */ }
  }, [start, stop, setPaused]);

  // On by default: start by itself only when the microphone is already allowed (no surprise permission prompt)
  useEffect(() => {
    if (voicePrefs().handsFree === false || path.startsWith("/welcome")) return;
    let live = true;
    navigator.permissions?.query({ name: "microphone" as PermissionName })
      .then((p) => { if (live && p.state === "granted") start().catch(() => {}); }).catch(() => {});
    return () => { live = false; };
  }, [start, path]);
  // a new language means a new listener in that language
  useEffect(() => { if (hf.current) { stop(); start().catch(() => {}); } }, [lang]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => hf.current?.stop(), []);

  const finishSpeaking = useCallback(() => {
    hf.current?.finishNow?.();
  }, []);

  const value: Loop = {
    on, paused, state, partialText, toggle, setPaused, finishSpeaking,
    setQuestionHandler: (fn) => { question.current = fn; },
    setBargeInHandler: (fn) => { bargeInCb.current = fn; },
    actions,
  };
  return (
    <Ctx.Provider value={value}>
      {children}
      {(actions.pending || actions.result) && (
        <div className="fixed inset-x-4 bottom-[calc(11rem+env(safe-area-inset-bottom))] z-40 md:inset-x-auto md:bottom-28 md:end-6 md:w-[380px] print:hidden">
          <ActionCard a={actions} className="shadow-[var(--e3)]" />
        </div>
      )}
      {on && (
        <LoopPill
          paused={paused}
          state={state}
          hasPartial={Boolean(partialText)}
          lang={lang}
          onFinish={finishSpeaking}
          onResume={() => setPaused(false)}
          onOff={toggle}
          labels={t}
        />
      )}
    </Ctx.Provider>
  );
}

/** A small status pill while hands-free is on: listening / hearing / paused, explicit finish button, and switch off. */
function LoopPill({ paused, state, hasPartial, lang, onFinish, onResume, onOff, labels: t }: {
  paused: boolean; state: Loop["state"]; hasPartial: boolean; lang: string;
  onFinish: () => void; onResume: () => void; onOff: () => void; labels: any;
}) {
  const showFinish = !paused && (hasPartial || state === "waiting_for_continuation" || state === "speech");
  const finishLabel = lang === "bn" ? "আমি বলা শেষ করেছি" : lang === "hi" ? "मैंने बोल दिया है" : "I'm finished speaking";
  return (
    <div role="status" aria-live="polite"
      className="fixed start-1/2 top-[calc(4.25rem+env(safe-area-inset-top))] z-40 flex -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-[0.875rem] font-semibold shadow-[var(--rim),var(--e2)] rtl:translate-x-1/2 print:hidden">
      {paused ? <Pause aria-hidden className="h-4 w-4 text-warn" />
        : <Mic aria-hidden className={`h-4 w-4 ${state === "speech" ? "text-brand" : "text-ok"}`} />}
      <span>{paused ? t.voicePaused : state === "waiting_for_continuation" ? (lang === "bn" ? "ধীরে বলুন, শুনছি…" : "Still listening…") : t.voiceLoopOn}</span>
      {showFinish && (
        <button type="button" onClick={onFinish} className="rounded-full bg-brand px-2.5 py-0.5 text-[0.8125rem] text-brand-ink">
          {finishLabel}
        </button>
      )}
      {paused && <button type="button" onClick={onResume} className="rounded-full bg-brand-soft px-2 py-0.5 text-brand">{t.resumeVoice}</button>}
      <button type="button" onClick={onOff} aria-label={t.voiceLoopOff} title={t.voiceLoopOff}
        className="grid h-7 w-7 place-items-center rounded-full hover:bg-surface-2"><MicOff aria-hidden className="h-4 w-4" /></button>
    </div>
  );
}

/** Personal baseline (opt-in, on this device only): the usual loudness and speaking pace, learnt from the first
 *  utterances. Two utterances in a row that are much quieter AND much slower than usual → a gentle question,
 *  at most once a day. Returns true when it is time to ask. */
const BASE_KEY = "sushrut.voiceBaseline";
function voiceCare(f: VoiceFeatures): boolean {
  if (f.ms < 1200 || f.words < 3) return false;  // too short to judge
  const wps = f.words / (f.ms / 1000);
  let b: { n: number; rms: number; wps: number; low: number; asked?: string } = { n: 0, rms: 0, wps: 0, low: 0 };
  try { b = { ...b, ...JSON.parse(localStorage.getItem(BASE_KEY) || "{}") }; } catch { /* fresh */ }
  let ask = false;
  if (b.n >= 5) {
    const tired = f.rms < 0.6 * b.rms && wps < 0.75 * b.wps;
    b.low = tired ? b.low + 1 : 0;
    const today = new Date().toDateString();
    if (b.low >= 2 && b.asked !== today) { ask = true; b.asked = today; b.low = 0; }
    if (!tired) { b.rms = 0.9 * b.rms + 0.1 * f.rms; b.wps = 0.9 * b.wps + 0.1 * wps; }  // the baseline follows normal days only
  } else {
    b.rms = (b.rms * b.n + f.rms) / (b.n + 1);
    b.wps = (b.wps * b.n + wps) / (b.n + 1);
  }
  b.n += 1;
  try { localStorage.setItem(BASE_KEY, JSON.stringify(b)); } catch { /* ignore */ }
  return ask;
}
