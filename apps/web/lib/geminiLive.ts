"use client";

import { getProfile, getTokens, wsUrl } from "./api";

export type LiveMode = "transcribe" | "translate" | "talk";

export type LiveEvent = {
  type: string;
  text?: string;
  greeting?: string;
  question?: string;
  spoken_response?: string;
  language?: string;
  lang?: string;
  message?: string;
  safety?: { triggered?: boolean; kind?: string | null; messages?: string[] };
  skip_tool?: boolean;
  onboarding?: Record<string, unknown> | null;
  language_meta?: Record<string, unknown> | null;
  structured?: Record<string, unknown> | null;
  family_context?: Record<string, unknown> | null;
  patient_profile?: Record<string, unknown> | null;
  assessment_panel?: Record<string, unknown> | null;
  compact_summary?: Record<string, unknown> | null;
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

export type LiveState = "IDLE" | "CONNECTING" | "LISTENING" | "PROCESSING" | "SPEAKING" | "INTERRUPTED" | "ERROR";

export type LiveHandle = {
  stop: () => void;
  /** Ask the transcriber for the finished line, and keep the socket open until it arrives. */
  endAudio: () => void;
  /** Interrupt AI speech playback cleanly (barge-in) without dropping the WebSocket. */
  interrupt: () => void;
  /** Mute/unmute outbound microphone PCM frames while keeping session open. */
  setMuted: (muted: boolean) => void;
  /** Pause/resume queued AI speech playback cleanly. */
  setPaused: (paused: boolean) => void;
  level: () => number;
};

/** Play 24 kHz 16-bit mono PCM chunks from Gemini as they arrive with clean barge-in & pause/resume. */
export function createPcmPlayer(onSpeakingChange?: (speaking: boolean) => void) {
  let ctx: AudioContext | null = null;
  let next = 0;
  let paused = false;
  const active = new Set<AudioBufferSourceNode>();
  const notify = () => onSpeakingChange?.(active.size > 0 && !paused);
  return {
    push(pcm: ArrayBuffer) {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      if (!ctx) ctx = new Ctx({ sampleRate: 24000 });
      if (ctx.state === "suspended" && !paused) ctx.resume().catch(() => {});
      const i16 = new Int16Array(pcm);
      const f32 = new Float32Array(i16.length);
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
      const buf = ctx.createBuffer(1, f32.length, 24000);
      buf.copyToChannel(f32, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      active.add(src);
      src.onended = () => {
        active.delete(src);
        notify();
      };
      const when = Math.max(ctx.currentTime + 0.02, next);
      src.start(when);
      next = when + buf.duration;
      notify();
    },
    interrupt() {
      for (const src of active) {
        try { src.stop(); } catch { /* ignore */ }
      }
      active.clear();
      next = ctx ? ctx.currentTime : 0;
      notify();
    },
    setPaused(p: boolean) {
      paused = p;
      if (!ctx) { notify(); return; }
      if (p) ctx.suspend().catch(() => {});
      else ctx.resume().catch(() => {});
      notify();
    },
    isSpeaking: () => active.size > 0 && !paused,
    stop() {
      for (const src of active) {
        try { src.stop(); } catch { /* ignore */ }
      }
      active.clear();
      ctx?.close().catch(() => {});
      ctx = null;
      next = 0;
      notify();
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
  silenceMs?: number;
  onStateChange?: (s: LiveState) => void;
  onEvent: (ev: LiveEvent) => void;
}): Promise<LiveHandle> {
  opts.onStateChange?.("CONNECTING");
  const tokens = getTokens();
  if (!tokens) {
    opts.onStateChange?.("ERROR");
    throw new Error("Sign in first");
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
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

  const qs = new URLSearchParams({ token: tokens.access_token });
  const prof = getProfile()?.id;
  if (prof) qs.set("profile_id", prof);
  const ws = new WebSocket(wsUrl(`/voice/live?${qs.toString()}`));
  ws.binaryType = "arraybuffer";
  let muted = false;
  let closed = false;
  const player = opts.mode === "transcribe" ? null : createPcmPlayer((speaking) => {
    if (!closed) opts.onStateChange?.(speaking ? "SPEAKING" : "LISTENING");
  });
  let open = false;
  const pending: ArrayBuffer[] = [];

  ws.onopen = () => {
    open = true;
    ws.send(JSON.stringify({
      type: "start", mode: opts.mode, model: opts.model ?? "",
      lang: opts.lang, target_lang: opts.targetLang ?? opts.lang,
      session_id: opts.sessionId ?? "",
      silence_ms: opts.silenceMs ?? 3500,
    }));
    for (const buf of pending.splice(0)) ws.send(buf);
    opts.onStateChange?.("LISTENING");
  };
  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) {
      player?.push(e.data);
      return;
    }
    try {
      const ev = JSON.parse(e.data) as LiveEvent;
      if (ev.type === "ready") opts.onStateChange?.("LISTENING");
      else if (ev.type === "tool_call") opts.onStateChange?.("PROCESSING");
      else if (ev.type === "interrupted" || ev.type === "vad_start") {
        if (player?.isSpeaking() || ev.type === "interrupted") {
          player?.interrupt();
          opts.onStateChange?.("INTERRUPTED");
        }
      } else if (ev.type === "turn_complete" && !player?.isSpeaking()) {
        opts.onStateChange?.("LISTENING");
      } else if (ev.type === "error") {
        opts.onStateChange?.("ERROR");
      }
      opts.onEvent(ev);
    } catch { /* ignore a bad frame */ }
  };
  ws.onerror = () => {
    opts.onStateChange?.("ERROR");
    opts.onEvent({ type: "error", message: "Gemini Live stopped. You can still use the mic on this device." });
  };
  ws.onclose = () => {
    if (!closed) opts.onStateChange?.("IDLE");
  };
  node.port.onmessage = (e) => {
    if (muted || closed) return;
    const buf = e.data as ArrayBuffer;
    if (open && ws.readyState === WebSocket.OPEN) ws.send(buf);
    else pending.push(buf);
  };

  return {
    level: () => {
      if (muted || closed) return 0;
      analyser.getFloatTimeDomainData(levelBuf);
      let sum = 0;
      for (const v of levelBuf) sum += v * v;
      return Math.min(1, Math.sqrt(sum / levelBuf.length) * 6);
    },
    endAudio: () => {
      try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "end" })); } catch { /* ignore */ }
    },
    interrupt: () => {
      player?.interrupt();
      try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "interrupt" })); } catch { /* ignore */ }
      opts.onStateChange?.("INTERRUPTED");
    },
    setMuted: (m: boolean) => {
      muted = m;
      stream.getAudioTracks().forEach((t) => { t.enabled = !m; });
    },
    setPaused: (p: boolean) => {
      player?.setPaused(p);
    },
    stop: () => {
      closed = true;
      try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" })); } catch { /* ignore */ }
      try { ws.close(); } catch { /* ignore */ }
      player?.stop();
      stream.getTracks().forEach((t) => t.stop());
      audio.close().catch(() => {});
      opts.onStateChange?.("IDLE");
    },
  };
}
