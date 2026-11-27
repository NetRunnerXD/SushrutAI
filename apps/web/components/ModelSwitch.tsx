"use client";

import { Check, ChevronDown, Gauge } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export type ModelPref = "auto" | "balanced" | "fast" | "instant" | "cloud" | "gemini" | "gemini_fast";
export const CHAT_MODEL_KEY = "sushrut.chatModel";
const KEY = CHAT_MODEL_KEY;

/** The chat's model switch: Auto (picks per question) / Careful / Faster / Instant. Remembered on this device and
 *  sent with each message; Settings keeps the default for new devices. */
export function useChatModel(): [ModelPref, (m: ModelPref) => void, ModelPref[]] {
  const [m, setM] = useState<ModelPref>("auto");
  const [opts, setOpts] = useState<ModelPref[]>(["auto", "balanced"]);
  useEffect(() => {
    let saved: ModelPref | null = null;
    try { saved = localStorage.getItem(KEY) as ModelPref | null; } catch { /* ignore */ }
    api<{ model_pref: ModelPref; model_options: ModelPref[] }>("/me/settings").then((s) => {
      const options: ModelPref[] = s.model_options?.length ? s.model_options : ["auto", "balanced"];
      setOpts(options);
      // The account setting and this device stay the same choice. A value saved only on the device is used
      // when the account has none that this server still offers.
      const fromServer = s.model_pref && options.includes(s.model_pref) ? s.model_pref : null;
      const next = fromServer ?? (saved && options.includes(saved) ? saved : "auto");
      setM(next);
      try { localStorage.setItem(KEY, next); } catch { /* ignore */ }
    }).catch(() => saved && setM(saved));
  }, []);
  const set = (v: ModelPref) => { setM(v); try { localStorage.setItem(KEY, v); } catch { /* ignore */ } };
  return [m, set, opts];
}

export function ModelSwitch({ value, onChange, options }: { value: ModelPref; onChange: (m: ModelPref) => void; options: ModelPref[] }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const LABEL: Record<ModelPref, [string, string]> = {
    auto: [t.modelAuto, t.modelAutoHelp], balanced: [t.modelBalanced, t.modelBalancedHelp], fast: [t.modelFast, t.modelFastHelp],
    instant: [t.modelInstant, t.modelInstantHelp], cloud: [t.modelCloud, t.modelCloudHelp],
    gemini: [t.modelGemini, t.modelGeminiHelp], gemini_fast: [t.modelGeminiFast, t.modelGeminiFastHelp],
  };
  const choose = async (o: ModelPref) => {
    try {
      if (o === "cloud" || o.startsWith("gemini")) {
        const purpose = o === "cloud" ? "cloud_llm_deidentified" : "cloud_llm_full";
        const c = await api<{ active: string[] }>("/consents").catch(() => ({ active: [] as string[] }));
        if (!c.active.includes(purpose)) {
          if (!window.confirm(o === "cloud" ? t.cloudConsent : t.geminiConsent)) return;
          await api("/consents", { method: "POST", body: JSON.stringify({ purposes: [purpose] }) });
        }
      }
      onChange(o);
      await api("/me/settings", { method: "PUT", body: JSON.stringify({ model_pref: o }) }).catch(() => {});
    } catch { /* consent failed: keep the previous model */ }
  };
  return (
    <div ref={ref} className="relative">
      <button type="button" onClick={() => setOpen(!open)} aria-haspopup="listbox" aria-expanded={open}
        aria-label={`${t.answerSpeed}: ${LABEL[value][0]}`}
        className="inline-flex h-9 shrink-0 items-center gap-1 rounded-full bg-surface-2 px-2.5 text-[0.75rem] font-semibold transition-colors hover:bg-border/60">
        <Gauge aria-hidden className="h-3.5 w-3.5" /><span className="max-sm:sr-only">{LABEL[value][0]}</span>
        <ChevronDown aria-hidden className="h-3.5 w-3.5" /></button>
      {open && (
        <ul role="listbox" aria-label={t.answerSpeed}
          className="pop-in absolute bottom-11 start-0 z-40 max-h-[min(55vh,320px)] w-72 max-w-[calc(100vw-2rem)] space-y-1 overflow-y-auto overscroll-contain rounded-[16px] border border-border bg-surface p-1.5 shadow-[var(--rim),var(--e3)]">
          {options.map((o) => (
            <li key={o} role="option" aria-selected={o === value}>
              <button type="button" onClick={() => { choose(o); setOpen(false); }}
                className={`flex w-full items-start gap-2 rounded-[12px] p-2.5 text-start ${o === value ? "bg-brand-soft" : "hover:bg-surface-2"}`}>
                <Check aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${o === value ? "" : "invisible"}`} />
                <span><span className="block font-semibold">{LABEL[o][0]}</span>
                  <span className="block text-[0.8125rem] leading-snug text-muted">{LABEL[o][1]}</span></span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
