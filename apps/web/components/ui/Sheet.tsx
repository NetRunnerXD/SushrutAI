"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

/** Accessible bottom sheet (mobile) / centred dialog (desktop): focus trap, Esc/back to close, focus restore. */
export function Sheet({ open, onClose, title, children, tone = "default", closeLabel = "Close", placement = "center" }: {
  open: boolean; onClose: () => void; title: ReactNode; children: ReactNode;
  tone?: "default" | "emergency"; closeLabel?: string;
  placement?: "center" | "left";  // left: a floating window at the side on wider screens (e.g. the chats list)
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  // Callers pass a new onClose on every render; keep the latest in a ref so the focus setup below runs only when
  // the sheet opens (re-running it on each keystroke moved focus back to the first button).
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const onClose = () => closeRef.current();
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const focusables = () => Array.from(el?.querySelectorAll<HTMLElement>(
      'a[href],button:not([disabled]),input,select,textarea,[tabindex]:not([tabindex="-1"])') ?? []);
    focusables()[0]?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); }
      if (e.key === "Tab") {
        const f = focusables();
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; prev?.focus(); };
  }, [open]);
  if (!open) return null;
  return (
    <div className={`fixed inset-0 z-50 flex items-end justify-center ${placement === "left" ? "sm:items-start sm:justify-start sm:p-4 sm:pt-20 lg:pl-28" : "sm:items-center"}`} role="presentation">
      <div className="sheet-scrim absolute inset-0 bg-[rgb(12_16_20/60%)]" onClick={onClose} aria-hidden />
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId}
           className={`sheet-in relative z-10 max-h-[92dvh] w-full overflow-y-auto rounded-t-[22px] bg-surface p-5 pb-safe shadow-[var(--rim),var(--e3)]
             sm:max-w-lg sm:rounded-[22px] ${placement === "left" ? "sm:max-h-[calc(100dvh-6rem)] sm:max-w-md" : ""} ${tone === "emergency" ? "border-2 border-emergency" : "border border-border"}`}>
        <div aria-hidden className="well mx-auto mb-3 h-1.5 w-12 rounded-full sm:hidden" />
        <div className="mb-4 flex items-start justify-between gap-3">
          <h2 id={titleId} className={tone === "emergency" ? "text-danger" : ""}>{title}</h2>
          <button onClick={onClose} aria-label={closeLabel}
                  className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-surface-2 hover:bg-border"><X aria-hidden className="h-6 w-6" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}
