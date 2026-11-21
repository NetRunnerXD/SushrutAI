"use client";

import { Loader2, type LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "emergency";
type Size = "md" | "lg";

const VARIANT: Record<Variant, string> = {
  primary: "bg-brand text-brand-ink hover:bg-brand-strong shadow-[inset_0_-2px_0_rgb(0_0_0/22%)]",
  secondary: "bg-surface text-text border-[1.5px] border-border-input hover:bg-surface-2",
  ghost: "bg-surface-2 text-text hover:bg-accent-soft",
  danger: "bg-danger-soft text-danger hover:brightness-95",
  emergency: "bg-emergency text-white hover:brightness-110",
};
const SIZE: Record<Size, string> = { md: "min-h-12 px-4 text-[1.0625rem]", lg: "min-h-14 px-5 text-[1.0625rem]" };

export function Button({
  variant = "primary", size = "md", icon: Icon, loading, children, className = "", ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: Size; icon?: LucideIcon; loading?: boolean; children?: ReactNode;
}) {
  return (
    <button
      {...rest}
      aria-busy={loading || undefined}
      disabled={rest.disabled || loading}
      className={`inline-flex min-w-12 items-center justify-center gap-2 rounded-[10px] font-semibold transition-[background-color,transform]
        active:translate-y-px
        disabled:cursor-not-allowed disabled:opacity-55 ${VARIANT[variant]} ${SIZE[size]} ${className}`}
    >
      {loading ? <Loader2 aria-hidden className="h-5 w-5 animate-spin" /> : Icon ? <Icon aria-hidden className="h-5 w-5 shrink-0" /> : null}
      {children}
    </button>
  );
}

export function IconButton({
  icon: Icon, label, variant = "ghost", className = "", ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string; variant?: Variant }) {
  return (
    <button
      {...rest}
      aria-label={label}
      title={label}
      className={`inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-full transition-colors
        disabled:opacity-55 ${VARIANT[variant]} ${className}`}
    >
      <Icon aria-hidden className="h-6 w-6" />
    </button>
  );
}
