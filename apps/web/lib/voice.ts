"use client";

import { api, ApiError, getProfile, getTokens, wsUrl } from "./api";
import { DEFAULT_SILENCE_CONFIG, SILENCE_PRESETS, type SilenceConfig } from "./orchestrator";

/**
 * Voice input, local-first:
 *  1. Record with MediaRecorder and send to the SushrutAI server (/stt → local faster-whisper, or the
 *     India-hosted/free APIs the user consented to).
 *  2. If the server has no speech engine (HTTP 501), fall back to the browser's Web Speech API
 *     (audio is processed by the browser vendor — the UI says so).
 */
/** `level()` = current input loudness 0..1 (for the live waveform); absent when the browser does the listening. */
export type Recorder = { stop: () => Promise<string>; level?: () => number };

function meter(stream: MediaStream): { level: () => number; close: () => void } {
  const Ctx = window.AudioContext || (window as any).webkitAudioContext;
  if (!Ctx) return { level: () => 0, close: () => {} };
  const ctx: AudioContext = new Ctx();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  return {
    level: () => {
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (const v of buf) sum += v * v;
      return Math.min(1, Math.sqrt(sum / buf.length) * 6); // speech RMS ~0.02–0.15 → 0.1–0.9
    },
    close: () => { ctx.close().catch(() => {}); },
  };
}

/** The language the server heard in the last recording (it detects among the app language, English and Hindi).
 *  The answer is written in it, so speaking Hindi to the English app gets a Hindi answer. */
export const heard: {
  lang: string | null;
  confidence: number;
  rawTranscript?: string;
  normalizedTranscript?: string;
  lowConfidence?: boolean;
  clarificationPrompt?: string | null;
  codeSwitched?: boolean;
  detectedLanguages?: string[];
  segments?: { start: number; end: number; text: string; language?: string }[];
  engine?: string;
  onPartial: ((text: string) => void) | null;
} = { lang: null, confidence: 0, onPartial: null };
export function setHeardLang(l: string | null) { heard.lang = l; heard.confidence = l ? 1 : 0; }
export function clearHeardMeta() {
  heard.lang = null; heard.confidence = 0; heard.rawTranscript = undefined;
  heard.normalizedTranscript = undefined; heard.lowConfidence = false;
  heard.clarificationPrompt = null; heard.codeSwitched = false;
  heard.detectedLanguages = undefined; heard.segments = undefined; heard.engine = undefined;
}

