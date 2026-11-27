"use client";

import { AudioLines, Loader2, MessageCircle, Square } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useSOS } from "@/components/Emergency";
import { type GeminiLiveState, type LiveAnswerPayload, useGeminiLive } from "@/components/GeminiLive";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type LiveTalkCtx = {
  live: GeminiLiveState;
  getCachedTurns: (sid: string | null | undefined) => any[] | null;
  setCachedTurns: (sid: string | null | undefined, turns: any[]) => void;
  clearCachedTurns: (sid?: string | null) => void;
  setContextBridge: (ctx: {
    sessionId?: string | null;
    useRecord?: boolean;
    speechMode?: "general" | "elderly";
    responseStyle?: "direct" | "detailed";
    voiceName?: string;
    history?: { role: string; content: string }[];
    onAnswer?: (payload: LiveAnswerPayload) => void;
  }) => void;
};

const Ctx = createContext<LiveTalkCtx | null>(null);

const TURNS_KEY = (sid: string) => `sushrut.turns.${sid}`;

export function LiveTalkProvider({ children }: { children: ReactNode }) {
  const { lang } = useI18n();
  const path = usePathname();
  const { openSOS } = useSOS();
  const [sosEnabled, setSosEnabled] = useState(true);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  sessionIdRef.current = sessionId;
  const [useRecord, setUseRecord] = useState(false);
  const [speechMode, setSpeechMode] = useState<"general" | "elderly">("general");
  const [responseStyle, setResponseStyle] = useState<"direct" | "detailed">("direct");
  const [voiceName, setVoiceName] = useState<string>("Aoede");
  const [history, setHistory] = useState<{ role: string; content: string }[]>([]);
  const onAnswerRef = useRef<((payload: LiveAnswerPayload) => void) | undefined>(undefined);
  const cachedTurnsRef = useRef<Record<string, any[]>>({});

  const getCachedTurns = useCallback((sid: string | null | undefined): any[] | null => {
    if (!sid) return null;
    if (cachedTurnsRef.current[sid]?.length) return cachedTurnsRef.current[sid];
    try {
      const raw = window.sessionStorage.getItem(TURNS_KEY(sid));
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length > 0) {
          cachedTurnsRef.current[sid] = parsed;
          return parsed;
        }
      }
    } catch { /* ignore */ }
    return null;
  }, []);

  const setCachedTurns = useCallback((sid: string | null | undefined, turns: any[]) => {
    if (!sid) return;
    cachedTurnsRef.current[sid] = turns;
    try {
      window.sessionStorage.setItem(TURNS_KEY(sid), JSON.stringify(turns.slice(-60)));
    } catch { /* ignore */ }
  }, []);

  const clearCachedTurns = useCallback((sid?: string | null) => {
    if (sid) {
      delete cachedTurnsRef.current[sid];
      try { window.sessionStorage.removeItem(TURNS_KEY(sid)); } catch { /* ignore */ }
    }
  }, []);

  useEffect(() => {
    try {
      const savedSid = window.sessionStorage.getItem("sushrut.activeSessionId");
      if (savedSid) {
        setSessionId(savedSid);
        const savedTurns = getCachedTurns(savedSid);
        if (savedTurns?.length) {
          setHistory(savedTurns.map((m: any) => ({ role: m.role, content: m.text })));
        }
      }
      const savedScope = window.localStorage.getItem("sushrut.askScope");
      if (savedScope === "record") setUseRecord(true);
      const savedMode = window.localStorage.getItem("sushrut.speechMode");
      if (savedMode === "elderly" || savedMode === "general") setSpeechMode(savedMode);
      const savedStyle = window.localStorage.getItem("sushrut.responseStyle");
      if (savedStyle === "direct" || savedStyle === "detailed") setResponseStyle(savedStyle);
    } catch { /* ignore */ }
    api<{ sos_enabled?: boolean; speech_mode?: "general" | "elderly"; response_style?: "direct" | "detailed" }>("/me/settings")
      .then((r) => {
        setSosEnabled(r.sos_enabled !== false);
        if (r.speech_mode === "elderly" || r.speech_mode === "general") setSpeechMode(r.speech_mode);
        if (r.response_style === "direct" || r.response_style === "detailed") setResponseStyle(r.response_style);
      })
      .catch(() => {});
  }, [getCachedTurns]);

  const live = useGeminiLive({
    lang,
    sessionId,
    useRecord,
    speechMode,
    responseStyle,
    voiceName,
    history,
    onEmergency: () => { if (sosEnabled) openSOS(); },
    onAnswer: (payload) => {
      const sid = sessionIdRef.current;
      if (sid) {
        const prev = getCachedTurns(sid) ?? [];
        const nextTurns = [
          ...prev,
          ...(payload.question ? [{ role: "user", text: payload.question }] : []),
          {
            role: "assistant",
            text: payload.answer.reply || payload.answer.text || "",
            answer: payload.answer,
            redFlag: payload.answer.red_flag?.triggered ? payload.answer.red_flag : undefined,
          },
        ];
        setCachedTurns(sid, nextTurns);
        setHistory(nextTurns.map((m: any) => ({ role: m.role, content: m.text })));
      }
      onAnswerRef.current?.(payload);
      window.dispatchEvent(new CustomEvent("sushrut:context-updated", { detail: payload }));
    },
  });

  const setContextBridge = useCallback((ctx: {
    sessionId?: string | null;
    useRecord?: boolean;
    speechMode?: "general" | "elderly";
    responseStyle?: "direct" | "detailed";
    voiceName?: string;
    history?: { role: string; content: string }[];
    onAnswer?: (payload: LiveAnswerPayload) => void;
  }) => {
    if (ctx.sessionId !== undefined && ctx.sessionId !== null) setSessionId(ctx.sessionId);
    if (ctx.useRecord !== undefined) setUseRecord(ctx.useRecord);
    if (ctx.speechMode !== undefined) setSpeechMode(ctx.speechMode);
    if (ctx.responseStyle !== undefined) setResponseStyle(ctx.responseStyle);
    if (ctx.voiceName !== undefined) setVoiceName(ctx.voiceName);
    if (ctx.history !== undefined) setHistory(ctx.history);
    if (ctx.onAnswer !== undefined) onAnswerRef.current = ctx.onAnswer;
  }, []);

  const finishLabel = lang === "bn" ? "বলা শেষ" : lang === "hi" ? "बात पूरी" : "Finished";
  const showFloating = !!live.active && !path.startsWith("/ask");

  return (
    <Ctx.Provider value={{ live, getCachedTurns, setCachedTurns, clearCachedTurns, setContextBridge }}>
      {children}
      {showFloating && (
        <aside
          aria-label="Floating Talk Live"
          className="fixed bottom-20 right-4 z-40 w-80 max-w-[calc(100vw-2rem)] rounded-[22px] border border-brand/35 bg-surface/95 p-3.5 shadow-[0_14px_40px_rgba(15,23,42,0.18)] backdrop-blur-md transition-all sm:bottom-6 sm:right-6"
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2.5">
              <span className="relative flex h-9 w-9 items-center justify-center rounded-full bg-brand text-brand-ink">
                <span aria-hidden className="absolute inset-0 animate-ping rounded-full bg-brand/40" />
                {live.processing ? (
                  <Loader2 aria-hidden className="relative h-4 w-4 animate-spin" />
                ) : (
                  <AudioLines aria-hidden className="relative h-4 w-4" />
                )}
              </span>
              <div>
                <p className="text-[0.8125rem] font-bold text-text">
                  {live.processing ? "Thinking…" : "Talk Live Active"}
                </p>
                <p className="text-[0.7rem] font-medium text-brand">
                  {speechMode === "elderly" ? "Elderly Mode · Unhurried" : "Listening across tabs"}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={live.stop}
              aria-label="Stop Talk Live"
              className="grid h-8 w-8 place-items-center rounded-full bg-danger-soft/70 text-danger hover:bg-danger-soft"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
            </button>
          </div>

          {(live.heard || live.line) && (
            <div className="mt-2.5 max-h-24 space-y-1 overflow-y-auto rounded-[12px] bg-surface-2/80 px-2.5 py-2 text-[0.75rem]">
              {live.heard && <p className="text-muted"><strong className="text-text">You:</strong> {live.heard}</p>}
              {live.line && <p className="font-medium text-text"><strong className="text-brand">AI:</strong> {live.line}</p>}
            </div>
          )}

          <div className="mt-2.5 flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={live.finishSpeaking}
              className="rounded-full border border-brand/35 bg-brand-soft/60 px-3 py-1 text-[0.75rem] font-semibold text-brand hover:bg-brand-soft"
            >
              {finishLabel}
            </button>
            <Link
              href="/ask"
              className="inline-flex items-center gap-1 rounded-full bg-brand px-3 py-1 text-[0.75rem] font-semibold text-brand-ink"
            >
              <MessageCircle className="h-3.5 w-3.5" /> Open Chat
            </Link>
          </div>
        </aside>
      )}
    </Ctx.Provider>
  );
}

export function useLiveTalk() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLiveTalk must be used inside LiveTalkProvider");
  return v;
}
