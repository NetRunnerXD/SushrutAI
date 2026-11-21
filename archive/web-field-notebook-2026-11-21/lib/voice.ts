"use client";

import { api, ApiError } from "./api";

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

export async function startServerRecording(lang: string): Promise<Recorder> {
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
          fd.append("lang", lang);
          try {
            const r = await api<{ text: string }>("/stt", { method: "POST", body: fd });
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

export async function startHandsFree(lang: string, opts: {
  onUtterance: (text: string) => void;
  onState: (s: "idle" | "speech" | "transcribing") => void;
  onError: (e: unknown) => void;
}): Promise<HandsFree> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  const ctx = new AudioContext();
  const src = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  src.connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";

  let noise = 0.01, rec: MediaRecorder | null = null, chunks: Blob[] = [];
  let speechMs = 0, silenceMs = 0, stopped = false, last = performance.now();

  const finish = () => {
    const r = rec;
    rec = null;
    if (!r) return;
    r.onstop = async () => {
      const blob = new Blob(chunks, { type: r.mimeType || "audio/webm" });
      chunks = [];
      if (blob.size < 4000) { opts.onState("idle"); return; }
      opts.onState("transcribing");
      const fd = new FormData();
      fd.append("file", blob, "speech.webm");
      fd.append("lang", lang);
      try {
        const res = await api<{ text: string }>("/stt", { method: "POST", body: fd });
        if (res.text?.trim()) opts.onUtterance(res.text.trim());
      } catch (e) { opts.onError(e); }
      opts.onState("idle");
    };
    r.stop();
  };

  const tick = () => {
    if (stopped) return;
    const now = performance.now(), dt = now - last;
    last = now;
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    const rms = Math.sqrt(sum / buf.length);
    const loud = rms > Math.max(0.02, noise * 3);
    if (!loud) noise = noise * 0.98 + rms * 0.02; // adaptive noise floor
    if (loud) {
      speechMs += dt; silenceMs = 0;
      if (speechMs > 150 && isSpeaking()) stopSpeaking(); // barge-in
      if (!rec && speechMs > 200) {
        chunks = [];
        rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
        rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
        rec.start(200);
        opts.onState("speech");
      }
    } else {
      silenceMs += dt;
      if (silenceMs > 300) speechMs = 0;
      if (rec && silenceMs > 900) finish();
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return {
    stop: () => {
      stopped = true;
      if (rec) rec.stop();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
    },
  };
}

// ---------------------------------------------------------------- TTS
let audio: HTMLAudioElement | null = null;

export function isSpeaking(): boolean {
  return (!!audio && !audio.paused) || (typeof speechSynthesis !== "undefined" && speechSynthesis.speaking);
}

export function stopSpeaking() {
  audio?.pause();
  audio = null;
  if (typeof speechSynthesis !== "undefined") speechSynthesis.cancel();
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

export async function speak(text: string, lang: string, bcp: string) {
  stopSpeaking();
  lastSpoken = { text, lang, bcp };
  const clean = text.replace(/[*_#>`]/g, "").slice(0, 1400);
  try {
    const r = await api<{ audio_base64: string }>("/tts", { method: "POST", body: JSON.stringify({ text: clean, lang }) });
    audio = new Audio(`data:audio/wav;base64,${r.audio_base64}`);
    audio.playbackRate = speechRate();
    (audio as any).preservesPitch = true;
    await audio.play();
    return;
  } catch {
    /* fall back to browser voices */
  }
  if (typeof speechSynthesis === "undefined") return;
  const u = new SpeechSynthesisUtterance(clean);
  u.lang = bcp;
  const voice = speechSynthesis.getVoices().find((v) => v.lang === bcp) ?? speechSynthesis.getVoices().find((v) => v.lang.startsWith(bcp.slice(0, 2)));
  if (voice) u.voice = voice;
  u.rate = 0.95 * speechRate();
  speechSynthesis.speak(u);
}
