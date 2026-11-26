"use client";

import { Languages, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { startGeminiLive, type LiveHandle } from "@/lib/geminiLive";
import { useI18n } from "@/lib/i18n";
import { voicePrefs } from "@/lib/voice";

/** Interpret (speech-to-speech) and Talk live (3.8 or legacy Flash). Shown when a Gemini key is configured. */
export function GeminiLiveBar({ lang, onEmergency, onAnswer }: {
  lang: string;
  onEmergency: () => void;
  onAnswer: (reply: string) => void;
}) {
  const { t } = useI18n();
  const [voices, setVoices] = useState<string[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [line, setLine] = useState("");
  const [heard, setHeard] = useState("");
  const [err, setErr] = useState("");
  const handleRef = useRef<LiveHandle | null>(null);

  useEffect(() => {
    api<{ live_voices?: string[] }>("/voice/engines").then((r) => setVoices(r.live_voices ?? [])).catch(() => {});
  }, []);

  const stop = () => { handleRef.current?.stop(); handleRef.current = null; setActive(null); };

  const start = async (mode: "translate" | "talk", model?: string) => {
    if (handleRef.current) stop();
    setErr(""); setLine(""); setHeard("");
    try {
      const c = await api<{ active: string[] }>("/consents").catch(() => ({ active: [] as string[] }));
      if (!c.active.includes("cloud_llm_live")) {
        if (!window.confirm(t.liveConsent)) return;
        await api("/consents", { method: "POST", body: JSON.stringify({ purposes: ["cloud_llm_live"] }) });
      }
      const h = await startGeminiLive({
        mode, model, lang, targetLang: lang,
        onEvent: (ev) => {
          if (ev.type === "interim" || ev.type === "input_transcript" || ev.type === "final") setHeard(ev.text || "");
          if (ev.type === "output_transcript") setLine(ev.text || "");
          if (ev.type === "answer" && ev.text) onAnswer(ev.text);
          if (ev.type === "red_flag") { setLine(ev.text || ""); onEmergency(); }
          if (ev.type === "error") { setErr(ev.message || t.liveStopped); stop(); }
        },
      });
      handleRef.current = h;
      setActive(model || mode);
    } catch {
      setErr(t.liveStopped);
    }
  };

  if (!voices.length) return null;
  const talk = voicePrefs().talk;
  return (
    <div className="mb-2 space-y-2 px-1">
      <div className="flex flex-wrap gap-2">
        {voices.includes("gemini_translate") && (
          <button type="button" onClick={() => active === "translate" ? stop() : start("translate")}
            aria-pressed={active === "translate"}
            className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-[0.875rem] font-semibold ${active === "translate" ? "bg-brand-soft" : "bg-surface-2"}`}>
            <Languages aria-hidden className="h-4 w-4" />{t.liveInterpret}</button>
        )}
        {voices.includes("gemini_talk") && talk !== "gemini_flash" && (
          <button type="button" onClick={() => active === "talk" ? stop() : start("talk")}
            aria-pressed={active === "talk"}
            className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-[0.875rem] font-semibold ${active === "talk" ? "bg-brand-soft" : "bg-surface-2"}`}>
            {t.liveTalk}</button>
        )}
        {voices.includes("gemini_flash") && talk === "gemini_flash" && (
          <button type="button" onClick={() => active === "flash" ? stop() : start("talk", "flash")}
            aria-pressed={active === "flash"}
            className={`inline-flex min-h-11 items-center gap-1.5 rounded-full px-3 text-[0.875rem] font-semibold ${active === "flash" ? "bg-brand-soft" : "bg-surface-2"}`}>
            {t.liveFlash}</button>
        )}
        {handleRef.current && active && (
          <button type="button" onClick={stop} className="inline-flex min-h-11 items-center gap-1 rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">
            <Square aria-hidden className="h-3.5 w-3.5" />{t.stop}</button>
        )}
      </div>
      {(heard || line) && (
        <p aria-live="polite" className="text-[0.9375rem] text-muted">
          {heard && <span className="block">{heard}</span>}
          {line && <span className="block font-semibold text-text">{line}</span>}
        </p>
      )}
      {err && <p role="alert" className="text-[0.9375rem] font-semibold text-danger">{err}</p>}
    </div>
  );
}