export function isValidTranscript(text: string): boolean {
  const clean = (text || "").replace(/[.,!?।॥:;…\-'"()\[\]\s]+/g, "").trim();
  if (!clean) return false;
  if (/^(uh+|um+|hmm+|ah+|er+)$/i.test(clean)) return false;
  return clean.length >= 1;
}

const _speakingListeners = new Set<(speaking: boolean) => void>();
let _speakingAbort: (() => void) | null = null;

export function subscribeSpeaking(cb: (speaking: boolean) => void): () => void {
  _speakingListeners.add(cb);
  return () => { _speakingListeners.delete(cb); };
}

function notifySpeaking(speaking: boolean) {
  for (const cb of _speakingListeners) {
    try { cb(speaking); } catch { /* ignore */ }
  }
}
/** Voice capabilities reported by /voice/engines. */
export type VoiceCapabilities = {
  stt_local?: { indic_conformer?: boolean; faster_whisper?: boolean };
  tts_local?: { available?: boolean };
  cloud_voices?: string[];
  gemini_stt?: boolean;
  gemini_tts?: boolean;
  live_voices?: string[];
};

let _capCache: VoiceCapabilities | null = null;
export async function getVoiceCapabilities(): Promise<VoiceCapabilities> {
  if (_capCache) return _capCache;
  try {
    _capCache = await api<VoiceCapabilities>("/voice/engines");
    return _capCache;
  } catch {
    return {};
  }
}

async function startGeminiRecording(lang: string): Promise<Recorder> {
  const { startGeminiLive } = await import("./geminiLive");
  let latest = "";
  let settled = false;
  let stopping = false;
  let finish: (s: string) => void = () => {};
  const done = new Promise<string>((res) => {
    finish = (s: string) => {
      if (settled) return;
      settled = true;
      res(s);
    };
  });
  const listenAs = spokenRoute() || voicePrefs().autoLang ? "auto" : lang;
  const handle = await startGeminiLive({
    mode: "transcribe", lang: listenAs,
    onEvent: (ev) => {
      if (ev.lang) setHeardLang(ev.lang.split("-")[0]);
      if ((ev.type === "interim" || ev.type === "final" || ev.type === "input_transcript") && ev.text) {
        latest = ev.text;
        heard.rawTranscript = latest;
        heard.normalizedTranscript = latest;
        heard.onPartial?.(latest);
      }
      if (ev.type === "final" && stopping) finish(latest.trim());
      if (ev.type === "error") finish(latest.trim());
    },
  });
  heard.engine = "Gemini 3.5 Transcribe Live";
  return {
    level: handle.level,
    stop: () => {
      stopping = true;
      handle.endAudio();
      const timer = setTimeout(() => finish(latest.trim()), 2800);
      return done.finally(() => { clearTimeout(timer); handle.stop(); });
    },
  };
}

async function startGeminiHandsFree(lang: string, opts: {
  onUtterance: (text: string, lang?: string, f?: VoiceFeatures) => void;
  onPartial?: (fragment: string, merged: string) => void;
  onBargeIn?: (spokenSoFar: string) => void;
  onState: (s: HandsFreeState) => void;
  onError: (e: unknown) => void;
}): Promise<HandsFree> {
  const { startGeminiLive } = await import("./geminiLive");
  opts.onState("listening");
  const listenAs = spokenRoute() || voicePrefs().autoLang ? "auto" : lang;
  let fragments: string[] = [];
  let activeLang = lang === "auto" || lang === "app" ? "en" : lang;
  let langLocked = lang !== "auto" && lang !== "app";
  let flushTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;

  const explicitSwitchLang = (txt: string): string | null => {
    const t = (txt || "").toLowerCase();
    if (/(amake\s+banglay|banglay\s+(?:bojhan|bolun|bolo)|in\s+bengali|bengali\s+please|বাংলায়\s*(?:বলুন|বোঝান))/.test(t)) return "bn";
    if (/(in\s+english|english\s+(?:e|te|mein)\s+(?:bolun|bataiye)|ইংরেজিতে)/.test(t)) return "en";
    if (/(hindi\s+(?:mein|me)\s+(?:bataiye|bolo)|in\s+hindi|हिंदी\s*में|हिन्दी\s*में)/.test(t)) return "hi";
    return null;
  };

  const flush = () => {
    if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
    const merged = fragments.join(" ").replace(/\s+/g, " ").trim();
    fragments = [];
    if (merged) {
      opts.onUtterance(merged, activeLang);
    }
    if (!stopped) opts.onState("listening");
  };

  const handle = await startGeminiLive({
    mode: "transcribe", lang: listenAs,
    onEvent: (ev) => {
      if (stopped || isSpeaking()) return;
      const heardLang = (ev.lang || activeLang).split("-")[0];
      if (ev.type === "interim" && ev.text) {
        if (flushTimer) { clearTimeout(flushTimer); flushTimer = null; }
        const merged = [...fragments, ev.text.trim()].filter(Boolean).join(" ").trim();
        opts.onPartial?.(ev.text, merged);
        opts.onState("speech");
      }
      if (ev.type === "final" && ev.text?.trim()) {
        const sw = explicitSwitchLang(ev.text);
        if (sw) {
          activeLang = sw;
          langLocked = true;
          setHeardLang(sw);
        } else if (!langLocked && ev.lang) {
          activeLang = heardLang;
          langLocked = true;
          setHeardLang(heardLang);
        }
        fragments.push(ev.text.trim());
        const merged = fragments.join(" ").replace(/\s+/g, " ").trim();
        opts.onPartial?.(ev.text.trim(), merged);
        opts.onState("waiting_for_continuation");
        if (flushTimer) clearTimeout(flushTimer);
        // Elderly-friendly 25s continuation grace window (explicit "finished speaking" flushes immediately)
        if (/(আমি\s*বলা\s*শেষ\s*করেছি|i'?m\s+finished\s+speaking|i\s+am\s+done\s+speaking)/i.test(ev.text)) {
          flush();
        } else {
          flushTimer = setTimeout(flush, 25000);
        }
      }
      if (ev.type === "error") {
        if (flushTimer) clearTimeout(flushTimer);
        opts.onError(new Error(ev.message || "Gemini Live stopped"));
      }
    },
  });
  const unsubSpeak = subscribeSpeaking((speaking) => {
    if (stopped) return;
    handle.setMuted(speaking);
    if (!speaking) opts.onState("listening");
  });
  if (isSpeaking()) handle.setMuted(true);
  return {
    finishNow: flush,
    stop: () => {
      stopped = true;
      unsubSpeak();
      if (flushTimer) clearTimeout(flushTimer);
      handle.stop();
      opts.onState("idle");
    },
  };
}

export async function startServerRecording(lang: string): Promise<Recorder> {
  if (spokenRoute() || voicePrefs().engine === "gemini_transcribe") {
    try { return await startGeminiRecording(lang); } catch { /* fall through to /stt or browser */ }
  }
  const caps = await getVoiceCapabilities();
  const hasLocalStt = !!(caps.stt_local?.indic_conformer || caps.stt_local?.faster_whisper);
  const chosenEngine = voicePrefs().engine ?? "local";
  const hasCloudEngine = chosenEngine !== "local" && (caps.cloud_voices?.includes(chosenEngine) || caps.gemini_stt);
  if (!hasLocalStt && !hasCloudEngine) {
    // If Gemini STT is available & consented, try /stt?engine=gemini; otherwise use browser recognition directly
    const c = await api<{ active: string[] }>("/consents").catch(() => ({ active: [] as string[] }));
    const geminiAllowed = caps.gemini_stt && (c.active.includes("cloud_llm_live") || c.active.includes("cloud_speech"));
    if (!geminiAllowed && browserRecognitionAvailable()) {
      return startBrowserRecognition(BCP[lang] ?? lang, (txt) => heard.onPartial?.(txt));
    }
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.start(250);
  const m = meter(stream);
  return {
    level: m.level,
    stop: () =>
      new Promise<string>((resolve, reject) => {
        rec.onstop = async () => {
          stream.getTracks().forEach((t) => t.stop());
          m.close();
          const fd = new FormData();
          fd.append("file", new Blob(chunks, { type: rec.mimeType || "audio/webm" }), "speech.webm");
          fd.append("lang", sttLang() === "auto" ? "auto" : lang);
          fd.append("engine", voicePrefs().engine ?? (caps.gemini_stt && !hasLocalStt ? "gemini" : "local"));
          fd.append("ui", lang);
          try {
            const r = await api<{
              text: string; lang?: string; confidence?: number;
              raw_transcript?: string; normalized_transcript?: string;
              low_confidence?: boolean; clarification_prompt?: string | null;
              code_switched?: boolean; detected_languages?: string[];
              segments?: { start: number; end: number; text: string; language?: string }[];
              engine?: string;
            }>("/stt", { method: "POST", body: fd });
            heard.lang = r.lang ?? null;
            heard.confidence = r.confidence ?? 0;
            heard.rawTranscript = r.raw_transcript ?? r.text;
            heard.normalizedTranscript = r.normalized_transcript ?? r.text;
            heard.lowConfidence = !!r.low_confidence;
            heard.clarificationPrompt = r.clarification_prompt ?? null;
            heard.codeSwitched = !!r.code_switched;
            heard.detectedLanguages = r.detected_languages;
            heard.segments = r.segments;
            heard.engine = r.engine;
            resolve(r.text);
          } catch (e) {
            reject(e);
          }
        };
        rec.stop();
      }),
  };
}

type SR = { lang: string; interimResults: boolean; continuous: boolean; start(): void; stop(): void;
            onresult: ((e: any) => void) | null; onerror: ((e: any) => void) | null; onend: (() => void) | null };

export function browserRecognitionAvailable(): boolean {
  return typeof window !== "undefined" && !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
}

export function startBrowserRecognition(bcp: string, onInterim: (t: string) => void): Recorder {
  const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
  const sr: SR = new Ctor();
  sr.lang = bcp;
  sr.interimResults = true;
  sr.continuous = true;
  let finalText = "";
  let resolveFn: (s: string) => void = () => {};
  sr.onresult = (e: any) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finalText += r[0].transcript + " ";
      else interim += r[0].transcript;
    }
    onInterim((finalText + interim).trim());
  };
  sr.onend = () => resolveFn(finalText.trim());
  sr.onerror = () => resolveFn(finalText.trim());
  sr.start();
  return { stop: () => new Promise((res) => { resolveFn = res; sr.stop(); }) };
}

export const isNoServerSpeech = (e: unknown) => e instanceof ApiError && e.status === 501;

// ---------------------------------------------------------------- hands-free (VAD + barge-in)
/**
 * Continuous listening with energy-based voice activity detection:
 * speech starts -> record; ~0.9 s silence -> stop, transcribe on the server, call onUtterance.
 * If the user starts talking while the assistant is speaking, playback stops (barge-in).
 */
export type HandsFree = { stop: () => void; finishNow?: () => void };

/** How an utterance sounded (measured on this device; nothing leaves it): speaking time, loudness, words. */
export type VoiceFeatures = { ms: number; rms: number; words: number };

export type HandsFreeState =
  | "idle"
  | "listening"
  | "speech"
  | "transcribing"
  | "pause_detected"
  | "waiting_for_continuation"
  | "session_paused";

/** Open a low-latency streaming WebSocket when tokens allow; resolves null if unavailable so HTTP /stt works seamlessly. */
export function openVoiceWebSocket(lang: string, onPartial: (res: any) => void): {
  sendChunk: (bytes: ArrayBuffer) => void;
  finalize: () => void;
  reset: () => void;
  close: () => void;
} | null {
  try {
    const t = getTokens();
    if (!t || typeof WebSocket === "undefined") return null;
    const prof = getProfile()?.id;
    const qs = new URLSearchParams({ token: t.access_token, lang: sttLang() === "auto" ? "auto" : lang, ui: lang });
    if (prof) qs.set("profile_id", prof);
    const ws = new WebSocket(wsUrl(`/voice/stream?${qs.toString()}`));
    let open = false;
    ws.onopen = () => {
      open = true;
      ws.send(JSON.stringify({ type: "config", format: "webm", lang: sttLang() === "auto" ? "auto" : lang, ui: lang }));
    };
    ws.onmessage = (e) => {
      try { onPartial(JSON.parse(e.data)); } catch { /* ignore */ }
    };
    return {
      sendChunk: (buf) => { if (open && ws.readyState === WebSocket.OPEN) ws.send(buf); },
      finalize: () => { if (open && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "finalize" })); },
      reset: () => { if (open && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "reset" })); },
      close: () => { try { ws.close(); } catch { /* ignore */ } },
    };
  } catch {
    return null;
  }
}

