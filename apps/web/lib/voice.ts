"use client";

import { API, api, ApiError, getProfile, getTokens, reportServiceFailure } from "./api";
import { DEFAULT_SILENCE_CONFIG, type SilenceConfig } from "./orchestrator";

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

async function startGeminiRecording(lang: string): Promise<Recorder> {
  const { startGeminiLive } = await import("./geminiLive");
  let latest = "";
  let finish: (s: string) => void = () => {};
  const done = new Promise<string>((res) => { finish = res; });
  const listenAs = lang && lang !== "auto" && lang !== "app" ? lang : (spokenRoute() ? "auto" : lang);
  const handle = await startGeminiLive({
    mode: "transcribe", lang: listenAs,
    onEvent: (ev) => {
      if (ev.lang && (!lang || lang === "auto" || lang === "app")) setHeardLang(ev.lang.split("-")[0]);
      if ((ev.type === "interim" || ev.type === "final") && ev.text) {
        latest = ev.text;
        heard.onPartial?.(ev.text);
      }
      if (ev.type === "final" && stopping) finish(latest.trim());
      if (ev.type === "error") finish(latest.trim());
    },
  });
  let stopping = false;
  heard.engine = "Gemini 3.5 Transcribe Live";
  return {
    level: handle.level,
    stop: () => {
      stopping = true;
      handle.endAudio();
      const timer = setTimeout(() => finish(latest.trim()), 2500);
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
  const specificLang = !!lang && lang !== "auto" && lang !== "app";
  const listenAs = specificLang ? lang : (spokenRoute() ? "auto" : lang);
  const handle = await startGeminiLive({
    mode: "transcribe", lang: listenAs,
    onEvent: (ev) => {
      const heardLang = specificLang ? lang : (ev.lang || lang).split("-")[0];
      if (ev.type === "interim" && ev.text) {
        if (isSpeaking()) { stopSpeaking(); opts.onBargeIn?.(lastSpoken?.text ?? ""); }
        opts.onPartial?.(ev.text, ev.text);
        opts.onState("speech");
      }
      if (ev.type === "final" && ev.text?.trim()) {
        if (ev.lang) setHeardLang(heardLang);
        opts.onUtterance(ev.text.trim(), heardLang);
        opts.onState("listening");
      }
      if (ev.type === "error") opts.onError(new Error(ev.message || "Gemini Live stopped"));
    },
  });
  return { stop: () => { handle.stop(); opts.onState("idle"); } };
}

export async function startServerRecording(lang: string): Promise<Recorder> {
  if (spokenRoute() || voicePrefs().engine === "gemini_transcribe") {
    try { return await startGeminiRecording(lang); } catch { /* local recording still works */ }
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
          fd.append("engine", voicePrefs().engine ?? "local");
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
export type HandsFree = { stop: () => void };

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
    const wsBase = API.replace(/^http/, "ws");
    const prof = getProfile()?.id;
    const qs = new URLSearchParams({ token: t.access_token, lang: sttLang() === "auto" ? "auto" : lang, ui: lang });
    if (prof) qs.set("profile_id", prof);
    const ws = new WebSocket(`${wsBase}/voice/stream?${qs.toString()}`);
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
  const cfg = opts.silence ?? DEFAULT_SILENCE_CONFIG;
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
      fd.append("engine", voicePrefs().engine ?? "local");
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

export const SPEECH_STATE_EVENT = "sushrut:speech";
export type SpeechStatus = {
  speaking: boolean;
  lang?: string;
  gender?: "female" | "male";
  engine?: string;
  caption?: string;
  progress?: number;
};

export type AudioCueKind =
  | "listen_start"
  | "listen_stop"
  | "speak_start"
  | "send"
  | "receive"
  | "thinking_pause"
  | "gentle_prompt"
  | "barge_in_ack"
  | "emergency"
  | "error";

let cueCtx: AudioContext | null = null;
/** Gentle synthesized Web Audio cues for accessibility & Elderly Mode speaking states. */
export function playAudioCue(kind: AudioCueKind, opts?: { elderly?: boolean }) {
  if (typeof window === "undefined") return;
  if (voicePrefs().audioCues === false) return;
  try {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    if (!Ctx) return;
    cueCtx = cueCtx ?? new Ctx();
    if (cueCtx.state === "suspended") cueCtx.resume().catch(() => {});
    const now = cueCtx.currentTime;
    const elderly = opts?.elderly ?? isElderlySpeechMode();
    const pitchScale = elderly ? 0.78 : 1.0;
    const durScale = elderly ? 1.25 : 1.0;
    const attack = elderly ? 0.03 : 0.015;
    const tone = (freq: number, offset: number, dur: number, vol = 0.055) => {
      if (!cueCtx) return;
      const start = now + offset * durScale;
      const d = dur * durScale;
      const osc = cueCtx.createOscillator();
      const gain = cueCtx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq * pitchScale, start);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(vol, start + Math.min(attack, d * 0.45));
      gain.gain.exponentialRampToValueAtTime(0.0001, start + d);
      osc.connect(gain);
      gain.connect(cueCtx.destination);
      osc.start(start);
      osc.stop(start + d + 0.015);
    };
    if (kind === "listen_start") { tone(523.25, 0, 0.075); tone(659.25, 0.065, 0.095); }
    else if (kind === "listen_stop") { tone(587.33, 0, 0.065); tone(440.0, 0.055, 0.085); }
    else if (kind === "speak_start") { tone(698.46, 0, 0.08, 0.04); }
    else if (kind === "send") { tone(587.33, 0, 0.055, 0.035); tone(783.99, 0.05, 0.075, 0.035); }
    else if (kind === "receive") { tone(659.25, 0, 0.06, 0.032); tone(880.0, 0.055, 0.09, 0.03); }
    else if (kind === "thinking_pause") { tone(440.0, 0, 0.14, 0.028); }
    else if (kind === "gentle_prompt") { tone(440.0, 0, 0.09, 0.04); tone(523.25, 0.09, 0.12, 0.04); }
    else if (kind === "barge_in_ack") { tone(392.0, 0, 0.06, 0.042); tone(493.88, 0.055, 0.08, 0.04); }
    else if (kind === "emergency") {
      tone(587.33, 0, 0.1, 0.07); tone(783.99, 0.11, 0.12, 0.07);
      tone(587.33, 0.28, 0.1, 0.07); tone(783.99, 0.39, 0.14, 0.07);
    }
    else if (kind === "error") { tone(329.63, 0, 0.08, 0.05); tone(261.63, 0.09, 0.1, 0.05); }
  } catch { /* audio cues are best-effort */ }
}

/** Clean user/voice text before sending: collapse extra spaces/blank lines while keeping Indic punctuation intact. */
export function normalizeChatText(raw: string): string {
  return raw
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/ *\n */g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function splitCaptionSentences(text: string): { text: string; start: number; end: number }[] {
  const out: { text: string; start: number; end: number }[] = [];
  const re = /[^.!?।\n]+(?:[.!?।\n]+|$)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const s = m[0].trim();
    if (s) out.push({ text: s, start: m.index, end: m.index + m[0].length });
  }
  return out.length ? out : [{ text: text.trim(), start: 0, end: text.length }];
}

function emitSpeechState(detail: SpeechStatus) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent<SpeechStatus>(SPEECH_STATE_EVENT, { detail }));
  }
}

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
  return (!!audio && !audio.paused) || (typeof speechSynthesis !== "undefined" && speechSynthesis.speaking);
}

