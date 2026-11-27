"use client";

import { API, getProfile, getTokens } from "./api";

export type LiveMode = "transcribe" | "translate" | "talk";

export type LiveEvent = {
  type: string;
  text?: string;
  question?: string;
  reply?: string;
  structured?: unknown;
  citations?: unknown[];
  disclaimer?: string;
  red_flag?: { triggered?: boolean; kind?: string | null; messages?: string[] };
  provider?: string;
  model?: string;
  tier?: string;
  speed?: string;
  latency_ms?: number;
  kind?: string;
  clinical?: {
    specialty_classification?: unknown;
    specialist_routing?: unknown;
    investigations?: unknown[];
  } | null;
  lang?: string;
  message?: string;
  preserved_text?: string;
  interruption_type?: string;
  should_stop_tts?: boolean;
  spoken_prefix?: string;
  safety?: { triggered?: boolean; kind?: string | null; messages?: string[] };
  skip_tool?: boolean;
};

const WORKLET = `
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.acc = 0;
    this.buf = [];
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.acc += 1;
      if (this.acc < this.step) continue;
      this.acc -= this.step;
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.buf.push((s < 0 ? s * 0x8000 : s * 0x7fff) | 0);
      if (this.buf.length >= 1600) {
        const pcm = new Int16Array(this.buf);
        this.buf = [];
        this.port.postMessage(pcm.buffer, [pcm.buffer]);
      }
    }
    return true;
  }
}
registerProcessor("pcm-capture", PcmCapture);
`;

export type LiveHandle = {
  stop: () => void;
  /** Ask the transcriber for the finished line, and keep the socket open until it arrives. */
  endAudio: () => void;
  /** Immediately interrupt any queued AI audio playback when the patient starts speaking. */
  interruptAudio: () => void;
  /** Notify backend orchestrator of a patient barge-in with spoken prefix and captured text. */
  sendBargeIn: (payload: { text?: string; spokenPrefix?: string; interruptionType?: string }) => void;
  /** True while queued AI audio is actively playing. */
  isPlayingAudio: () => boolean;
  /** Sync updated session, scope, or edited conversation history to an active Live session. */
  syncContext: (ctx: {
    sessionId?: string | null;
    useRecord?: boolean;
    speechMode?: "general" | "elderly";
    responseStyle?: "direct" | "detailed";
    history?: { role: string; content: string }[];
    replaceHistory?: boolean;
  }) => void;
  level: () => number;
};

/** Play 24 kHz 16-bit mono PCM chunks from Gemini as they arrive, with elderly pacing and soft gain envelopes. */
export function createPcmPlayer(opts?: { speechMode?: "general" | "elderly" }) {
  let ctx: AudioContext | null = null;
  let masterGain: GainNode | null = null;
  let next = 0;
  let mode: "general" | "elderly" = opts?.speechMode ?? "general";
  let activeSources: AudioBufferSourceNode[] = [];
  let lastPlayEndMs = 0;

  const ensureCtx = () => {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return null;
    if (!ctx) {
      // Use native hardware sample rate so Chrome/Windows AEC keeps a clean playback reference
      ctx = new Ctx();
      masterGain = ctx.createGain();
      masterGain.gain.setValueAtTime(1.0, ctx.currentTime);
      masterGain.connect(ctx.destination);
    }
    return ctx;
  };

  return {
    setSpeechMode(m: "general" | "elderly") {
      mode = m;
    },
    push(pcm: ArrayBuffer) {
      const c = ensureCtx();
      if (!c || !masterGain) return;
      if (c.state === "suspended") c.resume().catch(() => {});
      const i16 = new Int16Array(pcm);
      const f32 = new Float32Array(i16.length);
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
      const buf = c.createBuffer(1, f32.length, 24000);
      buf.copyToChannel(f32, 0);
      const src = c.createBufferSource();
      src.buffer = buf;
      const rate = mode === "elderly" ? 0.9 : 1.0;
      src.playbackRate.value = rate;
      src.connect(masterGain);

      // Jitter-safe lead-in when starting a fresh utterance; seamless back-to-back chaining otherwise
      const leadIn = mode === "elderly" ? 0.06 : 0.02;
      const isFreshQueue = activeSources.length === 0 || next < c.currentTime;
      const when = isFreshQueue ? c.currentTime + leadIn : next;

      masterGain.gain.cancelScheduledValues(c.currentTime);
      masterGain.gain.setValueAtTime(masterGain.gain.value || 1.0, c.currentTime);
      masterGain.gain.linearRampToValueAtTime(1.0, c.currentTime + 0.015);

      activeSources.push(src);
      src.onended = () => {
        activeSources = activeSources.filter((s) => s !== src);
        lastPlayEndMs = Date.now();
      };
      src.start(when);
      next = when + buf.duration / rate;
      lastPlayEndMs = Date.now() + Math.max(0, (next - c.currentTime) * 1000);
    },
    interrupt() {
      if (ctx && masterGain) {
        const now = ctx.currentTime;
        masterGain.gain.cancelScheduledValues(now);
        masterGain.gain.setValueAtTime(masterGain.gain.value, now);
        masterGain.gain.linearRampToValueAtTime(0.0001, now + 0.025);
      }
      const toStop = activeSources;
      activeSources = [];
      next = ctx ? ctx.currentTime : 0;
      lastPlayEndMs = 0;
      setTimeout(() => {
        for (const s of toStop) {
          try { s.stop(); } catch { /* ignore */ }
        }
      }, 30);
    },
    isPlaying() {
      return !!ctx && activeSources.length > 0 && ctx.currentTime < next;
    },
    isPlayingOrTail(tailMs = 300) {
      return (!!ctx && activeSources.length > 0 && ctx.currentTime < next) || (lastPlayEndMs > 0 && Date.now() - lastPlayEndMs < tailMs);
    },
    stop() {
      for (const s of activeSources) {
        try { s.stop(); } catch { /* ignore */ }
      }
      activeSources = [];
      masterGain = null;
      ctx?.close().catch(() => {});
      ctx = null;
      next = 0;
    },
  };
}

