"use client";

import {
  ArrowDown, Captions, Check, Copy, Headphones, History, PanelRight, Paperclip, Pencil, Plus, RotateCcw,
  SendHorizonal, Square, Stethoscope, UserX, Volume2, VolumeX, X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnswerCard, plainText, RedFlagBanner, summaryOf } from "@/components/AnswerCard";
import { ChatHistory } from "@/components/ChatHistory";
import { ModelSwitch, useChatModel } from "@/components/ModelSwitch";
import { ProfileStrip } from "@/components/ProfileStrip";
import { Inspector } from "@/components/desktop/Inspector";
import { useUiMode } from "@/lib/ui-mode";
import { Suggestions } from "@/components/Suggestions";
import { Sheet } from "@/components/ui/Sheet";
import { useSOS } from "@/components/Emergency";
import { GeminiLiveButtons, GeminiLiveStatus } from "@/components/GeminiLive";
import { useLiveTalk } from "@/components/LiveTalkProvider";
import { PageTitle } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Banner } from "@/components/ui/Trust";
import { ActionCard, HeardConfirm, VoiceWell, VoiceError, VoiceOrb, Waveform, useVoiceCapture, useVoiceCommands } from "@/components/Voice";
import { api, postSSE } from "@/lib/api";
import { bcp47, fill, useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";
import { SYMPTOM_CHIPS } from "@/lib/symptoms";
import { clearHeardMeta, heard, isSpeaking, langOfText, normalizeChatText, playAudioCue, setVoicePrefs, speak, SPEECH_STATE_EVENT, spokenRoute, stopSpeaking, voicePrefs, type SpeechStatus } from "@/lib/voice";
import { FrontendConversationOrchestrator } from "@/lib/orchestrator";
import { useVoiceLoop, VOICE_INTENT_EVENT } from "@/components/VoiceLoop";
import { discardDoc, ImageCheckBanner, uploadDoc, type ImageIssue } from "@/components/ImageCheck";

type Turn = {
  role: "user" | "assistant"; text: string; answer?: any; redFlag?: any; error?: string; photos?: string[];
  stopped?: boolean
};
type Attached = { id: string; name: string; preview?: string; issue?: ImageIssue | null };  // issue: waiting for Retake / Send anyway
const MAX_FILES = 3;
const THINKING_KEYS = ["checkingSources", "thinking"] as const;

function AskInner() {
  const { t, lang } = useI18n();
  const { prefs } = usePrefs();
  const isV2 = (prefs.chatUi ?? "v2") === "v2";
  const params = useSearchParams();
  const router = useRouter();
  const { openSOS: triggerSOS } = useSOS();
  const [sosEnabled, setSosEnabled] = useState(true);
  const [speechMode, setSpeechMode] = useState<"general" | "elderly">("general");
  const [responseStyle, setResponseStyle] = useState<"direct" | "detailed">("direct");
  const [liveVoice, setLiveVoice] = useState<string>("Aoede");
  useEffect(() => {
    try {
      const v = window.localStorage.getItem(`sushrut.liveVoice.${lang}`);
      setLiveVoice(v || "Aoede");
    } catch { setLiveVoice("Aoede"); }
  }, [lang]);
  const changeLiveVoice = (next: string) => {
    setLiveVoice(next);
    try { window.localStorage.setItem(`sushrut.liveVoice.${lang}`, next); } catch { /* ignore */ }
  };
  useEffect(() => {
    try {
      const savedMode = window.localStorage.getItem("sushrut.speechMode");
      if (savedMode === "general" || savedMode === "elderly") setSpeechMode(savedMode);
      const savedStyle = window.localStorage.getItem("sushrut.responseStyle");
      if (savedStyle === "direct" || savedStyle === "detailed") setResponseStyle(savedStyle);
    } catch { /* ignore */ }
    api<{ sos_enabled?: boolean; speech_mode?: "general" | "elderly"; response_style?: "direct" | "detailed" }>("/me/settings")
      .then((r) => {
        setSosEnabled(r.sos_enabled !== false);
        if (r.speech_mode === "general" || r.speech_mode === "elderly") setSpeechMode(r.speech_mode);
        if (r.response_style === "direct" || r.response_style === "detailed") setResponseStyle(r.response_style);
      })
      .catch(() => { });
  }, []);
  const toggleSpeechMode = (next: "general" | "elderly") => {
    setSpeechMode(next);
    try { window.localStorage.setItem("sushrut.speechMode", next); } catch { /* ignore */ }
    api("/me/settings", { method: "PUT", body: JSON.stringify({ speech_mode: next }) }).catch(() => { });
  };
  const toggleResponseStyle = (next: "direct" | "detailed") => {
    setResponseStyle(next);
    try { window.localStorage.setItem("sushrut.responseStyle", next); } catch { /* ignore */ }
    api("/me/settings", { method: "PUT", body: JSON.stringify({ response_style: next }) }).catch(() => { });
  };
  const openSOS = useCallback(() => {
    if (sosEnabled) triggerSOS();
  }, [sosEnabled, triggerSOS]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const openedPast = useRef(false);  // set when a past chat is loaded, so it opens at its last answer
  const [turns, setTurns] = useState<Turn[]>([]);
  const [scope, setScope] = useState<"chat" | "record">("chat");
  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("sushrut.askScope");
      if (saved === "chat" || saved === "record") setScope(saved);
    } catch { /* ignore */ }
  }, []);
  const liveHistory = useMemo(
    () => turns.map((m) => ({ role: m.role, content: m.text })),
    [turns],
  );
  const { live, getCachedTurns, setCachedTurns, clearCachedTurns, setContextBridge } = useLiveTalk();
  const onLiveAnswer = useCallback(({ question, answer }: { question?: string; answer: any }) => {
    setTurns((p) => [
      ...p,
      ...(question ? [{ role: "user" as const, text: question }] : []),
      {
        role: "assistant" as const,
        text: answer.reply || answer.text || "",
        answer,
        redFlag: answer.red_flag?.triggered ? answer.red_flag : undefined,
      },
    ]);
  }, []);
  useEffect(() => {
    if (!sessionId) return;
    if (turns.length > 0) setCachedTurns(sessionId, turns);
    setContextBridge({
      sessionId,
      useRecord: scope === "record",
      speechMode,
      responseStyle,
      voiceName: liveVoice,
      history: liveHistory,
      onAnswer: onLiveAnswer,
    });
  }, [sessionId, turns, scope, speechMode, responseStyle, liveVoice, liveHistory, onLiveAnswer, setCachedTurns, setContextBridge]);
  const [text, setText] = useState("");
  // "Ask about this": a record attached from another tab (?ref=lab:<id>&about=HbA1c 9.5 %)
  const [about, setAbout] = useState<{ ref: string; label: string } | null>(null);
  useEffect(() => {
    const ref = params.get("ref");
    if (ref) setAbout({ ref, label: params.get("about") ?? ref });
  }, [params]);
  const [busy, setBusy] = useState(false);
  const [thinkIdx, setThinkIdx] = useState(0);
  const [files, setFiles] = useState<Attached[]>([]);
  const [draft, setDraft] = useState<string | null>(null);   // reply text while the model is still writing
  const abort = useRef<AbortController | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);     // the chats window
  useEffect(() => { if (params.get("chats")) setHistoryOpen(true); }, [params]);
  const [model, setModel, modelOptions] = useChatModel();
  const desktop = useUiMode() === "desktop";
  const [inspector, setInspector] = useState(false);
  const [showAll, setShowAll] = useState(false);              // long chats: earlier turns are folded
  const KEEP_TURNS = 6;
  const [listKey, setListKey] = useState(0);                 // bump to refresh the chat list
  const [profileCleared, setProfileCleared] = useState(false);
  const clearPatientProfile = async () => {
    if (!window.confirm("Clear your saved patient profile (name, age, demographics)?")) return;
    try {
      await api("/me/profile", { method: "DELETE" });
      setProfileCleared(true);
      setTimeout(() => setProfileCleared(false), 2500);
    } catch { /* ignore */ }
  };
  const [copied, setCopied] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [attachErr, setAttachErr] = useState<string | null>(null);
  const [stage, setStage] = useState<string>("thinking");
  const [speech, setSpeech] = useState<SpeechStatus>({ speaking: false });
  const [cuesOn, setCuesOn] = useState(true);
  const [captionsOn, setCaptionsOn] = useState(true);
  useEffect(() => {
    const vp = voicePrefs();
    setCuesOn(vp.audioCues !== false);
    setCaptionsOn(vp.captions !== false);
    setSpeech({ speaking: isSpeaking() });
    const onSpeech = (e: Event) => setSpeech((e as CustomEvent<SpeechStatus>).detail ?? { speaking: isSpeaking() });
    window.addEventListener(SPEECH_STATE_EVENT, onSpeech);
    return () => window.removeEventListener(SPEECH_STATE_EVENT, onSpeech);
  }, []);
  const toggleCues = () => {
    const next = !cuesOn;
    setCuesOn(next);
    setVoicePrefs({ audioCues: next });
  };
  const toggleCaptions = () => {
    const next = !captionsOn;
    setCaptionsOn(next);
    setVoicePrefs({ captions: next });
  };
  const loop = useVoiceLoop();  // the app-wide hands-free conversation (components/VoiceLoop.tsx)
  const handsFree = !!loop?.on;
  const handsFreeRef = useRef(handsFree);
  handsFreeRef.current = handsFree;
  const hfState = loop?.state ?? "idle";
  const orch = useRef(new FrontendConversationOrchestrator());
  useEffect(() => { orch.current.bindSession(sessionId); }, [sessionId]);
  useEffect(() => {
    const onVis = () => {
      if (document.hidden && orch.current.state !== "IDLE") orch.current.pauseSession();
      else if (!document.hidden && orch.current.state === "SESSION_PAUSED") orch.current.resumeSession();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);
  const bottom = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLFormElement>(null);
  // The composer sticks to the bottom over the conversation: keep focused buttons and scroll targets clear of it
  // (answer buttons under the composer on phones; WCAG 2.4.11 focus not obscured).
  useEffect(() => {
    const el = composer.current;
    if (!el) return;
    const root = document.documentElement;
    const fit = () => {
      const stuckAt = parseFloat(getComputedStyle(el).bottom) || 0;  // the bottom nav on phones, a margin on desktop
      root.style.scrollPaddingBottom = `${Math.min(el.offsetHeight + stuckAt + 12, window.innerHeight / 2)}px`;
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    window.addEventListener("resize", fit);
    return () => { ro.disconnect(); window.removeEventListener("resize", fit); root.style.scrollPaddingBottom = ""; };
  }, []);
  const sendRef = useRef<(m: string, spoken?: boolean) => void>(() => { });

  // session: resume ?session=… or start a new one. Changing language keeps the chat: each message carries its
  // language, so the next reply simply comes in the new one.
  const langRef = useRef(lang);
  langRef.current = lang;
  const createSessRef = useRef<Promise<{ id: string }> | null>(null);
  const startNew = useCallback(() => {
    abort.current?.abort();
    createSessRef.current = null;
    try {
      const prevSid = window.sessionStorage.getItem("sushrut.activeSessionId");
      if (prevSid) clearCachedTurns(prevSid);
      window.sessionStorage.removeItem("sushrut.activeSessionId");
    } catch { /* ignore */ }
    setTurns([]); setDraft(null); setFiles([]); setText("");
    api<{ id: string }>("/chat/sessions", { method: "POST", body: JSON.stringify({ lang: langRef.current }) })
      .then((s) => {
        setSessionId(s.id);
        try { window.sessionStorage.setItem("sushrut.activeSessionId", s.id); } catch { /* ignore */ }
      }).catch(() => { });
  }, [clearCachedTurns]);
  const resumeId = params.get("session");
  useEffect(() => {
    let ignore = false;
    let targetId = resumeId;
    if (!targetId) {
      try { targetId = window.sessionStorage.getItem("sushrut.activeSessionId"); } catch { /* ignore */ }
    }
    if (targetId) {
      const cached = getCachedTurns(targetId) as Turn[] | null;
      if (cached?.length) {
        setSessionId(targetId);
        setTurns(cached);
      }
      api<any>(`/chat/sessions/${targetId}`).then((s) => {
        if (ignore) return;
        setSessionId(s.id);
        try { window.sessionStorage.setItem("sushrut.activeSessionId", s.id); } catch { /* ignore */ }
        openedPast.current = true;
        const latestCached = getCachedTurns(s.id) as Turn[] | null;
        if (latestCached?.length && latestCached.length >= s.messages.length) {
          setTurns(latestCached);
        } else {
          const loaded: Turn[] = s.messages.map((m: any) => m.role === "user" ? { role: "user", text: m.content }
            : { role: "assistant", text: m.content, answer: { reply: m.content, structured: m.structured, citations: m.citations } });
          setTurns(loaded);
          if (loaded.length) setCachedTurns(s.id, loaded);
        }
      }).catch(() => { if (!ignore && !cached?.length) startNew(); });
      return () => { ignore = true; };
    }
    if (!createSessRef.current) {
      createSessRef.current = api<{ id: string }>("/chat/sessions", { method: "POST", body: JSON.stringify({ lang: langRef.current }) });
    }
    createSessRef.current.then((s) => {
      if (ignore) return;
      setSessionId(s.id);
      try { window.sessionStorage.setItem("sushrut.activeSessionId", s.id); } catch { /* ignore */ }
    }).catch(() => { createSessRef.current = null; });
    return () => { ignore = true; };
  }, [resumeId, startNew]);
  const newChat = () => { setHistoryOpen(false); if (resumeId) router.push("/ask"); else startNew(); };

  // Follow new messages only when the reader is already near the end; otherwise offer a "new reply" / scroll button.
  const nearEnd = useRef(true);
  const [newReply, setNewReply] = useState(false);
  const [scrolledUp, setScrolledUp] = useState(false);
  useEffect(() => {
    const onScroll = () => {
      const dist = document.documentElement.scrollHeight - (window.innerHeight + window.scrollY);
      nearEnd.current = dist <= 200;
      setScrolledUp(dist > 360);
      if (nearEnd.current) setNewReply(false);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  const toEnd = () => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); setNewReply(false); };
  useEffect(() => {
    const opened = openedPast.current && turns.length > 0;  // a past chat was just opened: show its last answer
    if (opened) openedPast.current = false;
    if (!turns.length) return;
    const last = turns[turns.length - 1];
    if (last?.role === "assistant" && (nearEnd.current || opened)) {
      setNewReply(false);
      requestAnimationFrame(() => document.querySelector("[data-last-answer]")?.scrollIntoView({ behavior: opened ? "auto" : "smooth", block: "start" }));
    } else if (nearEnd.current || last?.role === "user") toEnd();
    else setNewReply(true);
  }, [turns, busy]);
  useEffect(() => { if (draft && nearEnd.current) bottom.current?.scrollIntoView({ block: "end" }); }, [draft]);
  // the message box grows with its text, up to about five lines
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "36px";
    if (text) el.style.height = `${Math.min(el.scrollHeight, 140)}px`;
  }, [text]);
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => setThinkIdx((i) => (i + 1) % THINKING_KEYS.length), 3000);
    return () => clearInterval(id);
  }, [busy]);

  // prior: the conversation to send with (for Retry / Answer again / Edit, the turns before the replaced question)
  const send = useCallback(async (rawMsg: string, spoken = false, prior?: Turn[], withFiles: Attached[] = files) => {
    let msg = normalizeChatText(rawMsg);
    if ((!msg && !withFiles.length) || !sessionId || uploading || withFiles.some((f) => f.issue)) return;
    let base = prior ?? turns;
    if (busy) {
      // Patient spoke/typed extra input while a prompt is processing: cancel in-flight turn, append & re-send
      if (!msg || prior !== undefined) return;
      abort.current?.abort();
      const idx = (() => { for (let i = turns.length - 1; i >= 0; i--) if (turns[i].role === "user") return i; return -1; })();
      if (idx >= 0) {
        const prevTxt = turns[idx].text.startsWith("📎") ? "" : turns[idx].text;
        msg = prevTxt ? `${prevTxt} ${msg}`.trim() : msg;
        base = turns.slice(0, idx);
      }
    }
    playAudioCue("send");
    // answer in the language actually used: what the voice was heard in, or the script typed (Hindi typed in the
    // English app gets a Hindi answer)
    const L = spoken && heard.lang ? heard.lang : /[ऀ-෿]/.test(msg) ? langOfText(msg, lang) : lang;
    const voiceMeta = spoken ? {
      raw_transcript: heard.rawTranscript ?? msg,
      normalized_transcript: heard.normalizedTranscript ?? msg,
      lang: heard.lang ?? L,
      asr_confidence: heard.confidence || null,
      asr_engine: heard.engine ?? null,
      code_switched: !!heard.codeSwitched,
      segments: heard.segments ?? null,
    } : null;
    if (spoken) clearHeardMeta();
    const history = base.filter((x) => !x.error && !x.stopped)
      .map((x) => ({ role: x.role, content: x.role === "user" ? x.text : x.answer?.reply ?? "" }));
    const photos = withFiles.map((f) => f.preview).filter((x): x is string => !!x);
    setTurns([...base, { role: "user", text: msg.trim() || "📎 " + withFiles.map((f) => f.name).join(", "), photos }]);
    setStage(withFiles.length ? "readingAttachment" : "thinking");
    setText("");
    setFiles([]);
    setBusy(true);
    setDraft(null);
    const { generationId, controller: ctrl } = orch.current.beginGeneration();
    abort.current = ctrl;
    let redFlag: any = null;
    let written = "";
    let settled = false;
    try {
      await postSSE(`/chat/sessions/${sessionId}/messages`, {
        text: msg, lang: L, history,
        image_document_ids: withFiles.map((f) => f.id), context_refs: about ? [about.ref] : [], model_pref: model,
        replace_history: true, channel: spoken ? "voice" : "text", voice_meta: voiceMeta,
        force_english: spoken && spokenRoute() && (!L || L === "auto" || L === "app"), context_scope: scope,
        generation_id: generationId, spoken_prefix: orch.current.spokenPrefix || null
      },
        (ev, data) => {
          if (!orch.current.isCurrent(generationId)) return;
          if (ev === "cancelled") {
            if (settled) return;
            settled = true;
            setDraft(null);
            if (written) setTurns((p) => [...p, { role: "assistant", text: written, answer: { reply: written }, stopped: true }]);
            return;
          }
          if (ev === "status" && data.stage === "thinking") setStage("thinking");
          if (ev === "delta") { written = data.text; setDraft(data.text); }
          if (ev === "red_flag") {
            redFlag = data;
            if (data.kind === "emergency" || data.kind === "mental_health") openSOS();
          }
          if (ev === "answer") {
            settled = true;
            setDraft(null);
            playAudioCue("receive");
            setTurns((p) => [...p, { role: "assistant", text: data.reply, answer: data, redFlag }]);
            if (spoken || handsFreeRef.current) {  // a question is read whole; an answer reads its summary + follow-up question
              const top = data.structured?.possible_conditions?.[0]?.likelihood;
              const confidence = !top ? undefined : top === "high" ? "high" : top === "moderate" ? "medium" : "low";
              const cleanReply = plainText(data.reply, t);
              const fq = String(data.clinical?.followup_question?.question || data.structured?.follow_up_questions?.[0] || "").trim();
              const toSpeak = data.structured?.kind === "clarify"
                ? cleanReply
                : [summaryOf(cleanReply), fq].filter(Boolean).join(" ");
              speak(toSpeak, L, undefined, { confidence });
            }
          }
          if (ev === "error") {
            settled = true;
            playAudioCue("error");
            setTurns((p) => [...p, { role: "assistant", text: "", error: t.thinking.replace("…", "") + " — " + (data.hint ?? data.message) }]);
          }
        }, ctrl.signal);
    } catch (e: any) {
      if (e?.name === "AbortError") {
        // Stopped by the person: keep what was written, clearly marked as incomplete.
        // The cancelled event may already have saved that bubble.
        if (!settled && written) setTurns((p) => [...p, { role: "assistant", text: written, answer: { reply: written }, stopped: true }]);
      } else {
        playAudioCue("error");
        setTurns((p) => [...p, { role: "assistant", text: "", error: e.message }]);
      }
    } finally { setBusy(false); setDraft(null); abort.current = null; setListKey((k) => k + 1); }
  }, [sessionId, busy, uploading, turns, lang, files, about, openSOS, t, model, scope]);
  const stop = () => abort.current?.abort();
  useEffect(() => () => abort.current?.abort(), []);

  // Retry / Answer again: ask the last question again, replacing the answer (or error) after it
  const lastUser = (() => { for (let i = turns.length - 1; i >= 0; i--) if (turns[i].role === "user") return i; return -1; })();
  const askAgain = () => {
    if (lastUser < 0) return;
    send(turns[lastUser].text, false, turns.slice(0, lastUser), []);
  };
  // Edit: put the last question back in the box and remove it (and its answer) from the conversation
  const editLast = () => {
    if (lastUser < 0) return;
    setText(turns[lastUser].text.startsWith("📎") ? "" : turns[lastUser].text);
    setTurns(turns.slice(0, lastUser));
    box.current?.focus();
  };
  const copyAnswer = async (s: string) => {
    try { await navigator.clipboard.writeText(s); setCopied(true); setTimeout(() => setCopied(false), 1800); } catch { /* ignore */ }
  };
  sendRef.current = send;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.closest("input, textarea, [contenteditable]") !== null);
      if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey) { e.preventDefault(); box.current?.focus(); }
      if (e.key === "i" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey && document.documentElement.dataset.ui === "desktop") setInspector((o) => !o);
      if (e.key === "Escape") { stopSpeaking(); if (abort.current) abort.current.abort(); }
      if (e.altKey && (e.key === "m" || e.key === "M")) { e.preventDefault(); voiceToggle.current(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const foldAt = !showAll && turns.length > KEEP_TURNS + 2 ? turns.length - KEEP_TURNS : 0;
  const editSuggestion = (q: string) => { setText(q); requestAnimationFrame(() => box.current?.focus()); };

  // question handed over from Home / floating mic
  useEffect(() => {
    if (!sessionId) return;
    try {
      const pending = sessionStorage.getItem("sushrut.pendingAsk");
      if (pending) { sessionStorage.removeItem("sushrut.pendingAsk"); sendRef.current(pending, true); }
    } catch { /* ignore */ }
  }, [sessionId]);

  const voice = useVoiceCapture((said) => send(said, true));
  const voiceToggle = useRef(voice.toggle);
  voiceToggle.current = voice.toggle;
  const commands = useVoiceCommands();
  const actionsRef = useRef(voice.actions);
  actionsRef.current = voice.actions;

  async function toggleHandsFree() { await loop?.toggle(); }
  // While Ask is open, spoken questions and barge-in interruptions are orchestrated in place
  useEffect(() => {
    loop?.setQuestionHandler((said) => sendRef.current(said, true));
    loop?.setBargeInHandler((spokenSoFar) => {
      abort.current?.abort();
      orch.current.bargeIn(spokenSoFar, "");
      setBusy(false);
      setDraft(null);
    });
    return () => {
      loop?.setQuestionHandler(null);
      loop?.setBargeInHandler(null);
    };
  }, [loop]);
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  useEffect(() => {
    const on = (e: Event) => {
      const intent = (e as CustomEvent).detail;
      const last = [...turnsRef.current].reverse().find((x) => x.role === "assistant" && x.text);
      if (!last) return;
      if (intent === "simpler") sendRef.current(t.askSimpler, true);
      if (intent === "more") speak(last.text, langOfText(last.text, lang));
    };
    window.addEventListener(VOICE_INTENT_EVENT, on);
    return () => window.removeEventListener(VOICE_INTENT_EVENT, on);
  }, [t, lang]);

  async function attach(list: File[]) {
    setAttachErr(null);
    const ok = list.filter((f) => f.type.startsWith("image/") || f.type === "application/pdf");
    const room = MAX_FILES - files.length;
    if (ok.length > room) setAttachErr(t.maxAttachments);
    if (room <= 0 || !ok.length) return;
    setUploading(true);
    try {
      for (const f of ok.slice(0, room)) {
        const d = await uploadDoc(f, "other");  // also checked: blurry, dark, tiny or not health-related?
        setFiles((cur) => [...cur, {
          id: d.id, name: f.name || "photo", issue: d.issue,
          preview: f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined
        }]);
      }
    } catch (e: any) {
      setAttachErr(`${t.attachFailed}: ${e.message}`);
    } finally {
      setUploading(false);
    }
  }
  const flagged = files.find((f) => f.issue);
  const unattach = (id: string) => setFiles((cur) => {
    const f = cur.find((x) => x.id === id);
    if (f?.preview) URL.revokeObjectURL(f.preview);
    return cur.filter((x) => x.id !== id);
  });

  return (
    <div className={isV2 ? "w-full max-w-none px-1 sm:px-2" : "mx-auto max-w-4xl"}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <PageTitle>{t.askTitle}</PageTitle>
          <div className="flex flex-wrap gap-2">
            {/* chats live in a floating window (no side column), so the conversation always has the full width */}
            <button type="button" onClick={() => setHistoryOpen(true)} aria-haspopup="dialog"
              className="inline-flex min-h-12 items-center gap-2 rounded-[14px] border-[1.5px] border-border-input bg-surface px-3 font-semibold hover:bg-surface-2">
              <History aria-hidden className="h-5 w-5" /><span className="max-sm:sr-only">{t.chatHistory}</span></button>
            {turns.length > 0 && (
              <button type="button" onClick={newChat}
                className="inline-flex min-h-12 items-center gap-2 rounded-[14px] border-[1.5px] border-border-input bg-surface px-3 font-semibold hover:bg-surface-2">
                <Plus aria-hidden className="h-5 w-5" /><span className="max-sm:sr-only">{t.newChat}</span></button>
            )}
            <Link href="/ask/check" className="inline-flex min-h-12 items-center gap-2 rounded-[14px] border-[1.5px] border-border-input bg-surface px-4 font-semibold shadow-[var(--rim)] hover:bg-surface-2">
              <Stethoscope aria-hidden className="h-5 w-5" />{t.checkSymptoms}</Link>
            <Link href="/context" className="inline-flex min-h-12 items-center gap-2 rounded-[14px] border-[1.5px] border-border-input bg-surface px-3 font-semibold hover:bg-surface-2">
              {t.navContext}</Link>
            <button type="button" onClick={clearPatientProfile} title="Clear saved patient profile (name, age, demographics)"
              className="inline-flex min-h-12 items-center gap-1.5 rounded-[14px] border-[1.5px] border-border-input bg-surface px-2.5 text-[0.8125rem] font-semibold text-muted hover:border-danger/40 hover:bg-danger-soft/40 hover:text-danger">
              {profileCleared ? <Check aria-hidden className="h-4 w-4 text-ok" /> : <UserX aria-hidden className="h-4 w-4" />}
              <span className="max-sm:sr-only">{profileCleared ? "Profile cleared" : "Clear profile"}</span>
            </button>
            {desktop && (
              <button type="button" onClick={() => setInspector(!inspector)} aria-pressed={inspector} title={`${t.inspector} (i)`}
                className="inline-flex min-h-12 items-center gap-2 rounded-[14px] border-[1.5px] border-border-input bg-surface px-3 font-semibold hover:bg-surface-2">
                <PanelRight aria-hidden className="h-5 w-5" /><span className="max-[1279px]:sr-only">{t.inspector}</span></button>
            )}
          </div>
        </div>
        {!desktop && <ProfileStrip />}
        <Inspector open={desktop && inspector} onClose={() => setInspector(false)} profile={<ProfileStrip />}
          questions={turns.flatMap((x, i) => (x.role === "user" ? [{ i, text: x.text }] : []))}
          onJump={(i) => { setShowAll(true); requestAnimationFrame(() => document.querySelector(`[data-turn="${i}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" })); }}
          sources={(() => { const a = [...turns].reverse().find((x) => x.answer)?.answer; return [...(a?.citations ?? []), ...(a?.structured?.consulted ?? [])]; })()} />
        <Sheet open={historyOpen} onClose={() => setHistoryOpen(false)} title={t.chatHistory} closeLabel={t.close} placement="left">
          {historyOpen && (
            <div className="space-y-4">
              {turns.filter((x) => x.role === "user").length > 1 && (
                <section aria-label={t.inThisChat}>
                  <h3 className="mb-1.5 text-[0.875rem] font-semibold text-muted">{t.inThisChat}</h3>
                  <ol className="space-y-1">
                    {turns.map((x, i) => x.role === "user" && (
                      <li key={i}>
                        <button type="button" onClick={() => {
                          setHistoryOpen(false); setShowAll(true);
                          requestAnimationFrame(() => document.querySelector(`[data-turn="${i}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
                        }}
                          className="w-full truncate rounded-[10px] px-2 py-1.5 text-start text-[0.9375rem] hover:bg-surface-2">{x.text}</button>
                      </li>
                    ))}
                  </ol>
                </section>
              )}
              <ChatHistory current={sessionId} onNew={newChat} onPick={() => setHistoryOpen(false)} refreshKey={listKey} />
            </div>
          )}
        </Sheet>

        {turns.length === 0 && (
          <section className="space-y-5 rounded-[16px] border border-border bg-surface p-5 text-center shadow-[var(--e1)]">
            <VoiceOrb onText={(s) => send(s, true)} />
            <div className="text-start">
              <Suggestions sessionId={sessionId} refreshKey={listKey} onSend={(q) => send(q)} onEdit={editSuggestion} variant="grid" />
            </div>
            <div className="no-scrollbar -mx-5 flex gap-2 overflow-x-auto px-5 md:mx-0 md:flex-wrap md:px-0">
              {SYMPTOM_CHIPS.slice(0, 5).map((c) => (
                <Chip key={c.key} icon={c.icon} onClick={() => send(t.symptoms[c.key])}>{t.symptoms[c.key]}</Chip>
              ))}
            </div>
          </section>
        )}

        <section aria-live="polite" aria-label="Conversation" className="mt-4 space-y-4">
          {foldAt > 0 && (
            <div className="rounded-[16px] border border-dashed border-border p-3">
              <p className="mb-1.5 text-[0.875rem] font-semibold text-muted">{t.earlierInChat}</p>
              <ol className="space-y-1">
                {turns.slice(0, foldAt).map((x, i) => x.role === "user" && (
                  <li key={i}>
                    <button type="button" onClick={() => {
                      setShowAll(true); requestAnimationFrame(() =>
                        document.querySelector(`[data-turn="${i}"]`)?.scrollIntoView({ block: "start" }));
                    }}
                      className="flex w-full min-w-0 items-baseline gap-2 rounded-[10px] px-2 py-1.5 text-start hover:bg-surface-2">
                      <span className="shrink-0 font-semibold">{x.text.slice(0, 70)}{x.text.length > 70 ? "…" : ""}</span>
                      <span className="min-w-0 truncate text-[0.875rem] text-muted">{summaryOf(turns[i + 1]?.answer?.reply ?? turns[i + 1]?.text ?? "")}</span>
                    </button>
                  </li>
                ))}
              </ol>
              <button type="button" onClick={() => setShowAll(true)} className="mt-1.5 min-h-10 text-[0.875rem] font-semibold text-info underline">
                {fill(t.showEarlier, { n: foldAt })}</button>
            </div>
          )}
          {(voice.interim || voice.heard) && (voice.state === "listening" || voice.state === "transcribing" || voice.state === "confirm") && (
            <div className="ms-auto w-fit max-w-[85%] whitespace-pre-wrap break-words rounded-[18px] rounded-be-[4px] border border-border bg-rail px-4 py-2.5 text-rail-text">
              {voice.interim || voice.heard}</div>
          )}
          {turns.map((m, i) => i < foldAt ? null :
            m.role === "user" ? (
              <div key={i} data-turn={i} className="msg-in ms-auto flex max-w-[85%] scroll-mt-20 flex-col items-end">
                <div className="w-fit max-w-full space-y-2 whitespace-pre-wrap break-words rounded-[18px] rounded-be-[4px] border border-border bg-rail px-4 py-2.5 text-rail-text">
                  {!!m.photos?.length && (
                    <div className="flex flex-wrap gap-2">{m.photos.map((p) => <img key={p} src={p} alt="" className="max-h-40 rounded-[12px] object-cover" />)}</div>
                  )}{m.text}</div>
                {i === lastUser && !busy && (
                  <div className="mt-1 flex justify-end">
                    <button type="button" onClick={editLast} className="inline-flex min-h-12 items-center gap-1.5 rounded-full px-3 text-[0.9375rem] font-semibold text-muted hover:bg-surface-2 hover:text-text">
                      <Pencil aria-hidden className="h-4 w-4" />{t.editQuestion}</button>
                  </div>
                )}
              </div>
            ) : m.error ? (
              <div key={i}>
                <Banner tone="warning" action={i === turns.length - 1 && !busy ? (
                  <button type="button" onClick={askAgain} className="btn btn-outline"><RotateCcw aria-hidden className="h-5 w-5" />{t.retry}</button>
                ) : undefined}>{m.error}</Banner>
              </div>
            ) : (
              <div key={i} data-turn={i} data-last-answer={i === turns.length - 1 ? "" : undefined}
                className="msg-in scroll-mt-20 space-y-3 rounded-[16px] border border-border bg-surface p-4 shadow-[var(--e1)] sm:p-5">
                {m.redFlag && <RedFlagBanner rf={m.redFlag} />}
                {m.stopped ? (
                  <>
                    <p className="whitespace-pre-wrap text-[1.0625rem] leading-relaxed text-muted">{m.text}</p>
                    <Banner tone="warning">{t.answerStopped}</Banner>
                  </>
                ) : m.answer?.structured || !m.redFlag?.triggered ? <AnswerCard a={m.answer} onFollowUp={(q) => editSuggestion(q ? q + " " : "")} onAsk={(q) => send(q)} question={turns[i - 1]?.role === "user" ? turns[i - 1].text : undefined}
                  answered={i < turns.length - 1 || busy} /> : null}
                {i === turns.length - 1 && !busy && !m.error && (
                  <div className="border-t border-border pt-3">
                    <Suggestions sessionId={sessionId} refreshKey={listKey} onSend={(q) => send(q)} onEdit={editSuggestion} />
                  </div>
                )}
                {i === turns.length - 1 && !busy && (
                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
                    <div className="flex flex-wrap gap-2">
                      <button type="button" onClick={() => copyAnswer(m.text)} aria-live="polite"
                        className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.9375rem] font-semibold hover:bg-surface-2">
                        {copied ? <Check aria-hidden className="h-4 w-4 text-ok" /> : <Copy aria-hidden className="h-4 w-4" />}{copied ? t.copiedAnswer : t.copyAnswer}</button>
                      {lastUser >= 0 && (
                        <button type="button" onClick={askAgain}
                          className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.9375rem] font-semibold hover:bg-surface-2">
                          <RotateCcw aria-hidden className="h-4 w-4" />{t.regenerate}</button>
                      )}
                    </div>
                    {m.text && (() => {
                      const words = m.text.trim().split(/\s+/).filter(Boolean).length;
                      const sec = Math.max(5, Math.round((words / 180) * 60));
                      const script = langOfText(m.text, lang) === "hi" ? "हिन्दी" : "English";
                      return (
                        <span className="text-[0.75rem] font-medium text-muted">
                          {script} · {words} words · ~{sec >= 60 ? `${Math.round(sec / 60)}m` : `${sec}s`} read
                        </span>
                      );
                    })()}
                  </div>
                )}
              </div>
            ),
          )}
          {busy && (draft ? (
            <div className="space-y-2 rounded-[16px] border border-border bg-surface p-4 shadow-[var(--e1)] sm:p-5">
              <p className="flex items-center gap-2 text-[0.875rem] font-semibold text-muted">
                <Simmer />
                {t.writingDraft}</p>
              {/* the draft is not announced word by word; the finished answer is (aria-live on the conversation) */}
              <p aria-hidden className="whitespace-pre-wrap text-[1.0625rem] leading-relaxed">{draft}<span className="ms-0.5 inline-block h-[1.1em] w-[2px] translate-y-[3px] animate-pulse bg-text" /></p>
            </div>
          ) : (
            <div role="status" className="flex items-center gap-3 rounded-[18px] border border-border bg-surface p-4 font-semibold shadow-[var(--rim)]">
              <Simmer />{stage === "readingAttachment" ? t.readingAttachment : t[THINKING_KEYS[thinkIdx]]}
            </div>
          ))}
          <div ref={bottom} />
        </section>

        {/* Composer */}
        <form ref={composer} className="composer sticky bottom-[calc(5rem+env(safe-area-inset-bottom))] z-10 mt-4 md:bottom-4 desk:bottom-6" onSubmit={(e) => { e.preventDefault(); send(text); }}
          onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
          onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }}
          onDrop={(e) => { e.preventDefault(); setDragging(false); attach(Array.from(e.dataTransfer.files)); }}>
          {(newReply || (scrolledUp && turns.length > 2)) && (
            <div className="pointer-events-none mb-2 flex justify-center">
              <button type="button" onClick={toEnd}
                className="pop-in pointer-events-auto inline-flex min-h-10 items-center gap-1.5 rounded-full bg-brand px-4 text-[0.875rem] font-semibold text-brand-ink shadow-[var(--e2)] transition-transform hover:brightness-110 active:scale-95">
                <ArrowDown aria-hidden className="h-4 w-4" />{newReply ? t.newReply : "Jump to latest"}</button>
            </div>
          )}
          <div className={`relative rounded-[20px] border bg-surface px-2.5 py-1.5 shadow-[var(--rim),var(--e2)] transition-[border-color,box-shadow] duration-150 focus-within:border-brand focus-within:ring-2 focus-within:ring-brand-soft/60 ${dragging ? "border-2 border-brand" : "border-border"}`}>
            {dragging && (
              <p aria-hidden className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-[20px] bg-brand-soft/95 font-bold text-text">{t.dropToAttach}</p>
            )}
            {(files.length > 0 || uploading) && (
              <ul className="mb-1.5 flex flex-wrap gap-1.5 px-0.5">
                {files.map((f) => (
                  <li key={f.id} className="flex max-w-full items-center gap-1.5 rounded-[12px] border border-border/60 bg-surface-2 py-0.5 ps-1 pe-0.5">
                    {f.preview ? <img src={f.preview} alt="" className="h-8 w-8 rounded-[8px] object-cover" />
                      : <span aria-hidden className="grid h-8 w-8 place-items-center rounded-[8px] bg-surface"><Paperclip className="h-3.5 w-3.5" /></span>}
                    <span className="max-w-[9rem] truncate text-[0.8125rem] font-medium">{f.name}</span>
                    <button type="button" aria-label={`${t.removeAttachment}: ${f.name}`} onClick={() => { discardDoc(f.id); unattach(f.id); }}
                      className="grid h-7 w-7 place-items-center rounded-full text-muted transition-colors hover:bg-surface hover:text-text"><X aria-hidden className="h-3.5 w-3.5" /></button>
                  </li>
                ))}
                {uploading && <li className="flex min-h-8 items-center px-2 text-[0.8125rem] text-muted">{t.uploading}</li>}
              </ul>
            )}
            {about && (
              <div className="mb-1.5 space-y-1.5 px-0.5">
                <p className="flex items-center gap-2 rounded-full bg-info-soft py-0.5 ps-3 pe-1 text-[0.875rem] font-semibold text-info">
                  <span className="min-w-0 flex-1 truncate">{t.askingAbout}: {about.label}</span>
                  <button type="button" aria-label={t.removeAttachment} onClick={() => setAbout(null)}
                    className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-surface"><X aria-hidden className="h-3.5 w-3.5" /></button></p>
                {turns.length === 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {[t.qExplainThis, t.qIsThisNormal, t.qWhatShouldIDo].map((q) => (
                      <button key={q} type="button" onClick={() => send(q)}
                        className="min-h-9 rounded-full border border-border-input bg-surface px-3 text-[0.875rem] font-semibold transition-colors hover:bg-surface-2">{q}</button>))}
                  </div>
                )}
              </div>
            )}
            {attachErr && <p role="alert" className="mb-1 px-1 text-[0.875rem] font-semibold text-danger">{attachErr}</p>}
            {flagged?.issue && (
              <div className="mb-1.5 px-0.5">
                <ImageCheckBanner issue={flagged.issue}
                  onSend={() => setFiles((cur) => cur.map((f) => (f.id === flagged.id ? { ...f, issue: null } : f)))}
                  onRetake={(f) => { discardDoc(flagged.id); unattach(flagged.id); attach([f]); }} />
              </div>
            )}
            <GeminiLiveStatus live={live} />
            <ActionCard a={voice.actions} className="mb-1.5" />
            <HeardConfirm v={voice} className="mb-1.5" />
            {(voice.state === "listening" || voice.state === "transcribing") && (
              <div role="status" aria-live="polite" className="pop-in voice-active-ring mb-1.5 flex items-center justify-between gap-2 rounded-[14px] border border-brand/35 bg-brand-soft/45 px-3 py-1.5">
                <div className="flex min-w-0 items-center gap-2">
                  {voice.state === "listening" ? (
                    <>
                      <Waveform levels={voice.levels} className="w-24 shrink-0" />
                      <span className="truncate text-[0.875rem] font-semibold text-text">
                        {voice.interim || (voice.quietMic ? "Speak a little closer…" : t.listening)}
                      </span>
                    </>
                  ) : (
                    <span className="flex items-center gap-2 text-[0.875rem] font-semibold text-brand">
                      <Simmer />{t.uxProcessing}
                    </span>
                  )}
                </div>
                <span className="hidden shrink-0 rounded-full bg-surface/80 px-2 py-0.5 text-[0.7rem] font-semibold text-muted sm:inline">
                  Alt+M · Esc
                </span>
              </div>
            )}
            {(voice.notice || commands.notice) && <p aria-live="polite" className="px-1 pb-1 text-[0.875rem] font-semibold text-text">{voice.notice || commands.notice}</p>}
            {voice.error && <div className="flex justify-center pb-1"><VoiceError v={voice} /></div>}
            {speech.speaking && (
              <div role="status" aria-live="polite" className="pop-in voice-active-ring mb-1.5 space-y-1.5 rounded-[14px] border border-brand/35 bg-brand-soft/60 px-3 py-1.5 text-[0.8125rem]">
                <div className="flex items-center justify-between gap-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span aria-hidden className="eq-bars shrink-0 text-brand">
                      <span /><span /><span /><span /><span />
                    </span>
                    <span className="truncate font-semibold text-brand">
                      Speaking aloud{speech.gender ? ` · ${speech.gender === "male" ? "Male" : "Female"}` : ""}
                    </span>
                    {speech.engine && (
                      <span className="hidden truncate rounded-full bg-surface/80 px-2 py-0.5 text-[0.75rem] text-muted sm:inline">
                        {speech.engine}
                      </span>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1.5">
                    <button type="button" onClick={toggleCaptions} aria-pressed={captionsOn}
                      aria-label={captionsOn ? "Hide live captions" : "Show live captions"}
                      title={captionsOn ? "Live captions on" : "Live captions off"}
                      className={`grid h-7 w-7 place-items-center rounded-full transition-colors ${captionsOn ? "bg-brand text-brand-ink" : "bg-surface/80 text-muted hover:bg-surface hover:text-text"}`}>
                      <Captions aria-hidden className="h-3.5 w-3.5" />
                    </button>
                    <button type="button" onClick={toggleCues} aria-pressed={cuesOn}
                      aria-label={cuesOn ? "Mute voice audio chimes" : "Enable voice audio chimes"}
                      title={cuesOn ? "Audio cues on" : "Audio cues muted"}
                      className="grid h-7 w-7 place-items-center rounded-full bg-surface/80 text-muted transition-colors hover:bg-surface hover:text-text">
                      {cuesOn ? <Volume2 aria-hidden className="h-3.5 w-3.5" /> : <VolumeX aria-hidden className="h-3.5 w-3.5" />}
                    </button>
                    <button type="button" onClick={stopSpeaking}
                      className="inline-flex h-7 items-center gap-1 rounded-full bg-surface px-2.5 text-[0.75rem] font-semibold text-text shadow-[var(--rim)] transition-colors hover:bg-surface-2">
                      <Square aria-hidden className="h-2.5 w-2.5 fill-current" />{t.stop}
                    </button>
                  </div>
                </div>
                {captionsOn && speech.caption && (
                  <div className="space-y-1 border-t border-brand/20 pt-1">
                    <p className="line-clamp-2 text-[0.8125rem] font-medium leading-snug text-text">
                      {speech.caption}
                    </p>
                    <div aria-hidden className="h-1 w-full overflow-hidden rounded-full bg-surface/70">
                      <div className="caption-progress h-full w-full rounded-full bg-brand"
                        style={{ transform: `scaleX(${Math.max(0.04, speech.progress ?? 0)})` }} />
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Top tier: slim borderless prompt input */}
            <label htmlFor="ask-text" className="sr-only">{t.askPlaceholder}</label>
            <textarea id="ask-text" name="prompt" ref={box} rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder={t.askPlaceholder}
              maxLength={4000}
              className="w-full min-h-[36px] max-h-[140px] resize-none bg-transparent px-1 py-0.5 text-[1rem] leading-snug text-text placeholder:text-muted focus:outline-none"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
              onPaste={(e) => { const l = Array.from(e.clipboardData.files); if (l.length) { e.preventDefault(); attach(l); } }} />

            {/* Bottom toolbar: attachments & context controls (left) + voice & send (right) */}
            <div className="mt-1 flex flex-wrap items-center justify-between gap-1.5 border-t border-border/50 pt-1.5">
              <div className="flex flex-wrap items-center gap-1.5">
                <label htmlFor="ask-attach" className={`grid h-9 w-9 shrink-0 cursor-pointer place-items-center rounded-full bg-surface-2 text-text transition-colors hover:bg-border/60 ${files.length >= MAX_FILES ? "opacity-50" : ""}`} title={t.attachImage}>
                  <Paperclip aria-hidden className="h-4 w-4" /><span className="sr-only">{t.attachImage}</span>
                  <input id="ask-attach" name="attachments" type="file" multiple accept="image/*,application/pdf" className="sr-only" disabled={files.length >= MAX_FILES}
                    onChange={(e) => { const l = Array.from(e.target.files ?? []); e.target.value = ""; if (l.length) attach(l); }} />
                </label>
                <div role="group" aria-label={t.scopeChat} className="flex shrink-0 rounded-full bg-surface-2 p-0.5">
                  {(["chat", "record"] as const).map((s) => (
                    <button key={s} type="button" aria-pressed={scope === s} title={s === "chat" ? t.scopeChatHelp : t.scopeRecordHelp}
                      onClick={() => { setScope(s); try { window.localStorage.setItem("sushrut.askScope", s); } catch { /* ignore */ } }}
                      className={`min-h-8 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all ${scope === s ? "bg-surface text-text shadow-[var(--rim)]" : "text-muted hover:text-text"}`}>
                      {s === "chat" ? t.scopeChat : t.scopeRecord}</button>
                  ))}
                </div>
                <div role="group" aria-label="Speech & consultation mode" className="flex shrink-0 rounded-full bg-surface-2 p-0.5">
                  {(["general", "elderly"] as const).map((m) => (
                    <button key={m} type="button" aria-pressed={speechMode === m}
                      title={m === "elderly" ? "Patient, unhurried pauses (up to 25s) and simple everyday language" : "Standard pace and clinical detail"}
                      onClick={() => toggleSpeechMode(m)}
                      className={`min-h-8 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all ${speechMode === m ? "bg-surface text-text shadow-[var(--rim)]" : "text-muted hover:text-text"}`}>
                      {m === "general" ? "General" : "Elderly"}</button>
                  ))}
                </div>
                <div role="group" aria-label="Response style" className="flex shrink-0 rounded-full bg-surface-2 p-0.5">
                  {(["direct", "detailed"] as const).map((st) => (
                    <button key={st} type="button" aria-pressed={responseStyle === st}
                      title={st === "direct" ? "Direct, natural doctor-patient reply leading with takeaway & care" : "Detailed clinical explanation and full differential rationale"}
                      onClick={() => toggleResponseStyle(st)}
                      className={`min-h-8 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all ${responseStyle === st ? "bg-surface text-text shadow-[var(--rim)]" : "text-muted hover:text-text"}`}>
                      {st === "direct" ? "Direct" : "Detailed"}</button>
                  ))}
                </div>
                <ModelSwitch value={model} onChange={setModel} options={modelOptions} />
                <GeminiLiveButtons live={live} />
                <select
                  aria-label="Talk Live voice"
                  title={`Voice for ${lang.toUpperCase()}`}
                  value={liveVoice}
                  onChange={(e) => changeLiveVoice(e.target.value)}
                  className="h-8 rounded-full border border-border-input bg-surface-2 px-2.5 text-[0.75rem] font-semibold text-text focus:outline-none"
                >
                  <option value="Aoede">Aoede (Female)</option>
                  <option value="Kore">Kore (Female)</option>
                  <option value="Leda">Leda (Female)</option>
                  <option value="Zephyr">Zephyr (Female)</option>
                  <option value="Charon">Charon (Male)</option>
                  <option value="Puck">Puck (Male)</option>
                  <option value="Fenrir">Fenrir (Male)</option>
                </select>
              </div>

            <div className="flex items-center gap-1.5">
              {text.length > 240 && (
                <span className={`hidden text-[0.7rem] font-medium sm:inline ${text.length > 3600 ? "text-warn" : "text-muted"}`}>
                  {text.length}/4000
                </span>
              )}
              {text.trim().length > 0 && (
                <button type="button" onClick={() => { setText(""); box.current?.focus(); }} aria-label="Clear text" title="Clear text"
                  className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-text">
                  <X aria-hidden className="h-3.5 w-3.5" />
                </button>
              )}
              <button type="button" onClick={toggleHandsFree} aria-pressed={handsFree}
                aria-label={handsFree ? `${t.handsFreeOn}` : t.handsFree} title={t.handsFree}
                className={`grid h-9 w-9 shrink-0 place-items-center rounded-full transition-colors ${handsFree ? "bg-brand-soft text-brand" : "bg-surface-2 text-muted hover:bg-border/60 hover:text-text"}`}>
                <Headphones aria-hidden className="h-4 w-4" /></button>
              <button type="button" onClick={voice.toggle} aria-label={voice.state === "listening" ? t.listening : t.speak}
                aria-pressed={voice.state === "listening"} className="shrink-0 rounded-full">
                <VoiceWell state={voice.state} className="h-9 w-9">
                  {voice.state === "listening" ? <span className="h-3 w-3 rounded-sm bg-current" /> : <MicIcon />}
                </VoiceWell>
              </button>
              {busy ? (
                <button type="button" onClick={stop} aria-label={t.stopAnswer} title={t.stopAnswer}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full border-2 border-text bg-surface text-text transition-transform active:scale-95">
                  <Square aria-hidden className="h-3.5 w-3.5 fill-current" /></button>
              ) : (
                <button type="submit" disabled={uploading || !!flagged || (!text.trim() && !files.length)} aria-label={t.send}
                  className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand text-brand-ink shadow-sm transition-all hover:brightness-110 active:scale-95 disabled:opacity-40 disabled:hover:brightness-100">
                  <SendHorizonal aria-hidden className="h-4 w-4" /></button>
              )}
            </div>
          </div>

          {handsFree && (
            <div className="mt-1 space-y-1 border-t border-border/50 px-1 pt-1 text-[0.8125rem]">
              <p className="flex flex-wrap items-center gap-1.5 font-semibold">
                <span aria-hidden className="h-2 w-2 animate-pulse rounded-full bg-brand" />
                {hfState === "speech"
                  ? t.uxListening
                  : hfState === "waiting_for_continuation" || hfState === "pause_detected"
                    ? t.uxWaiting
                    : hfState === "transcribing"
                      ? t.uxProcessing
                      : hfState === "session_paused"
                        ? t.uxPaused
                        : t.handsFreeOn}
                <button type="button" onClick={() => { stopSpeaking(); abort.current?.abort(); if (handsFree) loop?.toggle(); }} className="ms-2 text-brand underline">{t.stop}</button>
              </p>
              {loop?.partialText && (
                <p aria-live="polite" className="rounded-[8px] bg-surface-2 px-2 py-0.5 text-[0.8125rem] italic text-muted">
                  “{loop.partialText}…”
                </p>
              )}
            </div>
          )}
      </div>
    </form>
      </div >
    </div >
  );
}

/** Thinking: three katoris simmering in turn. */
function Simmer() {
  return (
    <span aria-hidden className="simmer flex shrink-0 gap-1.5"><span /><span /><span /></span>
  );
}

function MicIcon() {
  return <svg aria-hidden viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4" /></svg>;
}

export default function Ask() {
  return <Suspense><AskInner /></Suspense>;
}
