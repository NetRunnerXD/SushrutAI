"use client";

import { Hand, Languages, Mic, MicOff, Pause, Play, RotateCcw, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { startGeminiLive, type LiveHandle, type LiveState } from "@/lib/geminiLive";
import { useI18n } from "@/lib/i18n";
import { repeatLast, speak, voicePrefs } from "@/lib/voice";
import { MedicalAssessmentPanel, type AssessmentPanelData, type FamilyContextSummary } from "./MedicalAssessmentPanel";

const STATE_DOT: Record<LiveState, string> = {
  IDLE: "bg-muted",
  CONNECTING: "bg-warn animate-pulse",
  LISTENING: "bg-ok animate-pulse",
  PROCESSING: "bg-brand animate-pulse",
  SPEAKING: "bg-brand",
  INTERRUPTED: "bg-warn",
  ERROR: "bg-danger",
};

/** Interpret (speech-to-speech) and Talk live (3.8 or legacy Flash). Shown when a Gemini key is configured. */
export function GeminiLiveBar({ lang, sessionId, onEmergency, onGreeting, onAnswer }: {
  lang: string;
  sessionId?: string | null;
  onEmergency: () => void;
  onGreeting?: (text: string) => void;
  onAnswer: (reply: string, question?: string, detail?: Record<string, unknown>) => void;
}) {
  const { t } = useI18n();
  const [voices, setVoices] = useState<string[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [liveState, setLiveState] = useState<LiveState>("IDLE");
  const [muted, setMuted] = useState(false);
  const [paused, setPaused] = useState(false);
  const [line, setLine] = useState("");
  const [heard, setHeard] = useState("");
  const [err, setErr] = useState("");
  const [familyCtx, setFamilyCtx] = useState<FamilyContextSummary | null>(null);
  const [assessmentPanel, setAssessmentPanel] = useState<AssessmentPanelData | null>(null);
  const handleRef = useRef<LiveHandle | null>(null);
  const lastAnswerRef = useRef("");

  useEffect(() => {
    api<{ live_voices?: string[] }>("/voice/engines").then((r) => setVoices(r.live_voices ?? [])).catch(() => {});
  }, []);

  useEffect(() => () => {
    handleRef.current?.stop();
    handleRef.current = null;
  }, []);

  const stop = () => {
    handleRef.current?.stop();
    handleRef.current = null;
    setActive(null);
    setMuted(false);
    setPaused(false);
    setLiveState("IDLE");
  };

  const start = async (mode: "translate" | "talk", model?: string) => {
    if (handleRef.current) stop();
    setErr(""); setLine(""); setHeard(""); setMuted(false); setPaused(false);
    setLiveState("CONNECTING");
    try {
      const c = await api<{ active: string[] }>("/consents").catch(() => ({ active: [] as string[] }));
      if (!c.active.includes("cloud_llm_live")) {
        if (!window.confirm(t.liveConsent)) { setLiveState("IDLE"); return; }
        await api("/consents", { method: "POST", body: JSON.stringify({ purposes: ["cloud_llm_live"] }) });
      }
      const preset = voicePrefs().silencePreset ?? "patient";
      const silenceMs = preset === "elderly" ? 4500 : preset === "standard" ? 2000 : 3500;
      const h = await startGeminiLive({
        mode, model, lang, targetLang: lang, sessionId, silenceMs,
        onStateChange: setLiveState,
        onEvent: (ev) => {
          if (ev.type === "greeting" && ev.text) {
            setLine(ev.text);
            onGreeting?.(ev.text);
          }
          if (ev.type === "interim" || ev.type === "input_transcript" || ev.type === "final") setHeard(ev.text || "");
          if (ev.type === "output_transcript") { setLine(ev.text || ""); if (ev.text) lastAnswerRef.current = ev.text; }
          if (ev.type === "answer" && ev.text) {
            lastAnswerRef.current = ev.spoken_response || ev.text;
            if (ev.family_context) setFamilyCtx(ev.family_context as FamilyContextSummary);
            if (ev.assessment_panel) setAssessmentPanel(ev.assessment_panel as AssessmentPanelData);
            onAnswer(ev.text, ev.question, ev as unknown as Record<string, unknown>);
          }
          if (ev.type === "red_flag") { setLine(ev.text || ""); onEmergency(); }
          if (ev.type === "error") { setErr(ev.message || t.liveStopped); stop(); setLiveState("ERROR"); }
        },
      });
      handleRef.current = h;
      setActive(model || mode);
    } catch {
      setErr(t.liveStopped);
      setLiveState("ERROR");
    }
  };

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    handleRef.current?.setMuted(next);
  };

  const togglePause = () => {
    const next = !paused;
    setPaused(next);
    handleRef.current?.setPaused(next);
  };

  const interruptNow = () => {
    handleRef.current?.interrupt();
  };

  const replay = () => {
    if (!repeatLast() && lastAnswerRef.current) {
      speak(lastAnswerRef.current, lang);
    }
  };

  if (!voices.length) return null;
  const talk = voicePrefs().talk;
  return (
    <div className="mb-2 space-y-2 px-1">
      <div className="flex flex-wrap items-center gap-2">
        {voices.includes("gemini_translate") && (
          <button type="button" onClick={() => active === "translate" ? stop() : start("translate")}
            aria-pressed={active === "translate"}
            className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-[0.875rem] font-semibold ${active === "translate" ? "bg-brand-soft" : "bg-surface-2"}`}>
            <Languages aria-hidden className="h-4 w-4" />{t.liveInterpret}</button>
        )}
        {voices.includes("gemini_talk") && talk !== "gemini_flash" && (
          <button type="button" onClick={() => active === "talk" ? stop() : start("talk")}
            aria-pressed={active === "talk"}
            className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-[0.875rem] font-semibold ${active === "talk" ? "bg-brand-soft" : "bg-surface-2"}`}>
            {t.liveTalk}</button>
        )}
        {voices.includes("gemini_flash") && talk === "gemini_flash" && (
          <button type="button" onClick={() => active === "flash" ? stop() : start("talk", "flash")}
            aria-pressed={active === "flash"}
            className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-[0.875rem] font-semibold ${active === "flash" ? "bg-brand-soft" : "bg-surface-2"}`}>
            {t.liveFlash}</button>
        )}
        {handleRef.current && active && (
          <>
            <span className="inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 text-[0.75rem] font-semibold text-muted" aria-live="polite">
              <span aria-hidden className={`h-2 w-2 rounded-full ${STATE_DOT[liveState]}`} />
              {liveState}
            </span>
            <button type="button" onClick={toggleMute} aria-pressed={muted}
              className={`inline-flex min-h-11 items-center gap-1 rounded-full px-3 text-[0.875rem] font-semibold ${muted ? "bg-warn-soft text-warn" : "bg-surface-2"}`}>
              {muted ? <MicOff aria-hidden className="h-4 w-4" /> : <Mic aria-hidden className="h-4 w-4" />}
              {muted ? (t.unmute || "Unmute") : (t.mute || "Mute")}
            </button>
            <button type="button" onClick={togglePause} aria-pressed={paused}
              className="inline-flex min-h-11 items-center gap-1 rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">
              {paused ? <Play aria-hidden className="h-4 w-4" /> : <Pause aria-hidden className="h-4 w-4" />}
              {paused ? (t.resumeVoice || "Resume") : (t.pause || "Pause")}
            </button>
            {liveState === "SPEAKING" && (
              <button type="button" onClick={interruptNow}
                className="inline-flex min-h-11 items-center gap-1 rounded-full bg-warn-soft px-3 text-[0.875rem] font-semibold text-warn">
                <Hand aria-hidden className="h-4 w-4" />{t.interrupt || "Interrupt"}
              </button>
            )}
            <button type="button" onClick={stop} className="inline-flex min-h-11 items-center gap-1 rounded-full bg-danger-soft px-3 text-[0.875rem] font-semibold text-danger">
              <Square aria-hidden className="h-3.5 w-3.5" />{t.endConsultation || t.stop}</button>
          </>
        )}
        {lastAnswerRef.current && !active && (
          <button type="button" onClick={replay} className="inline-flex min-h-11 items-center gap-1 rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">
            <RotateCcw aria-hidden className="h-3.5 w-3.5" />{t.repeat || "Replay"}
          </button>
        )}
      </div>
      {(heard || line) && (
        <p aria-live="polite" className="text-[0.9375rem] text-muted">
          {heard && <span className="block">{heard}</span>}
          {line && <span className="block font-semibold text-text">{line}</span>}
        </p>
      )}
      {err && <p role="alert" className="text-[0.9375rem] font-semibold text-danger">{err}</p>}
      {(assessmentPanel as any)?.recommendation_ready && (
        <MedicalAssessmentPanel familyContext={familyCtx} assessment={assessmentPanel} chiefComplaint={heard} />
      )}
    </div>
  );
}
