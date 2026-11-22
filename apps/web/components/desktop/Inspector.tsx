"use client";

import { BookOpen, ListOrdered, X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { useI18n } from "@/lib/i18n";

type Src = { title: string; url?: string | null };

/** The assistant's right-hand inspector (docs/DESKTOP_UI_SPEC.md §4.2): who the answers are for, the outline of this
 *  chat (jump to a question) and the trusted pages behind the last answer. A drawer that slides in from the right;
 *  Esc or "i" closes it. */
export function Inspector({ open, onClose, profile, questions, onJump, sources }: {
  open: boolean; onClose: () => void; profile: ReactNode; questions: { i: number; text: string }[];
  onJump: (i: number) => void; sources: Src[];
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!open) return;
    ref.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <aside ref={ref} tabIndex={-1} aria-label={t.inspector}
      className="d-pop d-drawer-in fixed bottom-6 right-6 top-24 z-40 flex w-[360px] flex-col overflow-hidden min-[1600px]:w-[400px]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--d-tile-line)] px-4 py-3">
        <h2 className="text-[1.0625rem] font-bold">{t.inspector}</h2>
        <button type="button" aria-label={`${t.close} (i)`} onClick={onClose} className="grid h-10 w-10 place-items-center rounded-full hover:bg-[var(--d-tile-hover)]"><X aria-hidden className="h-4 w-4" /></button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {profile}
        <section aria-label={t.inThisChat}>
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[0.875rem] font-semibold text-muted"><ListOrdered aria-hidden className="h-4 w-4" />{t.inThisChat}</h3>
          {questions.length === 0 ? <p className="text-[0.9375rem] text-muted">—</p> : (
            <ol className="space-y-0.5">
              {questions.map((q) => (
                <li key={q.i}><button type="button" onClick={() => onJump(q.i)}
                  className="w-full truncate rounded-[10px] px-2 py-1.5 text-left text-[0.9375rem] hover:bg-[var(--d-tile-hover)]">{q.text}</button></li>
              ))}
            </ol>
          )}
        </section>
        <section aria-label={t.sources}>
          <h3 className="mb-1.5 flex items-center gap-1.5 text-[0.875rem] font-semibold text-muted"><BookOpen aria-hidden className="h-4 w-4" />{t.sources}</h3>
          {sources.length === 0 ? <p className="text-[0.9375rem] text-muted">—</p> : (
            <ul className="space-y-1.5">
              {sources.map((s, i) => (
                <li key={i} className="text-[0.9375rem]">{s.url
                  ? <a href={s.url} target="_blank" rel="noreferrer" className="text-info underline underline-offset-4">{s.title}</a> : s.title}</li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </aside>
  );
}