export async function startHandsFree(lang: string, opts: {
  onUtterance: (text: string, lang?: string, f?: VoiceFeatures) => void;
  onPartial?: (fragment: string, merged: string) => void;
  onBargeIn?: (spokenSoFar: string) => void;
  onGentlePrompt?: () => void;
  onState: (s: HandsFreeState) => void;
  onError: (e: unknown) => void;
  silence?: SilenceConfig;
}): Promise<HandsFree> {
  if (spokenRoute() || voicePrefs().engine === "gemini_transcribe") return startGeminiHandsFree(lang, opts);
  const caps = await getVoiceCapabilities();
  const hasLocalStt = !!(caps.stt_local?.indic_conformer || caps.stt_local?.faster_whisper);
  const chosenEngine = voicePrefs().engine ?? "local";
  const hasCloudEngine = chosenEngine !== "local" && (caps.cloud_voices?.includes(chosenEngine) || caps.gemini_stt);
  let effectiveEngine: string = chosenEngine;
  if (!hasLocalStt && !hasCloudEngine) {
    const c = await api<{ active: string[] }>("/consents").catch(() => ({ active: [] as string[] }));
    const geminiAllowed = !!caps.gemini_stt && (c.active.includes("cloud_llm_live") || c.active.includes("cloud_speech"));
    if (geminiAllowed) {
      return startGeminiHandsFree(lang, opts);
    }
    if (browserRecognitionAvailable()) {
      // Continuous browser SpeechRecognition fallback when no local or consented cloud STT exists
      const Ctor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      const sr: SR = new Ctor();
      sr.lang = BCP[lang] ?? lang;
      sr.interimResults = true;
      sr.continuous = true;
      let stopped = false;
      opts.onState("listening");
      sr.onresult = (e: any) => {
        if (stopped) return;
        let interim = "";
        let finalSeg = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i];
          if (r.isFinal) finalSeg += r[0].transcript + " ";
          else interim += r[0].transcript;
        }
        if (isSpeaking() && (interim || finalSeg).trim()) {
          const spokenBefore = lastSpoken?.text ?? "";
          stopSpeaking();
          opts.onBargeIn?.(spokenBefore);
        }
        if (interim.trim()) opts.onPartial?.(interim.trim(), interim.trim());
        const clean = finalSeg.trim();
        if (clean) opts.onUtterance(clean, lang);
      };
      sr.onerror = (e: any) => {
        if (!stopped && e?.error !== "no-speech" && e?.error !== "aborted") opts.onError(e);
      };
      sr.onend = () => {
        if (!stopped) { try { sr.start(); } catch { /* ignore */ } }
      };
      sr.start();
      return {
        stop: () => {
          stopped = true;
          try { sr.stop(); } catch { /* ignore */ }
          opts.onState("idle");
        },
      };
    }
  }
  const cfg = opts.silence ?? getSilenceConfig();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const ctx = new AudioContext();
  const wake = () => { if (ctx.state === "suspended") ctx.resume().catch(() => {}); };
  wake();
  window.addEventListener("pointerdown", wake, { once: true });
  window.addEventListener("keydown", wake, { once: true });
  const src = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  src.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";

  let noise = 0.01, rec: MediaRecorder | null = null, chunks: Blob[] = [];
  let speechMs = 0, silenceMs = 0, stopped = false, last = performance.now();
  let voicedMs = 0, rmsSum = 0, rmsN = 0;
  let openFragments: string[] = [];
  let waitingSince: number | null = null;
  let gentleSent = false;
  let speakingStartedAt = 0;
  let wasSpeaking = false;
  let activeLang: string | undefined = undefined;
  const wsClient = openVoiceWebSocket(lang, (res) => {
    if (res?.is_partial && res?.text?.trim()) {
      const merged = [...openFragments, res.text.trim()].join(" ").trim();
      opts.onPartial?.(res.text.trim(), merged);
    }
  });

  const flushTurn = () => {
    const merged = openFragments.join(" ").replace(/\s+/g, " ").trim();
    const f: VoiceFeatures = { ms: voicedMs, rms: rmsN ? rmsSum / rmsN : 0, words: merged ? merged.split(/\s+/).length : 0 };
    openFragments = [];
    waitingSince = null;
    gentleSent = false;
    voicedMs = 0; rmsSum = 0; rmsN = 0;
    if (merged) opts.onUtterance(merged, activeLang, f);
    opts.onState("listening");
  };

  const finishFragment = () => {
    const r = rec;
    rec = null;
    if (!r) return;
    r.onstop = async () => {
      const blob = new Blob(chunks, { type: r.mimeType || "audio/webm" });
      chunks = [];
      if (blob.size < 3500) {
        if (!openFragments.length) opts.onState("listening");
        return;
      }
      opts.onState("transcribing");
      const fd = new FormData();
      fd.append("file", blob, "speech.webm");
      fd.append("lang", sttLang() === "auto" ? "auto" : lang);
      fd.append("engine", effectiveEngine === "local" && caps.gemini_stt && !hasLocalStt ? "gemini" : effectiveEngine);
      fd.append("ui", lang);
      try {
        const res = await api<{
          text: string; lang?: string; confidence?: number;
          raw_transcript?: string; normalized_transcript?: string;
          low_confidence?: boolean; clarification_prompt?: string | null;
          code_switched?: boolean; detected_languages?: string[];
          segments?: { start: number; end: number; text: string; language?: string }[];
          engine?: string;
        }>("/stt", { method: "POST", body: fd });
        heard.lang = res.lang ?? null;
        heard.confidence = res.confidence ?? 0;
        heard.rawTranscript = res.raw_transcript ?? res.text;
        heard.normalizedTranscript = res.normalized_transcript ?? res.text;
        heard.lowConfidence = !!res.low_confidence;
        heard.clarificationPrompt = res.clarification_prompt ?? null;
        heard.codeSwitched = !!res.code_switched;
        heard.detectedLanguages = res.detected_languages;
        heard.segments = res.segments;
        heard.engine = res.engine;
        activeLang = res.lang;
        const piece = (res.normalized_transcript || res.text || "").trim();
        if (piece) {
          openFragments.push(piece);
          const merged = openFragments.join(" ").replace(/\s+/g, " ").trim();
          opts.onPartial?.(piece, merged);
          waitingSince = performance.now();
          opts.onState("waiting_for_continuation");
          return;
        }
      } catch (e) { opts.onError(e); }
      opts.onState(openFragments.length ? "waiting_for_continuation" : "listening");
    };
    r.stop();
  };

  const tick = () => {
    if (stopped) return;
    const now = performance.now(), dt = now - last;
    last = now;
    const ttsActive = isSpeaking();
    if (ttsActive && !wasSpeaking) speakingStartedAt = now;
    wasSpeaking = ttsActive;

    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    const rms = Math.sqrt(sum / buf.length);

    // Acoustic playback-aware barge-in gate: ignore initial 200ms TTS speaker onset and require stronger energy while AI speaks
    const gateThresh = ttsActive ? Math.max(0.045, noise * 4.5) : Math.max(0.02, noise * 3);
    const loud = (!ttsActive || (now - speakingStartedAt) > 200) && rms > gateThresh;
    if (!loud && !ttsActive) noise = noise * 0.98 + rms * 0.02;

    if (loud) {
      speechMs += dt; silenceMs = 0;
      waitingSince = null;
      gentleSent = false;
      if (rec) { voicedMs += dt; rmsSum += rms; rmsN++; }
      if (speechMs > 150 && ttsActive) {
        const spokenBefore = lastSpoken?.text ?? "";
        stopSpeaking();
        opts.onBargeIn?.(spokenBefore);
      }
      if (!rec && speechMs > 180) {
        chunks = [];
        rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        rec.ondataavailable = (e) => {
          if (e.data.size) {
            chunks.push(e.data);
            e.data.arrayBuffer().then((ab) => wsClient?.sendChunk(ab)).catch(() => {});
          }
        };
        rec.start(250);
        opts.onState("speech");
      }
    } else {
      silenceMs += dt;
      if (silenceMs > 300) speechMs = 0;
      if (rec && silenceMs >= cfg.pauseDetectMs) {
        opts.onState("pause_detected");
        finishFragment();
      }
      if (!rec && openFragments.length > 0 && waitingSince !== null) {
        const waited = now - waitingSince;
        if (waited >= cfg.gentleAckMs && !gentleSent) {
          gentleSent = true;
          opts.onGentlePrompt?.();
        }
        if (waited >= cfg.continuationGraceMs) {
          flushTurn();
        }
      } else if (!rec && openFragments.length === 0 && silenceMs >= cfg.sessionPauseMs) {
        opts.onState("session_paused");
      }
    }
    requestAnimationFrame(tick);
  };
  opts.onState("listening");
  requestAnimationFrame(tick);
  return {
    finishNow: flushTurn,
    stop: () => {
      stopped = true;
      if (rec) rec.stop();
      wsClient?.close();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
    },
  };
}

