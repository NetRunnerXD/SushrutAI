import type { LucideIcon } from "lucide-react";
import { Illustration, type ArtName } from "./Illustrations";
import type { CSSProperties, ReactNode } from "react";

/** A pinned note: paper card that settles onto the page when it first appears (index `i` staggers a group). */
export function Card({ children, className = "", as: As = "section", i, style, settle = true, ...rest }: {
  children: ReactNode; className?: string; as?: "section" | "div" | "article" | "li"; i?: number;
  style?: CSSProperties; settle?: boolean; [k: string]: unknown;
}) {
  return (
    <As {...rest} style={i != null ? { ...style, ["--i" as string]: i } : style}
      className={`${settle ? "note" : ""} rounded-[6px] border border-border bg-surface p-4 shadow-[var(--e1)] sm:p-5 ${className}`}>
      {children}
    </As>
  );
}

export function CardHeader({ icon: Icon, title, action, sub, level = 2 }: {
  icon?: LucideIcon; title: ReactNode; action?: ReactNode; sub?: ReactNode; level?: 2 | 3;
}) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <div className="mb-3 flex items-start justify-between gap-3">
      <div className="flex min-w-0 items-start gap-3">
        {Icon && (
          <span aria-hidden className="grid h-10 w-10 shrink-0 place-items-center rounded-[46%_54%_50%_50%/52%_48%_52%_48%] bg-brand-soft text-brand">
            <Icon className="h-5 w-5" />
          </span>
        )}
        <div className="min-w-0">
          <H className={`leading-snug ${level === 2 ? "text-[1.1875rem]" : "text-[1.0625rem]"}`}>{title}</H>
          {sub && <p className="text-[0.9375rem] text-muted">{sub}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}

/** A hand-drawn turmeric underline that inks itself in once. Decorative. */
export function Scribble({ className = "" }: { className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 220 14" preserveAspectRatio="none" className={`block h-3 w-full max-w-[14rem] ${className}`}>
      <path pathLength={1} className="ink-draw" d="M3 9.5C38 4.5 74 3.2 112 5.4c30 1.7 58 .9 78-1.6 9-1 17-.6 27 1.2"
        fill="none" stroke="var(--accent)" strokeWidth="4" strokeLinecap="round" />
      <path pathLength={1} className="ink-draw [animation-delay:0.5s]" d="M40 11.5c40-2.2 92-2.6 140-.6"
        fill="none" stroke="var(--accent)" strokeWidth="2.2" strokeLinecap="round" opacity="0.7" />
    </svg>
  );
}

export function EmptyState({ icon: Icon, text, action, art }: { icon: LucideIcon; text: ReactNode; action?: ReactNode; art?: ArtName }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-[6px] border-[1.5px] border-dashed border-border-input bg-surface/60 p-6 text-center">
      {art ? <Illustration name={art} /> : (
        <span aria-hidden className="grid h-14 w-14 place-items-center rounded-full bg-brand-soft text-brand">
          <Icon className="h-7 w-7" />
        </span>
      )}
      <p className="max-w-sm text-muted">{text}</p>
      {action}
    </div>
  );
}

export function Skeleton({ className = "h-5 w-full" }: { className?: string }) {
  return <div aria-hidden className={`skeleton ${className}`} />;
}

export function PageTitle({ children, sub }: { children: ReactNode; sub?: ReactNode }) {
  return (
    <div className="mb-5">
      <h1>{children}</h1>
      <Scribble className="mt-0.5" />
      {sub && <p className="mt-2 max-w-prose text-muted">{sub}</p>}
    </div>
  );
}
