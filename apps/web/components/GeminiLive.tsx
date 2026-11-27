"use client";

import { AudioLines, Languages, Loader2, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, reportServiceFailure } from "@/lib/api";
import { startGeminiLive, type LiveEvent, type LiveHandle } from "@/lib/geminiLive";
import { useI18n } from "@/lib/i18n";
import { playAudioCue, voicePrefs } from "@/lib/voice";

export type LiveAnswerPayload = {
  question?: string;
  answer: LiveEvent;
};

export function useGeminiLive({ lang, sessionId, useRecord, speechMode = "general", responseStyle = "direct", voiceName, history, onEmergency, onAnswer }: {
  lang: string;
  sessionId?: string | null;
  useRecord?: boolean;
  speechMode?: "general" | "elderly";
  responseStyle?: "direct" | "detailed";
  voiceName?: string;
  history?: { role: string; content: string }[];
  onEmergency: () => void;
  onAnswer: (payload: LiveAnswerPayload) => void;
}) {
  const { t } = useI18n();
  const [voices, setVoices] = useState<string[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [processing, setProcessing] = useState(false);
  const [doctorClarifying, setDoctorClarifying] = useState(false);
  const [interruptionHint, setInterruptionHint] = useState("");
  const [line, setLine] = useState("");
  const [heard, setHeard] = useState("");
  const [err, setErr] = useState("");
  const [pauseSec, setPauseSec] = useState(0);
  const heardRef = useRef("");
  const lineRef = useRef("");
  const lastTranscriptAtRef = useRef(0);
  const pauseCueTierRef = useRef<0 | 15 | 25>(0);
  const answeredThisTurnRef = useRef(false);
  const lastActivityRef = useRef(Date.now());
  const handleRef = useRef<LiveHandle | null>(null);
  const sessionIdRef = useRef(sessionId ?? null);
  const useRecordRef = useRef(!!useRecord);
  const speechModeRef = useRef<"general" | "elderly">(speechMode);
  const responseStyleRef = useRef<"direct" | "detailed">(responseStyle);
  const voiceNameRef = useRef<string | undefined>(voiceName);
  voiceNameRef.current = voiceName;
  const historyRef = useRef(history ?? []);
  const prevHistoryLenRef = useRef((history ?? []).length);
  const replaceHistoryRef = useRef(false);

  useEffect(() => {
    const nextHistory = history ?? [];
    if (nextHistory.length < prevHistoryLenRef.current) {
      replaceHistoryRef.current = true;
    }
    prevHistoryLenRef.current = nextHistory.length;
    sessionIdRef.current = sessionId ?? null;
    useRecordRef.current = !!useRecord;
    speechModeRef.current = speechMode;
    responseStyleRef.current = responseStyle;
    historyRef.current = nextHistory;
    if (handleRef.current) {
      handleRef.current.syncContext({
        sessionId: sessionIdRef.current,
        useRecord: useRecordRef.current,
        speechMode: speechModeRef.current,
        responseStyle: responseStyleRef.current,
        history: historyRef.current,
        replaceHistory: replaceHistoryRef.current,
      });
      replaceHistoryRef.current = false;
    }
  }, [sessionId, useRecord, speechMode, responseStyle, history]);

  // Adaptive pause & sustained-speech barge-in monitor when Talk Live is active
  useEffect(() => {
    if (!active) { setPauseSec(0); return; }
    lastActivityRef.current = Date.now();
    let loudStreak = 0;
    const id = setInterval(() => {
      const lvl = handleRef.current?.level?.() ?? 0;
      const aiSpeaking = handleRef.current?.isPlayingAudio?.() ?? false;
      if (aiSpeaking) {
        // Require sustained loud speech (>0.22 for 2 ticks = 1s) so speaker echo never cuts off TTS
        if (lvl > 0.22) {
          loudStreak += 1;
          if (loudStreak >= 2) {
            loudStreak = 0;
            lastActivityRef.current = Date.now();
            handleRef.current?.interruptAudio();
            handleRef.current?.sendBargeIn({
              text: heardRef.current,
              spokenPrefix: lineRef.current,
            });
            setPauseSec(0);
          }
        } else {
          loudStreak = 0;
        }
        return;
      }
      loudStreak = 0;
      if (lvl > 0.06) {
        lastActivityRef.current = Date.now();
        pauseCueTierRef.current = 0;
        setPauseSec(0);
      } else if (!processing) {
        const sec = Math.floor((Date.now() - lastActivityRef.current) / 1000);
        setPauseSec(sec);
        if (speechModeRef.current === "elderly") {
          if (sec >= 25 && pauseCueTierRef.current < 25) {
            pauseCueTierRef.current = 25;
            playAudioCue("gentle_prompt", { elderly: true });
          } else if (sec >= 15 && pauseCueTierRef.current < 15) {
            pauseCueTierRef.current = 15;
            playAudioCue("thinking_pause", { elderly: true });
          }
        }
      }
    }, 500);
    return () => clearInterval(id);
  }, [active, processing]);

  useEffect(() => {
    api<{ live_voices?: string[] }>("/voice/engines").then((r) => setVoices(r.live_voices ?? [])).catch(() => {});
  }, []);

  const stop = () => {
    if (handleRef.current) playAudioCue("listen_stop", { elderly: speechModeRef.current === "elderly" });
    handleRef.current?.stop();
    handleRef.current = null;
    setActive(null);
    setProcessing(false);
    setDoctorClarifying(false);
  };

  const start = async (mode: "translate" | "talk", model?: string) => {
    if (handleRef.current) stop();
    setErr(""); setLine(""); setHeard(""); heardRef.current = ""; lineRef.current = ""; answeredThisTurnRef.current = false; setProcessing(false); setDoctorClarifying(false);
    try {
      const c = await api<{ active: string[] }>("/consents").catch(() => ({ active: [] as string[] }));
      if (!c.active.includes("cloud_llm_live")) {
        if (!window.confirm(t.liveConsent)) return;
        await api("/consents", { method: "POST", body: JSON.stringify({ purposes: ["cloud_llm_live"] }) });
      }
      const replaceFlag = replaceHistoryRef.current;
      replaceHistoryRef.current = false;
      const h = await startGeminiLive({
        mode, model, lang, targetLang: lang,
        sessionId: sessionIdRef.current,
        useRecord: useRecordRef.current,
        speechMode: speechModeRef.current,
        responseStyle: responseStyleRef.current,
        voiceName: voiceNameRef.current,
        history: historyRef.current,
        replaceHistory: replaceFlag,
        onEvent: (ev) => {
          const elderly = speechModeRef.current === "elderly";
          if (ev.type === "ready") { playAudioCue("listen_start", { elderly }); lastActivityRef.current = Date.now(); pauseCueTierRef.current = 0; setPauseSec(0); }
          if (ev.type === "interim" || ev.type === "input_transcript" || ev.type === "final") {
            lastActivityRef.current = Date.now();
            lastTranscriptAtRef.current = Date.now();
            pauseCueTierRef.current = 0;
            setPauseSec(0);
            const txt = ev.text || "";
            if (txt) {
              setHeard(txt);
              heardRef.current = txt;
            }
            if (ev.type === "final") {
              answeredThisTurnRef.current = false;
              lineRef.current = "";
              setInterruptionHint("");
            }
          }
          if (ev.type === "barge_in_ack") {
            if (ev.interruption_type === "CORRECTION") setInterruptionHint("Correction noted");
            else if (ev.interruption_type === "ADDED_SYMPTOM") setInterruptionHint("Symptom added");
            else if (ev.interruption_type === "THINKING_PAUSE") {
              setInterruptionHint("Take your time…");
              playAudioCue("thinking_pause", { elderly });
            }
            if (ev.should_stop_tts) {
              handleRef.current?.interruptAudio();
              playAudioCue("barge_in_ack", { elderly });
            }
          }
          if (ev.type === "processing") {
            setProcessing(true);
            playAudioCue("send", { elderly });
            answeredThisTurnRef.current = false;
            if (ev.question) {
              setHeard(ev.question);
              heardRef.current = ev.question;
            }
          }
          if (ev.type === "output_transcript") {
            const chunk = ev.text || "";
            if (chunk) {
              lineRef.current = lineRef.current ? `${lineRef.current} ${chunk}`.trim() : chunk;
              setLine(lineRef.current);
            }
          }
          if (ev.type === "doctor_interrupt") {
            setDoctorClarifying(true);
            playAudioCue("gentle_prompt", { elderly });
            const preserved = (ev.preserved_text || "").trim();
            if (preserved) {
              setHeard(preserved);
              heardRef.current = preserved;
            }
          }
          if (ev.type === "answer") {
            setProcessing(false);
            setDoctorClarifying(false);
            playAudioCue("receive", { elderly });
            answeredThisTurnRef.current = true;
            lineRef.current = "";
            const q = (ev.question || heardRef.current || "").trim();
            if (ev.reply || ev.text || ev.structured) {
              onAnswer({ question: q || undefined, answer: ev });
            }
          }
          if (ev.type === "turn_complete") {
            setProcessing(false);
            setDoctorClarifying(false);
            const fallbackText = lineRef.current.trim();
            if (!answeredThisTurnRef.current && fallbackText) {
              const q = heardRef.current.trim();
              onAnswer({
                question: q || undefined,
                answer: {
                  type: "answer",
                  reply: fallbackText,
                  text: fallbackText,
                  structured: { kind: "general_health", reply: fallbackText },
                },
              });
            }
            answeredThisTurnRef.current = false;
            lineRef.current = "";
          }
          if (ev.type === "red_flag") {
            setProcessing(false);
            setLine(ev.text || "");
            playAudioCue("emergency", { elderly });
            onEmergency();
          }
          if (ev.type === "error") {
            setProcessing(false);
            const msg = ev.message || t.liveStopped;
            setErr(msg);
            playAudioCue("error", { elderly });
            reportServiceFailure({ service: "Gemini Live", reason: msg });
            stop();
          }
        },
      });
      handleRef.current = h;
      setActive(model || mode);
    } catch {
      playAudioCue("error");
      setProcessing(false);
      setErr(t.liveStopped);
    }
  };

  const finishSpeaking = () => {
    handleRef.current?.endAudio();
    lastActivityRef.current = Date.now();
    setPauseSec(0);
  };

  return { voices, active, processing, doctorClarifying, interruptionHint, line, heard, err, pauseSec, speechMode, hasHandle: !!handleRef.current, start, stop, finishSpeaking };
}

export type GeminiLiveState = ReturnType<typeof useGeminiLive>;

/** Compact Interpret & Talk Live pills sized to fit in the composer toolbar row. */
export function GeminiLiveButtons({ live }: { live: GeminiLiveState }) {
  const { t } = useI18n();
  if (!live.voices.length) return null;
  const talk = voicePrefs().talk;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {live.voices.includes("gemini_translate") && (
        <button type="button" onClick={() => live.active === "translate" ? live.stop() : live.start("translate")}
          aria-pressed={live.active === "translate"} title={t.liveInterpret}
          className={`inline-flex h-8 items-center gap-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-colors ${
            live.active === "translate" ? "bg-brand-soft text-brand" : "bg-surface-2 text-muted hover:text-text"
          }`}>
          <Languages aria-hidden className="h-3.5 w-3.5" />{t.liveInterpret}</button>
      )}
      {live.voices.includes("gemini_talk") && talk !== "gemini_flash" && (
        <button type="button" onClick={() => live.active === "talk" ? live.stop() : live.start("talk")}
          aria-pressed={live.active === "talk"} title={t.liveTalk}
          className={`inline-flex h-8 items-center gap-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-colors ${
            live.active === "talk" ? "bg-brand-soft text-brand" : "bg-surface-2 text-muted hover:text-text"
          }`}>
          <AudioLines aria-hidden className="h-3.5 w-3.5" />{t.liveTalk}</button>
      )}
      {live.voices.includes("gemini_flash") && talk === "gemini_flash" && (
        <button type="button" onClick={() => live.active === "flash" ? live.stop() : live.start("talk", "flash")}
          aria-pressed={live.active === "flash"} title={t.liveFlash}
          className={`inline-flex h-8 items-center gap-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-colors ${
            live.active === "flash" ? "bg-brand-soft text-brand" : "bg-surface-2 text-muted hover:text-text"
          }`}>
          <AudioLines aria-hidden className="h-3.5 w-3.5" />{t.liveFlash}</button>
      )}
      {live.hasHandle && live.active && (
        <button type="button" onClick={live.stop}
          className="voice-active-ring inline-flex h-8 items-center gap-1 rounded-full border border-brand/40 bg-brand-soft px-2.5 text-[0.75rem] font-semibold text-brand">
          <span aria-hidden className="eq-bars text-brand"><span /><span /><span /></span>
          <Square aria-hidden className="h-2.5 w-2.5 fill-current" />{t.stop}</button>
      )}
    </div>
  );
}

