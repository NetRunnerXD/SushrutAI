"use client";

import { Pencil, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Res = { items: string[]; source: "model" | "rules"; ms: number };

/** "Ask next" chips personalised from the person's data and the last answer (GET /chat/suggestions).
 *  Tap = send now. The pencil (or a long-press / right-click) puts the text in the box to edit first. */
export function Suggestions({ sessionId, refreshKey, onSend, onEdit, variant = "row" }: {
  sessionId: string | null; refreshKey: number; onSend: (q: string) => void; onEdit: (q: string) => void;
  variant?: "row" | "grid";
}) {
  const { t, lang } = useI18n();
  const [res, setRes] = useState<Res | null>(null);
  const press = useRef<number | null>(null);
  const [writing, setWriting] = useState(false);
  useEffect(() => {
    // Two steps: instant chips from the person's data, then the small model's chips for this conversation (~2 s)
    let live = true;
    setRes(null);
    const q = (mode: string) => new URLSearchParams({ lang, mode, ...(sessionId ? { session_id: sessionId } : {}) });
    api<Res>(`/chat/suggestions?${q("rules")}`).then((r) => live && setRes((cur) => cur?.source === "model" ? cur : r))
      .catch(() => live && setRes((cur) => cur ?? { items: [], source: "rules", ms: 0 }));
    setWriting(true);
    api<Res>(`/chat/suggestions?${q("model")}`).then((r) => { if (live && r.source === "model" && r.items.length) setRes(r); })
      .catch(() => {}).finally(() => live && setWriting(false));
    return () => { live = false; };
  }, [sessionId, refreshKey, lang]);

  if (res && !res.items.length) return null;
  const startPress = (q: string) => { press.current = window.setTimeout(() => { press.current = -1; onEdit(q); }, 550); };
  const endPress = () => { if (press.current && press.current > 0) clearTimeout(press.current); };

  return (
    <section aria-label={t.suggestedForYou} className="space-y-1.5">
      <p className="flex items-center gap-1.5 text-[0.875rem] font-semibold text-muted"><Sparkles aria-hidden className={`h-4 w-4 ${writing ? "animate-pulse text-text" : ""}`} />{t.suggestedForYou}
        {writing && <span className="sr-only">…</span>}</p>
      <ul className={variant === "grid" ? "grid gap-2 sm:grid-cols-2" : "flex flex-wrap gap-2"}>
        {!res ? [0, 1, 2].map((i) => <li key={i} className="skeleton h-11 w-44 rounded-full" />) : res.items.map((q) => (
          <li key={q} className={`chip-in flex min-w-0 items-stretch overflow-hidden ${variant === "grid" ? "rounded-[16px]" : "rounded-full"} border-[1.5px] border-border-input bg-surface shadow-[var(--rim)]`}>
            <button type="button"
              onClick={() => { if (press.current === -1) { press.current = null; return; } onSend(q); }}
              onPointerDown={() => startPress(q)} onPointerUp={endPress} onPointerLeave={endPress}
              onContextMenu={(e) => { e.preventDefault(); onEdit(q); }}
              className={`min-h-12 min-w-0 flex-1 px-3.5 text-start text-[0.9375rem] font-semibold hover:bg-surface-2 ${variant === "grid" ? "py-2" : ""}`}>{q}</button>
            <button type="button" onClick={() => onEdit(q)} aria-label={`${t.editSuggestion}: ${q}`} title={t.editSuggestion}
              className="grid w-12 shrink-0 place-items-center border-s border-border text-muted hover:bg-surface-2 hover:text-text">
              <Pencil aria-hidden className="h-3.5 w-3.5" /></button>
          </li>
        ))}
      </ul>
    </section>
  );
}
