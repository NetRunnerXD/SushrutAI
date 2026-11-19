import type { LucideIcon } from "lucide-react";
import { Illustration, type ArtName } from "./Illustrations";
import { createContext, useContext, type CSSProperties, type ReactNode } from "react";
import { ReadPageButton } from "../ReadPage";

/** Inside a Compartments tray, cards drop their own lid and become cells divided by a rule. */
const Bare = createContext(false);
export function Compartments({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`divide-y divide-border overflow-hidden [&>*:empty]:hidden rounded-[24px] border border-border bg-surface shadow-[var(--rim),var(--e1)] ${className}`}>
      <Bare.Provider value>{children}</Bare.Provider>
    </div>
  );
}

/** A steel lid: polished surface with a rolled rim. (`i` and `settle` are accepted for older callers and ignored.) */
export function Card({ children, className = "", as: As = "section", i: _i, settle: _s, style, ...rest }: {
  children: ReactNode; className?: string; as?: "section" | "div" | "article" | "li"; i?: number;
  style?: CSSProperties; settle?: boolean; [k: string]: unknown;
}) {
  const bare = useContext(Bare);
  return (
    <As {...rest} style={style}
      className={`${bare ? "" : "rounded-[18px] border border-border bg-surface shadow-[var(--rim),var(--e1)]"} p-4 sm:p-5 ${className}`}>
      {children}
    </As>
  );
}

/** A round katori well holding an icon: the one form used for every small mark in the app. */
export function Katori({ icon: Icon, size = 40, className = "" }: { icon: LucideIcon; size?: number; className?: string }) {
  return (
    <span aria-hidden style={{ width: size, height: size }}
      className={`well grid shrink-0 place-items-center rounded-full text-text ${className}`}>
      <Icon style={{ width: size * 0.48, height: size * 0.48 }} strokeWidth={2.1} />
    </span>
  );
}

export function CardHeader({ icon, title, action, sub, level = 2 }: {
  icon?: LucideIcon; title: ReactNode; action?: ReactNode; sub?: ReactNode; level?: 2 | 3;
}) {
  const H = level === 2 ? "h2" : "h3";
  return (
    <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
      <div className="flex min-w-0 flex-1 basis-[12rem] items-center gap-3">
        {icon && <Katori icon={icon} />}
        <div className="min-w-0">
          <H className={`leading-snug ${level === 2 ? "text-[1.25rem]" : "display text-[1.125rem] font-bold"}`}>{title}</H>
          {sub && <p className="text-[0.9375rem] text-muted">{sub}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ icon, text, action, art }: { icon: LucideIcon; text: ReactNode; action?: ReactNode; art?: ArtName }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-[16px] bg-surface-2/70 p-6 text-center">
      {art ? <Illustration name={art} /> : <Katori icon={icon} size={56} />}
      <p className="max-w-sm text-muted">{text}</p>
      {action}
    </div>
  );
}

export function Skeleton({ className = "h-5 w-full" }: { className?: string }) {
  return <div aria-hidden className={`skeleton ${className}`} />;
}

export function PageTitle({ children, sub, read = true }: { children: ReactNode; sub?: ReactNode; read?: boolean }) {
  return (
    <div className="mb-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1"><h1>{children}</h1>{read && <ReadPageButton />}</div>
      {sub && <p className="mt-1.5 max-w-prose text-muted">{sub}</p>}
    </div>
  );
}

/** @deprecated Field Notebook ornament; renders nothing now. */
export function Scribble(_: { className?: string }) {
  return null;
}
