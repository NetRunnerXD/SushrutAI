"use client";

import { ChevronDown } from "lucide-react";
import { useId, type ReactNode } from "react";
import { usePrefs } from "@/lib/prefs";

/** One collapsible part of an answer. Open/closed is remembered per section kind on this device (prefs.sections);
 *  until the person changes it, `defaultOpen` decides (e.g. Next steps opens by itself when the answer is urgent). */
export function AnswerSection({ id, icon: Icon, title, summary, defaultOpen = true, tone = "", children }: {
  id: string; icon: any; title: string; summary?: ReactNode; defaultOpen?: boolean; tone?: string; children: ReactNode;
}) {
  const { prefs, setPrefs } = usePrefs();
  const open = prefs.sections?.[id] ?? defaultOpen;
  const body = useId();
  return (
    <section className={`rounded-[18px] border border-border bg-surface-2/60 ${tone}`}>
      <h3>
        <button type="button" aria-expanded={open} aria-controls={body}
          onClick={() => setPrefs({ sections: { ...(prefs.sections ?? {}), [id]: !open } })}
          className="flex min-h-12 w-full items-center gap-2.5 rounded-[18px] px-3.5 py-2 text-start hover:bg-surface-2">
          <span aria-hidden className="well grid h-8 w-8 shrink-0 place-items-center rounded-full"><Icon className="h-[18px] w-[18px]" /></span>
          <span className="flex-1 font-semibold">{title}</span>
          {summary != null && <span className="text-[0.875rem] font-normal text-muted">{summary}</span>}
          <ChevronDown aria-hidden className={`h-5 w-5 shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
      </h3>
      {open && <div id={body} className="expand-in space-y-3 px-3.5 pb-3.5">{children}</div>}
    </section>
  );
}

/** Small pill-shaped action inside a section. */
export function SectionAction({ icon: Icon, children, onClick, href, done, disabled }: {
  icon: any; children: ReactNode; onClick?: () => void; href?: string; done?: boolean; disabled?: boolean;
}) {
  const cls = `inline-flex min-h-12 items-center gap-1.5 rounded-full border-[1.5px] px-3 text-[0.875rem] font-semibold
    ${done ? "border-ok/50 bg-ok-soft text-ok" : "border-border-input bg-surface hover:bg-surface-2"} disabled:opacity-60`;
  if (href) return <a href={href} target={href.startsWith("http") ? "_blank" : undefined} rel="noreferrer" className={cls}><Icon aria-hidden className="h-4 w-4" />{children}</a>;
  return <button type="button" onClick={onClick} disabled={disabled || done} aria-pressed={done} className={cls}><Icon aria-hidden className="h-4 w-4" />{children}</button>;
}
