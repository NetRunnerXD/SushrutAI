"use client";

import { API, getProfile, getTokens } from "./api";

export type LiveMode = "transcribe" | "translate" | "talk";

export type LiveEvent = {
  type: string;
  text?: string;
  lang?: string;
  message?: string;
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
  level: () => number;
};

/** Play 24 kHz 16-bit mono PCM chunks from Gemini as they arrive. */
export function createPcmPlayer() {
  let ctx: AudioContext | null = null;
  let next = 0;
  return {
    push(pcm: ArrayBuffer) {
      const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      if (!ctx) ctx = new Ctx({ sampleRate: 24000 });
      const i16 = new Int16Array(pcm);
      const f32 = new Float32Array(i16.length);
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;
      const buf = ctx.createBuffer(1, f32.length, 24000);
      buf.copyToChannel(f32, 0);
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.connect(ctx.destination);
      const when = Math.max(ctx.currentTime + 0.02, next);
      src.start(when);
      next = when + buf.duration;
    },
    stop() {
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
  onEvent: (ev: LiveEvent) => void;
}): Promise<LiveHandle> {
  const tokens = getTokens();
  if (!tokens) throw new Error("Sign in first");
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

  const wsBase = API.replace(/^http/, "ws");
  const qs = new URLSearchParams({ token: tokens.access_token });
  const prof = getProfile()?.id;
  if (prof) qs.set("profile_id", prof);
  const ws = new WebSocket(`${wsBase}/voice/live?${qs.toString()}`);
  ws.binaryType = "arraybuffer";
  const player = opts.mode === "transcribe" ? null : createPcmPlayer();
  let open = false;
  const pending: ArrayBuffer[] = [];

  ws.onopen = () => {
    open = true;
    ws.send(JSON.stringify({
      type: "start", mode: opts.mode, model: opts.model ?? "",
      lang: opts.lang, target_lang: opts.targetLang ?? opts.lang,
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
    stop: () => {
      try { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" })); } catch { /* ignore */ }
      try { ws.close(); } catch { /* ignore */ }
      player?.stop();
      stream.getTracks().forEach((t) => t.stop());
      audio.close().catch(() => {});
    },
  };
}
