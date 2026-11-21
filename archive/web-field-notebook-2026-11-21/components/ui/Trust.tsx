"use client";

import {
  AlertTriangle, CheckCircle2, CircleAlert, Info, ShieldAlert, ShieldCheck, Siren, Stethoscope,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { useI18n } from "@/lib/i18n";

/* ---------------- banners ---------------- */
type Tone = "info" | "warning" | "danger" | "emergency" | "success";
const TONE: Record<Tone, { cls: string; rule: string; icon: LucideIcon }> = {
  info: { cls: "bg-info-soft text-info", rule: "border-info", icon: Info },
  success: { cls: "bg-ok-soft text-ok", rule: "border-ok", icon: CheckCircle2 },
  warning: { cls: "bg-warn-soft text-warn", rule: "border-warn", icon: AlertTriangle },
  danger: { cls: "bg-danger-soft text-danger", rule: "border-danger", icon: CircleAlert },
  emergency: { cls: "bg-emergency text-white", rule: "border-[#7A110B]", icon: Siren },
};

export function Banner({ tone = "info", title, children, action, icon }: {
  tone?: Tone; title?: ReactNode; children?: ReactNode; action?: ReactNode; icon?: LucideIcon;
}) {
  const { cls, rule, icon: DefaultIcon } = TONE[tone];
  const Icon = icon ?? DefaultIcon;
  return (
    <div role={tone === "danger" || tone === "emergency" ? "alert" : "status"}
         className={`flex gap-3 rounded-[3px_8px_8px_3px] border-l-[5px] p-3 sm:p-4 ${rule} ${cls}`}>
      <Icon aria-hidden className="mt-0.5 h-5 w-5 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className="text-[0.9375rem] leading-relaxed">{children}</div>}
        {action && <div className="mt-2 flex flex-wrap gap-2">{action}</div>}
      </div>
    </div>
  );
}

/* ---------------- urgency (the single severity scale used everywhere) ---------------- */
export type Urgency = "emergency" | "see_doctor_24h" | "see_doctor_week" | "self_care";
const URG: Record<Urgency, { tone: Tone; icon: LucideIcon }> = {
  emergency: { tone: "emergency", icon: Siren },
  see_doctor_24h: { tone: "danger", icon: Stethoscope },
  see_doctor_week: { tone: "warning", icon: Stethoscope },
  self_care: { tone: "success", icon: CheckCircle2 },
};

export function UrgencyBanner({ urgency, specialist, onEmergency }: {
  urgency: Urgency; specialist?: string; onEmergency?: () => void;
}) {
  const { t } = useI18n();
  const u = URG[urgency] ?? URG.see_doctor_week;
  return (
    <Banner tone={u.tone} icon={u.icon} title={t.urgency[urgency] ?? urgency}
      action={urgency === "emergency" && onEmergency ? (
        <button onClick={onEmergency} className="min-h-12 rounded-[10px] bg-white px-4 font-bold text-[#B01E16]">{t.sosOpen}</button>
      ) : undefined}>
      {specialist ? <span>{t.specialist}: <b>{specialist}</b></span> : null}
    </Banner>
  );
}

export function UrgencyPill({ urgency }: { urgency: Urgency }) {
  const { t } = useI18n();
  const u = URG[urgency] ?? URG.see_doctor_week;
  const Icon = u.icon;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[0.875rem] font-semibold ${TONE[u.tone].cls}`}>
      <Icon aria-hidden className="h-4 w-4" />{t.urgency[urgency] ?? urgency}
    </span>
  );
}

/* ---------------- confidence ---------------- */
export type Confidence = "high" | "medium" | "low";
export function ConfidencePill({ level }: { level: Confidence }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const map = { high: { cls: "bg-ok-soft text-ok", icon: ShieldCheck }, medium: { cls: "bg-warn-soft text-warn", icon: AlertTriangle },
                low: { cls: "bg-danger-soft text-danger", icon: ShieldAlert } }[level];
  const Icon = map.icon;
  return (
    <span className="inline-flex flex-col">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
              className={`inline-flex min-h-12 items-center gap-1 rounded-full px-3 text-[0.875rem] font-semibold ${map.cls}`}>
        <Icon aria-hidden className="h-4 w-4" />{t.confidence[level]}
      </button>
      {open && <span className="mt-1 max-w-xs text-[0.875rem] text-muted">{t.confidenceHelp}</span>}
    </span>
  );
}

/* ---------------- sources ---------------- */
export function SourceList({ sources }: { sources: { n: number; title: string; url?: string | null }[] }) {
  const { t } = useI18n();
  if (!sources?.length) {
    return (
      <p className="flex items-center gap-1.5 text-[0.875rem] text-warn">
        <AlertTriangle aria-hidden className="h-4 w-4" />{t.noSource}
      </p>
    );
  }
  return (
    <details className="text-[0.9375rem]">
      <summary className="flex min-h-12 cursor-pointer list-none items-center gap-1.5 font-semibold text-ok">
        <ShieldCheck aria-hidden className="h-4 w-4" />{t.sources} ({sources.length})
      </summary>
      <ul className="mt-1 space-y-1 pl-6">
        {sources.map((s) => (
          <li key={s.n}>{s.url ? <a className="text-info underline" href={s.url} target="_blank" rel="noreferrer">{s.title}</a> : s.title}</li>
        ))}
      </ul>
    </details>
  );
}

/* ---------------- disclaimer ---------------- */
export function DisclaimerChip() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <div className="text-[0.875rem] text-muted">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open} className="inline-flex min-h-12 items-center gap-1.5">
        <Stethoscope aria-hidden className="h-4 w-4" />{t.disclaimerShort}
        <span className="text-info underline">{t.learnMore}</span>
      </button>
      {open && <p className="mt-1">{t.disclaimerLong}</p>}
    </div>
  );
}

export function PrivacyBadge({ local }: { local: boolean }) {
  const { t } = useI18n();
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[0.8125rem] font-semibold
      ${local ? "bg-ok-soft text-ok" : "bg-info-soft text-info"}`}>
      <ShieldCheck aria-hidden className="h-3.5 w-3.5" />{local ? t.processedLocally : t.processedCloud}
    </span>
  );
}
