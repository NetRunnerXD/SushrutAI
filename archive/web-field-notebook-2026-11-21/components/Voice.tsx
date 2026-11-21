"use client";

import { Check, Loader2, Mic, RotateCcw, SendHorizonal, Square, Volume2, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { bcp47, fill, useI18n } from "@/lib/i18n";
import { api, switchProfile } from "@/lib/api";
import {
  browserRecognitionAvailable, isNoServerSpeech, repeatLast, speak, startBrowserRecognition, startServerRecording,
  stopSpeaking, type Recorder,
} from "@/lib/voice";
import { matchCommand } from "@/lib/voiceCommands";
import { useSOS } from "./Emergency";

export type VoiceState = "idle" | "listening" | "transcribing" | "confirm" | "error";

const BARS = 28;
const CONFIRM_SECONDS = 4;

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return document.documentElement.dataset.motion === "reduce" || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Spoken commands ("help", "phir se", "ruko", "go home"): run(text) returns true when the text was a command. */
export function useVoiceCommands() {
  const { t } = useI18n();
  const { openSOS } = useSOS();
  const router = useRouter();
  const [notice, setNotice] = useState<string | null>(null);
  const run = useCallback((text: string): boolean => {
    const cmd = matchCommand(text);
    if (!cmd) return false;
    if (cmd === "emergency") { stopSpeaking(); openSOS(); setNotice(t.cmdEmergency); }
    if (cmd === "repeat") setNotice(repeatLast() ? t.cmdRepeat : t.cmdNothingToRepeat);
    if (cmd === "stop") { stopSpeaking(); setNotice(t.cmdStop); }
    if (cmd === "home") { setNotice(t.cmdHome); router.push("/"); }
    return true;
  }, [openSOS, router, t]);
  useEffect(() => {
    if (!notice) return;
    const id = setTimeout(() => setNotice(null), 4000);
    return () => clearTimeout(id);
  }, [notice]);
  return { run, notice };
}

// ---------------------------------------------------------------- voice actions
export type VoicePlan = { action: string | null; params?: any; confirm?: boolean; say?: string; href?: string };
const YES = /^(yes|yeah|yep|ok|okay|sure|save|do it|confirm|haan|haa|han|ha|ji|ji haan|haan ji|theek hai|thik hai|sahi|bilkul|हाँ|हां|जी|जी हाँ|ठीक है|सही|बिल्कुल)$/i;
const NO = /^(no|nope|cancel|dont|do not|stop|nahi|nahin|na|mat|mat karo|रहने दो|नहीं|ना|मत)$/i;
const short = (s: string) => s.toLowerCase().replace(/[.,!?।']/g, "").replace(/\s+/g, " ").trim();

/** "My BP is 130 over 85", "did I take my night tablets?", "open my reports", "switch to Asha".
 *  handle(text) returns true when the words were an action (or a yes/no for a pending one). */
export function useVoiceActions() {
  const { t, lang } = useI18n();
  const router = useRouter();
  const [pending, setPending] = useState<VoicePlan | null>(null);
  const [result, setResult] = useState<{ text: string; href?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const say = useCallback((s?: string) => { if (s) speak(s, lang, bcp47(lang)); }, [lang]);

  const confirm = useCallback(async (plan: VoicePlan | null) => {
    if (!plan) return;
    setBusy(true);
    try {
      if (plan.action === "log_vital") {
        await api("/me/vitals", { method: "POST", body: JSON.stringify(plan.params.readings) });
      } else if (plan.action === "log_dose") {
        for (const it of plan.params.items) {
          await api("/me/doses", { method: "POST", body: JSON.stringify({ medication_id: it.medication_id, slot: it.slot, status: "taken" }) });
        }
      }
      setPending(null);
      setResult({ text: t.saved, href: plan.action === "log_vital" ? "/records?tab=vitals" : "/medicines" });
      say(t.saved);
      window.dispatchEvent(new Event("sushrut:data-changed")); // open screens (Home, doses) refresh
    } catch (e: any) {
      setResult({ text: e.message });
    } finally {
      setBusy(false);
    }
  }, [say, t]);

  const cancel = useCallback(() => { setPending(null); setResult(null); stopSpeaking(); }, []);

  const handle = useCallback(async (text: string): Promise<boolean> => {
    if (pending) {
      const w = short(text);
      if (YES.test(w)) { await confirm(pending); return true; }
      if (NO.test(w)) { cancel(); return true; }
    }
    let plan: VoicePlan;
    try {
      plan = await api<VoicePlan>("/assistant/parse", { method: "POST", body: JSON.stringify({ text, lang }) });
    } catch {
      return false;
    }
    if (!plan.action) return false;
    setResult(null);
    if (plan.confirm) { setPending(plan); say(plan.say); return true; }
    if (plan.action === "navigate" && plan.href) { say(plan.say); router.push(plan.href); return true; }
    if (plan.action === "switch_profile") {
      const p = plan.params;
      switchProfile(p?.id ? { id: p.id, name: p.name, relation: p.relation ?? "other" } : null);
      return true;
    }
    setResult({ text: plan.say ?? "", href: plan.href });
    say(plan.say);
    return true;
  }, [pending, confirm, cancel, lang, router, say]);

  return { handle, pending, result, busy, confirm: () => confirm(pending), cancel, dismiss: () => setResult(null) };
}

export type VoiceActions = ReturnType<typeof useVoiceActions>;

/** The proposal ("Save BP 130/85?" with Save / Cancel, or say yes / no) or the spoken result of an action. */
export function ActionCard({ a, className = "" }: { a: VoiceActions; className?: string }) {
  const { t } = useI18n();
  if (a.pending) {
    return (
      <div role="alertdialog" aria-label={a.pending.say} className={`note space-y-3 rounded-[6px] border border-border border-t-[5px] border-t-accent bg-surface p-3 text-left shadow-[var(--e2)] ${className}`}>
        <p aria-live="assertive" className="text-[1.0625rem] font-semibold">{a.pending.say}</p>
        <p className="text-[0.875rem] text-muted">{t.sayYesNo}</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={a.confirm} disabled={a.busy} className="btn btn-primary"><Check aria-hidden className="h-5 w-5" />{t.save}</button>
          <button type="button" onClick={a.cancel} className="btn btn-outline"><X aria-hidden className="h-5 w-5" />{t.cancel}</button>
        </div>
      </div>
    );
  }
  if (!a.result?.text) return null;
  return (
    <div role="status" className={`note flex flex-wrap items-center gap-2 rounded-[6px] border border-border bg-accent-soft p-3 text-left ${className}`}>
      <p className="min-w-0 flex-1 font-semibold">{a.result.text}</p>
      {a.result.href && <Link href={a.result.href} className="font-semibold text-info underline">{t.open}</Link>}
      <button type="button" aria-label={t.close} onClick={a.dismiss} className="grid h-12 w-12 place-items-center rounded-full bg-surface"><X aria-hidden className="h-4 w-4" /></button>
    </div>
  );
}

/**
 * Push-to-talk capture: local server STT first, browser recognition as fallback.
 * After transcription the words are shown for a short, pausable countdown ("Did I hear this right?") so a
 * misheard question can be corrected before it is sent. Command phrases are acted on instead of sent.
 */
export function useVoiceCapture(onText: (text: string) => void, { confirm = true }: { confirm?: boolean } = {}) {
  const { lang } = useI18n();
  const cmds = useVoiceCommands();
  const actions = useVoiceActions();
  const [state, setState] = useState<VoiceState>("idle");
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>([]);
  const [heard, setHeard] = useState("");
  const [left, setLeft] = useState<number | null>(null); // countdown seconds; null = paused / not counting
  const rec = useRef<Recorder | null>(null);
  const serverOk = useRef(true);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  // live waveform while listening
  useEffect(() => {
    if (state !== "listening" || !rec.current?.level || prefersReducedMotion()) { setLevels([]); return; }
    let raf = 0, last = 0;
    const ring: number[] = Array(BARS).fill(0);
    const tick = (now: number) => {
      if (now - last > 70) {
        ring.push(rec.current?.level?.() ?? 0);
        ring.shift();
        setLevels([...ring]);
        last = now;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [state]);

  const reset = useCallback(() => { setHeard(""); setLeft(null); setState("idle"); }, []);
  const accept = useCallback((text?: string) => {
    const words = (text ?? heard).trim();
    reset();
    if (words) onTextRef.current(words);
  }, [heard, reset]);

  // auto-send countdown (pauses as soon as the person starts editing)
  useEffect(() => {
    if (state !== "confirm" || left === null) return;
    if (left <= 0) { accept(); return; }
    const id = setTimeout(() => setLeft((n) => (n === null ? null : n - 1)), 1000);
    return () => clearTimeout(id);
  }, [state, left, accept]);

  const start = useCallback(async () => {
    stopSpeaking();
    setError(null);
    setHeard("");
    setLeft(null);
    try {
      if (serverOk.current) rec.current = await startServerRecording(lang);
      else if (browserRecognitionAvailable()) rec.current = startBrowserRecognition(bcp47(lang), setInterim);
      else throw new Error("unsupported");
      setState("listening");
    } catch (e: any) {
      setState("error");
      setError(e?.name === "NotAllowedError" ? "permission" : "failed");
    }
  }, [lang]);

  const toggle = useCallback(async () => {
    if (state !== "listening") return start();
    setState("transcribing");
    try {
      const text = (await rec.current!.stop())?.trim();
      setInterim("");
      if (!text) { setState("idle"); setError("noSpeech"); return; }
      if (cmds.run(text)) { setState("idle"); return; }
      if (await actions.handle(text)) { setState("idle"); return; } // "my BP is 130 over 85", "open my reports"…
      if (!confirm) { setState("idle"); onTextRef.current(text); return; }
      setHeard(text);
      setLeft(CONFIRM_SECONDS);
      setState("confirm");
    } catch (e) {
      if (isNoServerSpeech(e)) serverOk.current = false;
      setState("error");
      setError("failed");
    }
  }, [state, start, cmds, actions, confirm]);

  return {
    state, interim, error, levels, heard, left, notice: cmds.notice, toggle, actions,
    edit: (s: string) => { setHeard(s); setLeft(null); },
    pause: () => setLeft(null),
    accept, retry: () => { reset(); start(); }, cancel: reset,
  };
}

export type VoiceCapture = ReturnType<typeof useVoiceCapture>;

/** Live input level as a pen trace: a mirrored ink ribbon. Decorative: the text label always states "Listening…". */
export function Waveform({ levels, className = "" }: { levels: number[]; className?: string }) {
  if (!levels.length) return null;
  const step = 6, mid = 16;
  const pts = levels.map((l, i) => [i * step + 3, Math.max(1.2, l * 14)] as const);
  const top = pts.map(([x, h]) => `${x},${(mid - h).toFixed(1)}`).join(" L");
  const bottom = [...pts].reverse().map(([x, h]) => `${x},${(mid + h).toFixed(1)}`).join(" L");
  return (
    <svg aria-hidden viewBox={`0 0 ${BARS * step} 32`} className={`h-8 w-40 ${className}`} preserveAspectRatio="none">
      <line x1="0" x2={BARS * step} y1={mid} y2={mid} stroke="var(--border-input)" strokeWidth="1" strokeDasharray="2 4" />
      <path d={`M${top} L${bottom} Z`} className="fill-brand" strokeLinejoin="round" stroke="var(--brand)" strokeWidth="1.5" />
    </svg>
  );
}

/** The ink blot itself: a breathing, morphing drop of ink. Swells with the voice level while listening. */
export function InkBlot({ state, level = 0, className = "", children }: {
  state: VoiceState; level?: number; className?: string; children: ReactNode;
}) {
  return (
    <span data-state={state} className={`blot ${className}`} style={{ ["--lvl" as string]: level.toFixed(2) }}>
      <span aria-hidden className="blot-bleed" />
      <span aria-hidden className="blot-core" />
      <span aria-hidden className="blot-drop" /><span aria-hidden className="blot-drop" /><span aria-hidden className="blot-drop" />
      {state === "transcribing" && (
        <svg aria-hidden viewBox="0 0 100 100" className="blot-ring">
          <circle cx="50" cy="50" r="46" fill="none" stroke="currentColor" strokeWidth="2.5" strokeDasharray="10 9 3 9" strokeLinecap="round" />
        </svg>
      )}
      {children}
    </span>
  );
}

const lastLevel = (levels: number[]) => (levels.length ? Math.min(1, (levels[levels.length - 1] + (levels[levels.length - 2] ?? 0)) / 1.4) : 0);

/** "Did I hear this right?" — editable transcript with a visible, pausable auto-send countdown. */
export function HeardConfirm({ v, className = "" }: { v: VoiceCapture; className?: string }) {
  const { t } = useI18n();
  const id = useId();
  if (v.state !== "confirm") return null;
  return (
    <div role="group" aria-label={t.didIHear} className={`note space-y-2 rounded-[6px] border border-border border-t-[5px] border-t-accent bg-surface p-3 text-left shadow-[var(--e2)] ${className}`}>
      <label htmlFor={id} className="serif block text-[1.125rem] font-bold">{t.didIHear}</label>
      <textarea id={id} rows={2} value={v.heard} onChange={(e) => v.edit(e.target.value)} onFocus={v.pause}
        className="input w-full resize-none" />
      <p aria-live="polite" className="text-[0.875rem] text-muted">
        {v.left !== null ? fill(t.sendingIn, { n: v.left }) : t.heardPaused}</p>
      {v.left !== null && (
        <div aria-hidden className="h-1.5 overflow-hidden rounded-full bg-surface-2">
          <div className="h-full rounded-full bg-accent transition-[width] duration-1000 ease-linear"
               style={{ width: `${(v.left / CONFIRM_SECONDS) * 100}%` }} />
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => v.accept()} disabled={!v.heard.trim()}
          className="btn btn-primary"><SendHorizonal aria-hidden className="h-5 w-5" />{t.send}</button>
        <button type="button" onClick={v.retry} className="btn btn-outline"><RotateCcw aria-hidden className="h-5 w-5" />{t.sayAgain}</button>
      </div>
    </div>
  );
}

const ERR: Record<string, { en: string; hi: string }> = {
  permission: { en: "Microphone is blocked. Allow it in your browser settings, or type instead.",
                hi: "माइक्रोफ़ोन बंद है। ब्राउज़र सेटिंग में अनुमति दें, या लिखकर पूछें।" },
  noSpeech: { en: "I didn't catch that — please try again.", hi: "मैं सुन नहीं पाया — कृपया फिर से बोलें।" },
  failed: { en: "Voice isn't working right now — please type your question.", hi: "आवाज़ अभी काम नहीं कर रही — कृपया लिखकर पूछें।" },
};

export function VoiceError({ v }: { v: VoiceCapture }) {
  const { lang } = useI18n();
  if (!v.error) return null;
  return <p role="alert" className="max-w-xs text-center text-[0.9375rem] text-warn">{(ERR[v.error] ?? ERR.failed)[lang === "hi" ? "hi" : "en"]}</p>;
}

/** The big, calm voice button. Text label always shows the state (never colour/animation alone). */
export function VoiceOrb({ size = "lg", onText }: { size?: "lg" | "md"; onText: (t: string) => void }) {
  const { t } = useI18n();
  const v = useVoiceCapture(onText);
  const root = useRef<HTMLDivElement>(null);
  // While this orb is on screen the floating mic steps aside (two mics side by side would compete).
  useEffect(() => {
    const el = root.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const html = document.documentElement;
    const io = new IntersectionObserver(([e]) => { html.toggleAttribute("data-orb-visible", e.isIntersecting); }, { threshold: 0.25 });
    io.observe(el);
    return () => { io.disconnect(); html.removeAttribute("data-orb-visible"); };
  }, []);
  const dim = size === "lg" ? "h-32 w-32" : "h-24 w-24";
  const label = v.state === "listening" ? t.listening : v.state === "transcribing" ? t.transcribing : t.tapToSpeak;
  const Icon = v.state === "listening" ? Square : v.state === "transcribing" ? Loader2 : Mic;
  return (
    <div ref={root} className="flex flex-col items-center gap-3">
      <ActionCard a={v.actions} className="w-full max-w-md" />
      {v.state === "confirm" ? <HeardConfirm v={v} className="w-full max-w-md" /> : (
        <>
          <button onClick={v.toggle} aria-label={label} aria-pressed={v.state === "listening"} disabled={v.state === "transcribing"}
            className={`rounded-full p-3 ${size === "lg" ? "my-2" : "my-1"}`}>
            <InkBlot state={v.state} level={lastLevel(v.levels)} className={dim}>
              <Icon aria-hidden className={`${size === "lg" ? "h-12 w-12" : "h-9 w-9"} ${v.state === "transcribing" ? "animate-spin" : ""}`} />
            </InkBlot>
          </button>
          <Waveform levels={v.levels} />
          <p aria-live="polite" className="min-h-6 text-center text-[1.0625rem] font-semibold">{v.notice || v.interim || label}</p>
          {v.state === "idle" && size === "lg" && <p className="text-[0.875rem] text-muted">{t.voiceHint}</p>}
        </>
      )}
      <VoiceError v={v} />
    </div>
  );
}

/** Floating mic (mobile): speak from anywhere → confirm → opens Ask with the question. */
export function FloatingMic() {
  const { t } = useI18n();
  const router = useRouter();
  const path = usePathname();
  const v = useVoiceCapture((text) => {
    try { sessionStorage.setItem("sushrut.pendingAsk", text); } catch { /* ignore */ }
    router.push("/ask");
  });
  if (path.startsWith("/ask") || path.startsWith("/welcome")) return null;
  const Icon = v.state === "listening" ? Square : v.state === "transcribing" ? Loader2 : Mic;
  return (
    <>
      {(v.state === "confirm" || v.state === "listening" || v.notice || v.actions.pending || v.actions.result) && (
        <div className="fixed inset-x-4 bottom-[calc(11rem+env(safe-area-inset-bottom))] z-30 md:hidden">
          {v.actions.pending || v.actions.result ? <ActionCard a={v.actions} className="shadow-[var(--e3)]" />
            : v.state === "confirm" ? <HeardConfirm v={v} className="shadow-[var(--e3)]" /> : (
            <div className="note flex items-center gap-3 rounded-[6px] border border-border border-t-[5px] border-t-accent bg-surface p-3 shadow-[var(--e3)]">
              <Waveform levels={v.levels} className="w-28" />
              <p aria-live="polite" className="font-semibold">{v.notice || v.interim || t.listening}</p>
            </div>
          )}
        </div>
      )}
      <button onClick={v.toggle} aria-label={v.state === "listening" ? t.listening : t.speak} aria-pressed={v.state === "listening"}
        className="floating-mic fixed bottom-[calc(5.5rem+env(safe-area-inset-bottom))] right-3 z-30 rounded-full p-1.5 md:hidden">
        <InkBlot state={v.state} level={lastLevel(v.levels)} className="h-16 w-16">
          <Icon aria-hidden className={`h-7 w-7 ${v.state === "transcribing" ? "animate-spin" : ""}`} />
        </InkBlot>
      </button>
    </>
  );
}

export function ListenButton({ onClick, label }: { onClick: () => void; label: string }) {
  return (
    <button onClick={onClick} className="inline-flex min-h-12 items-center gap-1.5 rounded-full border border-border-input bg-surface px-3 text-[0.9375rem] font-semibold hover:bg-accent-soft">
      <Volume2 aria-hidden className="h-4 w-4" />{label}
    </button>
  );
}