// ---------------------------------------------------------------- TTS
let audio: HTMLAudioElement | null = null;

/** Read the page's main content: [data-speak] regions if the page marks them, else headings, paragraphs and list
 *  items of <main> (not navigation, buttons, forms or anything marked data-speak-skip). */
export function readPage(lang: string) {
  const root = document.querySelector("main") ?? document.body;
  const marked = [...root.querySelectorAll<HTMLElement>("[data-speak]")];
  const els = marked.length ? marked : [...root.querySelectorAll<HTMLElement>("h1, h2, h3, p, li, td, th")];
  const parts: string[] = [];
  for (const el of els) {
    if (el.closest("nav, button, form, [data-speak-skip], [aria-hidden='true'], .sr-only") || !el.offsetParent) continue;
    const s = el.innerText.replace(/\s+/g, " ").trim();
    if (s && !parts.includes(s)) parts.push(s);
    if (parts.join(". ").length > 1300) break;
  }
  if (parts.length) speak(parts.join(". "), lang);
}

export function isSpeaking(): boolean {
  return (!!audio && !audio.paused) || (typeof speechSynthesis !== "undefined" && speechSynthesis.speaking && !speechSynthesis.paused);
}

export function isSpeechPaused(): boolean {
  return (!!audio && audio.paused && audio.currentTime > 0 && !audio.ended) ||
    (typeof speechSynthesis !== "undefined" && speechSynthesis.paused);
}