/** Live transcript / processing / error status banner shown above the prompt when active. */
export function GeminiLiveStatus({ live }: { live: GeminiLiveState }) {
  const { t, lang } = useI18n();
  if (!live.active && !live.heard && !live.line && !live.processing && !live.err) return null;
  const finishLabel = lang === "bn" ? "আমি বলা শেষ করেছি" : lang === "hi" ? "मेरी बात पूरी हो गई है" : "I'm finished speaking";
  const pauseHint = live.speechMode === "elderly" && live.active && !live.processing
    ? live.pauseSec >= 25
      ? (lang === "bn" ? "আপনি কি আর কিছু বলতে চাইছেন, নাকি আমি পরের প্রশ্নটা করব?" : lang === "hi" ? "क्या आप कुछ और जोड़ना चाहते हैं, या मैं अगला सवाल पूछूँ?" : "Would you like to add anything else, or should I ask the next question?")
      : live.pauseSec >= 15
        ? (lang === "bn" ? "কোনো তাড়া নেই, আপনি সময় নিয়ে বলুন।" : lang === "hi" ? "कोई जल्दी नहीं है, आप आराम से बोलिए।" : "No rush — take your time to remember…")
        : live.pauseSec >= 3
          ? (lang === "bn" ? "শুনছি — আপনার সময় নিয়ে বলুন…" : lang === "hi" ? "सुन रहा हूँ — आराम से बोलें…" : "Taking your time — still listening…")
          : null
    : null;
  return (
    <div className="mb-1.5 space-y-1.5">
      {(live.active || live.heard || live.line || live.processing) && (
        <div aria-live="polite" className="pop-in space-y-1.5 rounded-[12px] border border-border/70 bg-surface-2/80 px-3 py-2 text-[0.8125rem]">
          {live.active && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2 text-[0.75rem] font-semibold text-brand">
                {live.processing ? (
                  <>
                    <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" />
                    <span>{t.uxProcessing || "Processing request…"}</span>
                  </>
                ) : (
                  <span>{pauseHint || t.listening || "Listening…"}</span>
                )}
                {live.doctorClarifying && (
                  <span className="rounded-full border border-amber-500/35 bg-amber-500/10 px-2 py-0.5 text-[0.6875rem] font-semibold text-amber-700">
                    {lang === "bn" ? "ডাক্তার সংক্ষেপে জিজ্ঞাসা করছেন…" : lang === "hi" ? "डॉक्टर थोड़ा स्पष्ट पूछ रहे हैं…" : "Doctor gently clarifying…"}
                  </span>
                )}
                {live.interruptionHint && !live.doctorClarifying && (
                  <span className="rounded-full border border-brand/30 bg-brand-soft/60 px-2 py-0.5 text-[0.6875rem] font-semibold text-brand">
                    {live.interruptionHint}
                  </span>
                )}
              </div>
              {!live.processing && (
                <button
                  type="button"
                  onClick={live.finishSpeaking}
                  className="rounded-full border border-brand/35 bg-brand-soft/60 px-3 py-1 text-[0.75rem] font-semibold text-brand hover:bg-brand-soft"
                >
                  {finishLabel}
                </button>
              )}
            </div>
          )}
          {live.heard && <p className="text-muted"><span className="font-semibold text-text/80">You:</span> {live.heard}</p>}
          {live.line && <p className="font-semibold text-text"><span className="text-brand">Live AI:</span> {live.line}</p>}
        </div>
      )}
      {live.err && <p role="alert" className="px-1 text-[0.875rem] font-semibold text-danger">{live.err}</p>}
    </div>
  );
}

/** Interpret (speech-to-speech) and Talk live (3.8 or legacy Flash). Shown when a Gemini key is configured. */
export function GeminiLiveBar({ lang, onEmergency, onAnswer }: {
  lang: string;
  onEmergency: () => void;
  onAnswer: (payload: LiveAnswerPayload) => void;
}) {
  const live = useGeminiLive({ lang, onEmergency, onAnswer });
  if (!live.voices.length) return null;
  return (
    <div className="mb-1.5 space-y-1.5 px-1">
      <GeminiLiveButtons live={live} />
      <GeminiLiveStatus live={live} />
    </div>
  );
}