export function stopSpeaking() {
  audio?.pause();
  audio = null;
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
  emitSpeechState({ speaking: false });
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
    const r = JSON.parse(localStorage.getItem("sushrut.prefs") || "{}").rate;
    return r === "slow" ? 0.8 : r === "fast" ? 1.2 : 1;
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
  gender?: "female" | "male";
  localTts?: "auto" | "kokoro" | "parler" | "mms";
  audioCues?: boolean;  // gentle audio chimes on listen start/stop & playback (default on)
  captions?: boolean;   // live closed captions while speaking aloud (default on)
  autoLang?: boolean;   // detect the spoken language each time (off: the app language is used, faster and lighter)
  handsFree?: boolean;  // keep listening between answers on every page (on by default)
  engine?: "local" | "sarvam" | "elevenlabs" | "gemini_transcribe";
  talk?: "off" | "gemini_talk" | "gemini_flash";
  spoken?: boolean;  // mic → Transcribe Live → English answer → speak back in the heard language
  playback?: "device" | "gemini_tts";
  voiceCare?: boolean;  // opt-in: learn the usual speaking pace / energy on this device and notice tiredness  // listening / read-aloud engine (cloud ones need the cloud_voice consent)
};
export function voicePrefs(): VoicePrefs {
  try { return JSON.parse(localStorage.getItem("sushrut.voice") || "{}"); } catch { return {}; }
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

const _FEMALE_VOICE_RE = /\b(female|woman|zira|aria|jenny|neerja|heera|kalpana|swara|aditi|raveena|samantha|victoria|karen|veena|lekha|susan|hazel|libby|sonia|natasha|tessa|moira|kanya|google us english)\b/i;
const _MALE_VOICE_RE = /\b(male|man|david|mark|george|ravi|prabhat|hemant|madhur|daniel|alex|ryan|guy|oliver|thomas|rishi|aaron|james)\b/i;

/** Best device voice for a language: the person's choice, else Indian English / the language's Indian voice
 *  matching the preferred gender (female/male), else any voice of that language. */
export function pickVoice(voices: SpeechSynthesisVoice[], lang: string, gender: "female" | "male" = voicePrefs().gender ?? "female"): SpeechSynthesisVoice | null {
  const chosen = voicePrefs().voices?.[lang];
  const byUri = chosen && voices.find((v) => v.voiceURI === chosen);
  if (byUri) return byUri;
  const bcp = BCP[lang] ?? lang;
  const base = bcp.slice(0, 2).toLowerCase();
  const same = voices.filter((v) => v.lang.replace("_", "-").toLowerCase().startsWith(base));
  const exact = same.filter((v) => v.lang.replace("_", "-").toLowerCase() === bcp.toLowerCase());
  const genderRe = gender === "male" ? _MALE_VOICE_RE : _FEMALE_VOICE_RE;
  const oppRe = gender === "male" ? _FEMALE_VOICE_RE : _MALE_VOICE_RE;
  const local = (l: SpeechSynthesisVoice[]) =>
    l.find((v) => v.localService && genderRe.test(v.name))
    ?? l.find((v) => genderRe.test(v.name))
    ?? l.find((v) => v.localService && !oppRe.test(v.name))
    ?? l.find((v) => !oppRe.test(v.name))
    ?? l.find((v) => v.localService)
    ?? l[0];
  if (exact.length) return local(exact);
  if (lang === "en") {
    const notGb = same.filter((v) => !/en[-_]gb/i.test(v.lang));
    return local(notGb.length ? notGb : same) ?? null;
  }
  return local(same) ?? null;
}

async function playAudioPayload(r: { audio_base64: string; format?: string; engine?: string }, lang: string, gender: "female" | "male", rate: number, sourceText = "") {
  const sentences = splitCaptionSentences(sourceText);
  audio = new Audio(`data:audio/${r.format === "mp3" ? "mpeg" : "wav"};base64,${r.audio_base64}`);
  audio.playbackRate = rate;
  (audio as any).preservesPitch = true;
  audio.onplay = () => {
    playAudioCue("speak_start");
    emitSpeechState({ speaking: true, lang, gender, engine: r.engine, caption: sentences[0]?.text, progress: 0 });
  };
  audio.ontimeupdate = () => {
    if (!audio || !audio.duration || !Number.isFinite(audio.duration)) return;
    const progress = Math.min(1, Math.max(0, audio.currentTime / audio.duration));
    const idx = Math.min(sentences.length - 1, Math.floor(progress * sentences.length));
    emitSpeechState({ speaking: true, lang, gender, engine: r.engine, caption: sentences[idx]?.text, progress });
  };
  audio.onended = () => emitSpeechState({ speaking: false });
  audio.onerror = () => {
    playAudioCue("error");
    emitSpeechState({ speaking: false });
    reportServiceFailure({ service: "Speech Playback", reason: "Audio playback failed", hint: "Try another voice engine in Settings." });
  };
  try {
    await audio.play();
  } catch (e: any) {
    playAudioCue("error");
    emitSpeechState({ speaking: false });
    reportServiceFailure({ service: "Speech Playback", reason: e?.message || "Browser blocked audio playback" });
  }
}

export type SpeakOpts = { confidence?: "high" | "medium" | "low" };
/** Confidence in the voice: a steady pace for a confident answer; slightly slower and softer when unsure (the
 *  written "not a diagnosis" line always stays). */
function isElderlySpeechMode(): boolean {
  try { return typeof window !== "undefined" && window.localStorage.getItem("sushrut.speechMode") === "elderly"; }
  catch { return false; }
}

function confidenceTone(o?: SpeakOpts): { rate: number; pitch: number } {
  const base = o?.confidence === "low" ? { rate: 0.9, pitch: 0.95 } : o?.confidence === "medium" ? { rate: 0.96, pitch: 0.98 } : { rate: 1, pitch: 1 };
  return isElderlySpeechMode() ? { rate: +(base.rate * 0.88).toFixed(2), pitch: base.pitch } : base;
}

export async function speak(text: string, lang: string, _bcp?: string, opts?: SpeakOpts) {
  const tone = confidenceTone(opts);
  stopSpeaking();
  const clean = text.replace(/[*_#>`]/g, "").slice(0, 1400);
  const l = langOfText(clean, lang);
  lastSpoken = { text, lang: l, bcp: BCP[l] ?? l };
  // 0. a cloud voice the person chose (Sarvam / ElevenLabs): falls back to the device voice when unavailable
  let prefs = voicePrefs();
  if (!prefs.gender) {
    try {
      const s = await api<{ tts_voice_gender?: "female" | "male"; tts_engine?: "auto" | "kokoro" | "parler" | "mms" }>("/me/settings");
      prefs = { ...prefs, gender: s.tts_voice_gender ?? "female", localTts: s.tts_engine ?? "auto" };
      setVoicePrefs({ gender: prefs.gender, localTts: prefs.localTts });
    } catch { /* offline or unauthenticated */ }
  }
  const gender = prefs.gender ?? "female";
  const playback = prefs.playback ?? (spokenRoute() ? "gemini_tts" : "device");
  if (playback === "gemini_tts") {
    try {
      const r = await api<{ audio_base64: string; format?: string; engine?: string }>("/tts", { method: "POST", body: JSON.stringify({ text: clean, lang: l, engine: "gemini", gender }) });
      await playAudioPayload(r, l, gender, speechRate() * tone.rate, clean);
      return;
    } catch { /* the device voice speaks instead */ }
  }
  const engine = prefs.engine ?? "local";
  if (engine === "sarvam" || engine === "elevenlabs") {
    try {
      const r = await api<{ audio_base64: string; format?: string; engine?: string }>("/tts", { method: "POST", body: JSON.stringify({ text: clean, lang: l, engine, gender }) });
      await playAudioPayload(r, l, gender, speechRate() * tone.rate, clean);
      return;
    } catch { /* fall through to the device voice */ }
  }
  // 0.5 premium local Kokoro / Parler when explicitly selected for English or Hindi too
  if ((l === "en" || l === "hi") && (prefs.localTts === "kokoro" || prefs.localTts === "parler")) {
    try {
      const r = await api<{ audio_base64: string; engine?: string }>("/tts", { method: "POST", body: JSON.stringify({ text: clean, lang: l, gender }) });
      await playAudioPayload(r, l, gender, speechRate() * tone.rate, clean);
      return;
    } catch { /* fall through to device voice */ }
  }
  // 1. the device's own voice (on-device, instant, no data leaves the phone)
  const voice = pickVoice(await deviceVoices(), l, gender);
  if (voice) {
    const sentences = splitCaptionSentences(clean);
    const u = new SpeechSynthesisUtterance(clean);
    u.voice = voice;
    u.lang = voice.lang;
    u.rate = 0.95 * speechRate() * tone.rate;
    u.pitch = (voicePrefs().pitch ?? 1) * tone.pitch;
    u.onstart = () => {
      playAudioCue("speak_start");
      emitSpeechState({ speaking: true, lang: l, gender, engine: voice.name, caption: sentences[0]?.text, progress: 0 });
    };
    u.onboundary = (ev) => {
      const ci = ev.charIndex ?? 0;
      const hit = sentences.find((s) => ci >= s.start && ci < s.end) ?? sentences[sentences.length - 1];
      const progress = clean.length ? Math.min(1, ci / clean.length) : 0;
      emitSpeechState({ speaking: true, lang: l, gender, engine: voice.name, caption: hit?.text, progress });
    };
    u.onend = () => emitSpeechState({ speaking: false });
    u.onerror = (ev) => {
      playAudioCue("error");
      emitSpeechState({ speaking: false });
      if (ev.error !== "interrupted" && ev.error !== "canceled") {
        reportServiceFailure({ service: "Device Speech", reason: `Speech synthesis failed (${ev.error || "unavailable"})` });
      }
    };
    speechSynthesis.speak(u);
    return;
  }
  // 2. no device voice for this language: the local server voice (Kokoro / Parler / MMS)
  if (l === "en") {
    playAudioCue("error");
    emitSpeechState({ speaking: false });
    reportServiceFailure({ service: "Device Speech", reason: "No English voice is available on this device." });
    return;
  }
  try {
    const r = await api<{ audio_base64: string; engine?: string }>("/tts", { method: "POST", body: JSON.stringify({ text: clean, lang: l, gender }) });
    await playAudioPayload(r, l, gender, speechRate() * tone.rate, clean);
  } catch (e: any) {
    playAudioCue("error");
    emitSpeechState({ speaking: false });
    if (e instanceof ApiError && e.status === 501) {
      reportServiceFailure({ service: "Speech Service", reason: e.message || "No voice available for this language", status: 501 });
    }
  }
}