export function pauseSpeaking() {
  if (audio && !audio.paused) audio.pause();
  if (typeof speechSynthesis !== "undefined" && speechSynthesis.speaking && !speechSynthesis.paused) {
    speechSynthesis.pause();
  }
}

export function resumeSpeaking() {
  if (audio && audio.paused && !audio.ended) audio.play().catch(() => {});
  if (typeof speechSynthesis !== "undefined" && speechSynthesis.paused) {
    speechSynthesis.resume();
  }
}

let _segmentEpoch = 0;
let _activeSection: string | null = null;
const _sectionListeners = new Set<(sectionId: string | null) => void>();
function notifyActiveSection(id: string | null) {
  _activeSection = id;
  for (const fn of _sectionListeners) fn(id);
}
export function subscribeActiveSection(fn: (sectionId: string | null) => void): () => void {
  _sectionListeners.add(fn);
  fn(_activeSection);
  return () => { _sectionListeners.delete(fn); };
}

export function stopSpeaking() {
  _segmentEpoch += 1;
  notifyActiveSection(null);
  if (audio) {
    audio.pause();
    audio.currentTime = 0;
  }
  audio = null;
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
  _speakingAbort?.();
  _speakingAbort = null;
  notifySpeaking(false);
}

let lastSpoken: { text: string; lang: string; bcp: string } | null = null;