/** Open /voice/live and a microphone that sends 16 kHz PCM. The page never sees the Gemini key. */
export async function startGeminiLive(opts: {
  mode: LiveMode;
  model?: string;
  lang: string;
  targetLang?: string;
  sessionId?: string | null;
  useRecord?: boolean;
  speechMode?: "general" | "elderly";
  responseStyle?: "direct" | "detailed";
  voiceName?: string;
  history?: { role: string; content: string }[];
  replaceHistory?: boolean;
  onEvent: (ev: LiveEvent) => void;
}): Promise<LiveHandle> {
  const tokens = getTokens();
  if (!tokens) throw new Error("Sign in first");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false, channelCount: 1 },
  });
  const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const audio = new Ctx();
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  await audio.audioWorklet.addModule(url);
  URL.revokeObjectURL(url);
  const src = audio.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(audio, "pcm-capture");
  const analyser = audio.createAnalyser();
  analyser.fftSize = 512;
  src.connect(analyser);
  src.connect(node);
  const levelBuf = new Float32Array(analyser.fftSize);

  const wsBase = API.replace(/^http/, "ws");
  const qs = new URLSearchParams({ token: tokens.access_token });
  const prof = getProfile()?.id;
  if (prof) qs.set("profile_id", prof);
  const ws = new WebSocket(`${wsBase}/voice/live?${qs.toString()}`);
  ws.binaryType = "arraybuffer";
  const player = opts.mode === "transcribe" ? null : createPcmPlayer({ speechMode: opts.speechMode });
  let open = false;
  const pending: ArrayBuffer[] = [];

  ws.onopen = () => {
    open = true;
    ws.send(JSON.stringify({
      type: "start", mode: opts.mode, model: opts.model ?? "",
      lang: opts.lang, target_lang: opts.targetLang ?? opts.lang,
      session_id: opts.sessionId ?? "",
      use_record: !!opts.useRecord,
      speech_mode: opts.speechMode ?? "general",
      response_style: opts.responseStyle ?? "direct",
      voice_name: opts.voiceName ?? "",
      history: opts.history ?? [],
      replace_history: !!opts.replaceHistory,
    }));
    for (const buf of pending.splice(0)) ws.send(buf);
  };
  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) {
      player?.push(e.data);
      return;
    }
    try { opts.onEvent(JSON.parse(e.data) as LiveEvent); } catch { /* ignore a bad frame */ }
  };
  ws.onerror = () => opts.onEvent({ type: "error", message: "Gemini Live stopped. You can still use the mic on this device." });
  node.port.onmessage = (e) => {
    // Echo gate: while TTS is playing (plus 300ms room tail), withhold mic PCM from Gemini Live
    // until genuine barge-in calls interruptAudio(), preventing speaker bleed from self-prompting.
    if (player?.isPlayingOrTail(300)) return;
    const buf = e.data as ArrayBuffer;
    if (open && ws.readyState === WebSocket.OPEN) ws.send(buf);
    else pending.push(buf);
  };

  return {
    level: () => {
      analyser.getFloatTimeDomainData(levelBuf);
      let sum = 0;
      for (const v of levelBuf) sum += v * v;
      return Math.min(1, Math.sqrt(sum / levelBuf.length) * 6);
    },
    endAudio: () => {
      try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "end" })); } catch { /* ignore */ }
    },
    interruptAudio: () => {
      player?.interrupt();
    },
    sendBargeIn: (payload) => {
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "barge_in",
            text: payload.text ?? "",
            spoken_prefix: payload.spokenPrefix ?? "",
            interruption_type: payload.interruptionType ?? "",
          }));
        }
      } catch { /* ignore */ }
    },
    isPlayingAudio: () => player?.isPlaying() ?? false,
    syncContext: (ctx) => {
      if (ctx.speechMode) player?.setSpeechMode(ctx.speechMode);
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "sync_context",
            session_id: ctx.sessionId ?? "",
            use_record: !!ctx.useRecord,
            speech_mode: ctx.speechMode ?? "general",
            response_style: ctx.responseStyle ?? "direct",
            history: ctx.history ?? [],
            replace_history: !!ctx.replaceHistory,
          }));
        }
      } catch { /* ignore */ }
    },
    stop: () => {
      try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" })); } catch { /* ignore */ }
      try { ws.close(); } catch { /* ignore */ }
      player?.stop();
      stream.getTracks().forEach((t) => t.stop());
      audio.close().catch(() => {});
    },
  };
}
