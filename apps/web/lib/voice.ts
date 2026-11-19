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

/** The language the server heard in the last recording (it detects among the app language, English and Hindi).
 *  The answer is written in it, so speaking Hindi to the English app gets a Hindi answer. */
export const heard: { lang: string | null; confidence: number } = { lang: null, confidence: 0 };
export function setHeardLang(l: string | null) { heard.lang = l; heard.confidence = l ? 1 : 0; }

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
          fd.append("lang", "auto");
          fd.append("ui", lang);
          try {
            const r = await api<{ text: string; lang?: string; confidence?: number }>("/stt", { method: "POST", body: fd });
            heard.lang = r.lang ?? null;
            heard.confidence = r.confidence ?? 0;
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
  onUtterance: (text: string, lang?: string) => void;
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
      fd.append("lang", "auto");
      fd.append("ui", lang);
      try {
        const res = await api<{ text: string; lang?: string; confidence?: number }>("/stt", { method: "POST", body: fd });
        heard.lang = res.lang ?? null;
        heard.confidence = res.confidence ?? 0;
        if (res.text?.trim()) opts.onUtterance(res.text.trim(), res.lang);
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
  [/[\u0C00-\u0C7F]/, "te"], [/[\u0A80-\u0AFF]/, "gu"], [/[\u0C80-\u0CFF]/, "kn"], [/[\u0D00-\u0D7F]/, "ml"], [/[\u0B00-\u0B7F]/, "or"]];
export function langOfText(text: string, fallback: string): string {
  const letters = text.replace(/[^\p{L}]/gu, "");
  for (const [re, l] of SCRIPTS) {
    const n = (letters.match(new RegExp(re.source, "g")) ?? []).length;
    if (n > letters.length * 0.3) return l === "hi" && fallback === "mr" ? "mr" : l;
  }
  return /[A-Za-z]/.test(letters) && !SCRIPTS.some(([re]) => re.test(letters)) ? "en" : fallback;
}
const BCP: Record<string, string> = { en: "en-IN", hi: "hi-IN", bn: "bn-IN", ta: "ta-IN", te: "te-IN", mr: "mr-IN",
  gu: "gu-IN", kn: "kn-IN", ml: "ml-IN", or: "or-IN" };

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

type VoicePrefs = { voices?: Record<string, string>; pitch?: number };
export function voicePrefs(): VoicePrefs {
  try { return JSON.parse(localStorage.getItem("sushrut.voice") || "{}"); } catch { return {}; }
}
export function setVoicePrefs(p: VoicePrefs) {
  try { localStorage.setItem("sushrut.voice", JSON.stringify({ ...voicePrefs(), ...p })); } catch { /* ignore */ }
}

/** Best device voice for a language: the person's choice, else Indian English / the language's Indian voice,
 *  else any voice of that language (English: anything but en-GB when another exists). */
export function pickVoice(voices: SpeechSynthesisVoice[], lang: string): SpeechSynthesisVoice | null {
  const chosen = voicePrefs().voices?.[lang];
  const byUri = chosen && voices.find((v) => v.voiceURI === chosen);
  if (byUri) return byUri;
  const bcp = BCP[lang] ?? lang;
  const base = bcp.slice(0, 2).toLowerCase();
  const same = voices.filter((v) => v.lang.replace("_", "-").toLowerCase().startsWith(base));
  const exact = same.filter((v) => v.lang.replace("_", "-").toLowerCase() === bcp.toLowerCase());
  const local = (l: SpeechSynthesisVoice[]) => l.find((v) => v.localService) ?? l[0];
  if (exact.length) return local(exact);
  if (lang === "en") {
    const notGb = same.filter((v) => !/en[-_]gb/i.test(v.lang));
    return local(notGb.length ? notGb : same) ?? null;
  }
  return local(same) ?? null;
}

export async function speak(text: string, lang: string, _bcp?: string) {
  stopSpeaking();
  const clean = text.replace(/[*_#>`]/g, "").slice(0, 1400);
  const l = langOfText(clean, lang);
  lastSpoken = { text, lang: l, bcp: BCP[l] ?? l };
  // 1. the device's own voice (on-device, instant, no data leaves the phone)
  const voice = pickVoice(await deviceVoices(), l);
  if (voice) {
    const u = new SpeechSynthesisUtterance(clean);
    u.voice = voice;
    u.lang = voice.lang;
    u.rate = 0.95 * speechRate();
    u.pitch = voicePrefs().pitch ?? 1;
    speechSynthesis.speak(u);
    return;
  }
  // 2. no device voice for this Indian language: the local server voice (MMS); English is never sent there
  if (l === "en") return;
  try {
    const r = await api<{ audio_base64: string }>("/tts", { method: "POST", body: JSON.stringify({ text: clean, lang: l }) });
    audio = new Audio(`data:audio/wav;base64,${r.audio_base64}`);
    audio.playbackRate = speechRate();
    (audio as any).preservesPitch = true;
    await audio.play();
  } catch { /* nothing can read this language aloud on this device */ }
}