/** Replay the last thing read aloud (the "repeat / phir se" voice command). */
export function repeatLast(): boolean {
  if (!lastSpoken) return false;
  speak(lastSpoken.text, lastSpoken.lang, lastSpoken.bcp);
  return true;
}

/** Speech speed from Settings (Display prefs, stored per device). */
function speechRate(): number {
  try {
    const prefs = JSON.parse(localStorage.getItem("sushrut.prefs") || "{}");
    const base = prefs.rate === "slow" ? 0.8 : prefs.rate === "fast" ? 1.2 : 1;
    const preset = voicePrefs().silencePreset;
    return preset === "elderly" ? Math.min(base, 0.86) : base;
  } catch { return 1; }
}

/** Script of a text -> app language (a Hindi answer in the English app is read with a Hindi voice). */
const SCRIPTS: [RegExp, string][] = [[/[\u0900-\u097F]/, "hi"], [/[\u0980-\u09FF]/, "bn"], [/[\u0B80-\u0BFF]/, "ta"],
  [/[\u0C00-\u0C7F]/, "te"], [/[\u0A80-\u0AFF]/, "gu"], [/[\u0C80-\u0CFF]/, "kn"], [/[\u0D00-\u0D7F]/, "ml"], [/[\u0B00-\u0B7F]/, "or"],
  [/[\u0A00-\u0A7F]/, "pa"], [/[\u0600-\u06FF]/, "ur"]];
export function langOfText(text: string, fallback: string): string {
  const letters = text.replace(/[^\p{L}]/gu, "");
  for (const [re, l] of SCRIPTS) {
    const n = (letters.match(new RegExp(re.source, "g")) ?? []).length;
    if (n > letters.length * 0.3) {
      if (l === "hi" && fallback === "mr") return "mr";
      // Bengali script is shared: Assamese has its own ৰ and ৱ, or the person's app language says so
      if (l === "bn" && (/[\u09F0\u09F1]/.test(letters) || fallback === "as")) return "as";
      return l;
    }
  }
  return /[A-Za-z]/.test(letters) && !SCRIPTS.some(([re]) => re.test(letters)) ? "en" : fallback;
}
const BCP: Record<string, string> = { en: "en-IN", hi: "hi-IN", bn: "bn-IN", ta: "ta-IN", te: "te-IN", mr: "mr-IN",
  gu: "gu-IN", kn: "kn-IN", ml: "ml-IN", or: "or-IN", pa: "pa-IN", as: "as-IN", ur: "ur-IN" };

/** Device voices (on-device text-to-speech). Loaded asynchronously by most browsers. */
export function deviceVoices(): Promise<SpeechSynthesisVoice[]> {
  if (typeof speechSynthesis === "undefined") return Promise.resolve([]);
  const now = speechSynthesis.getVoices();
  if (now.length) return Promise.resolve(now);
  return new Promise((res) => {
    const done = () => res(speechSynthesis.getVoices());
    speechSynthesis.addEventListener("voiceschanged", done, { once: true });
    setTimeout(done, 1200);
  });
}

type VoicePrefs = {
  voices?: Record<string, string>; pitch?: number;
  autoLang?: boolean;   // detect the spoken language each time (off: the app language is used, faster and lighter)
  handsFree?: boolean;  // keep listening between answers on every page (on by default)
  engine?: "local" | "sarvam" | "elevenlabs" | "gemini_transcribe";
  talk?: "off" | "gemini_talk" | "gemini_flash";
  spoken?: boolean;  // mic → Transcribe Live → English answer → speak back in the heard language
  playback?: "device" | "gemini_tts";
  voiceCare?: boolean;  // opt-in: learn the usual speaking pace / energy on this device and notice tiredness  // listening / read-aloud engine (cloud ones need the cloud_voice consent)
  silencePreset?: "patient" | "elderly" | "standard";
};
export function voicePrefs(): VoicePrefs {
  try { return JSON.parse(localStorage.getItem("sushrut.voice") || "{}"); } catch { return {}; }
}
export function getSilenceConfig(): SilenceConfig {
  const preset = voicePrefs().silencePreset ?? "patient";
  return SILENCE_PRESETS[preset] ?? DEFAULT_SILENCE_CONFIG;
}
/** Spoken questions use Transcribe Live, the Settings answer model in English, then playback in the heard language. */
export function spokenRoute(): boolean {
  return voicePrefs().spoken === true;
}
export function setVoicePrefs(p: VoicePrefs) {
  try { localStorage.setItem("sushrut.voice", JSON.stringify({ ...voicePrefs(), ...p })); } catch { /* ignore */ }
}
/** What the server should listen for: the app language, or "auto" when language detection is switched on. */
export function sttLang(): string {
  return voicePrefs().autoLang ? "auto" : "app";
}

/** Best device voice for a language: the person's choice, else Indian English / the language's Indian voice,
 *  else any voice of that language (English: anything but en-GB when another exists). */
const _sessionVoiceCache = new Map<string, string>();

export function pickVoice(voices: SpeechSynthesisVoice[], lang: string): SpeechSynthesisVoice | null {
  const chosen = voicePrefs().voices?.[lang] ?? _sessionVoiceCache.get(lang);
  const byUri = chosen && voices.find((v) => v.voiceURI === chosen);
  if (byUri) return byUri;
  const bcp = BCP[lang] ?? lang;
  const base = bcp.slice(0, 2).toLowerCase();
  const same = voices.filter((v) => v.lang.replace("_", "-").toLowerCase().startsWith(base));
  const exact = same.filter((v) => v.lang.replace("_", "-").toLowerCase() === bcp.toLowerCase());
  const local = (l: SpeechSynthesisVoice[]) => l.find((v) => v.localService) ?? l[0];
  let picked: SpeechSynthesisVoice | null = null;
  if (exact.length) picked = local(exact) ?? null;
  else if (lang === "en") {
    const notGb = same.filter((v) => !/en[-_]gb/i.test(v.lang));
    picked = local(notGb.length ? notGb : same) ?? null;
  } else {
    picked = local(same) ?? null;
  }
  if (picked) _sessionVoiceCache.set(lang, picked.voiceURI);
  return picked;
}

export type SpeakOpts = { confidence?: "high" | "medium" | "low" };
/** Confidence in the voice: a steady pace for a confident answer; slightly slower and softer when unsure (the
 *  written "not a diagnosis" line always stays). */
function confidenceTone(o?: SpeakOpts): { rate: number; pitch: number } {
  return o?.confidence === "low" ? { rate: 0.9, pitch: 0.95 } : o?.confidence === "medium" ? { rate: 0.96, pitch: 0.98 } : { rate: 1, pitch: 1 };
}

async function playAudioAndWait(el: HTMLAudioElement): Promise<void> {
  notifySpeaking(true);
  await new Promise<void>((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      _speakingAbort = null;
      notifySpeaking(false);
      resolve();
    };
    _speakingAbort = finish;
    el.onended = finish;
    el.onerror = finish;
    el.play().catch(finish);
  });
}

async function _speakOne(clean: string, l: string, opts?: SpeakOpts) {
  const tone = confidenceTone(opts);
  const prefs = voicePrefs();
  const playback = prefs.playback ?? (spokenRoute() ? "gemini_tts" : "device");
  if (playback === "gemini_tts") {
    try {
      const r = await api<{ audio_base64: string; format?: string }>("/tts", { method: "POST", body: JSON.stringify({ text: clean, lang: l, engine: "gemini" }) });
      audio = new Audio(`data:audio/${r.format === "mp3" ? "mpeg" : "wav"};base64,${r.audio_base64}`);
      audio.playbackRate = speechRate() * tone.rate;
      (audio as any).preservesPitch = true;
      await playAudioAndWait(audio);
      return;
    } catch { /* the device voice speaks instead */ }
  }
  const engine = prefs.engine ?? "local";
  if (engine === "sarvam" || engine === "elevenlabs") {
    try {
      const r = await api<{ audio_base64: string; format?: string }>("/tts", { method: "POST", body: JSON.stringify({ text: clean, lang: l, engine }) });
      audio = new Audio(`data:audio/${r.format === "mp3" ? "mpeg" : "wav"};base64,${r.audio_base64}`);
      audio.playbackRate = speechRate() * tone.rate;
      (audio as any).preservesPitch = true;
      await playAudioAndWait(audio);
      return;
    } catch { /* fall through to the device voice */ }
  }
  // 1. the device's own voice (on-device, instant, no data leaves the phone)
  const voice = pickVoice(await deviceVoices(), l);
  if (voice) {
    notifySpeaking(true);
    await new Promise<void>((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        _speakingAbort = null;
        notifySpeaking(false);
        resolve();
      };
      _speakingAbort = finish;
      const u = new SpeechSynthesisUtterance(clean);
      u.voice = voice;
      u.lang = voice.lang;
      u.rate = 0.95 * speechRate() * tone.rate;
      u.pitch = (voicePrefs().pitch ?? 1) * tone.pitch;
      u.onend = finish;
      u.onerror = finish;
      // Safety timeout if browser speechSynthesis never fires onend
      setTimeout(finish, Math.max(8000, clean.length * 140));
      speechSynthesis.speak(u);
    });
    return;
  }
  // 2. no device voice for this Indian language: local server MMS or Gemini TTS (English is never sent to MMS)
  const caps = await getVoiceCapabilities();
  if (l !== "en" || caps.gemini_tts) {
    const useEngine = l === "en" ? "gemini" : (caps.tts_local?.available ? undefined : (caps.gemini_tts ? "gemini" : undefined));
    if (l === "en" && !useEngine) return;
    try {
      const r = await api<{ audio_base64: string; format?: string }>("/tts", {
        method: "POST",
        body: JSON.stringify(useEngine ? { text: clean, lang: l, engine: useEngine } : { text: clean, lang: l }),
      });
      audio = new Audio(`data:audio/${r.format === "mp3" ? "mpeg" : "wav"};base64,${r.audio_base64}`);
      audio.playbackRate = speechRate() * tone.rate;
      (audio as any).preservesPitch = true;
      await playAudioAndWait(audio);
    } catch { notifySpeaking(false); }
  }
}

export async function speak(text: string, lang: string, _bcp?: string, opts?: SpeakOpts) {
  stopSpeaking();
  const clean = text.replace(/[*_#>`]/g, "").slice(0, 1400);
  if (!clean.trim()) return;
  // Respect locked conversation language; only auto-detect from text if lang is auto/app or empty.
  const l = !lang || lang === "auto" || lang === "app" ? langOfText(clean, "en") : lang.split("-")[0];
  lastSpoken = { text, lang: l, bcp: BCP[l] ?? l };
  await _speakOne(clean, l, opts);
}

export type SpokenSegment = { section_id: string; text: string };

/** Speak synchronized segments sequentially and notify listeners which screen section is currently spoken. */
export async function speakSegments(
  segments: SpokenSegment[],
  lang: string,
  _bcp?: string,
  opts?: SpeakOpts & { onSectionChange?: (sectionId: string | null) => void },
) {
  stopSpeaking();
  const epoch = _segmentEpoch;
  const cleanSegs = segments
    .map((s) => ({ section_id: s.section_id, text: (s.text || "").replace(/[*_#>`]/g, "").trim() }))
    .filter((s) => s.text.length > 0);
  if (!cleanSegs.length) return;
  const joined = cleanSegs.map((s) => s.text).join(" ");
  const l = !lang || lang === "auto" || lang === "app" ? langOfText(joined, "en") : lang.split("-")[0];
  lastSpoken = { text: joined, lang: l, bcp: BCP[l] ?? l };
  for (let i = 0; i < cleanSegs.length; i++) {
    const seg = cleanSegs[i];
    if (_segmentEpoch !== epoch) break;
    notifyActiveSection(seg.section_id);
    opts?.onSectionChange?.(seg.section_id);
    await _speakOne(seg.text.slice(0, 1200), l, opts);
    if (i < cleanSegs.length - 1 && _segmentEpoch === epoch) {
      await new Promise((r) => setTimeout(r, voicePrefs().silencePreset === "elderly" ? 280 : 140));
    }
  }
  if (_segmentEpoch === epoch) {
    notifyActiveSection(null);
    opts?.onSectionChange?.(null);
  }
}
