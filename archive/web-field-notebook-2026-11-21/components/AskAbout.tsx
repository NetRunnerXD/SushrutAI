"use client";

import { MessageCircle } from "lucide-react";
import Link from "next/link";
import { useI18n } from "@/lib/i18n";

/** "Ask about this": opens the assistant with this record attached (lab:<id>, med:<id>, product:<id>, rx:<id>,
 *  vital:<type>, triage:<id>). The assistant then answers about that exact record. */
export function askHref(ref: string, label: string) {
  return `/ask?ref=${encodeURIComponent(ref)}&about=${encodeURIComponent(label.slice(0, 80))}`;
}

export function AskAbout({ refId, label, compact = false, text }: { refId: string; label: string; compact?: boolean; text?: string }) {
  const { t } = useI18n();
  return (
    <Link href={askHref(refId, label)} aria-label={`${t.askAboutThis}: ${label}`}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border-[1.5px] border-info font-semibold text-info hover:bg-info-soft
        ${compact ? "min-h-12 px-2.5 text-[0.8125rem]" : "min-h-12 px-3 text-[0.9375rem]"}`}>
      <MessageCircle aria-hidden className="h-4 w-4" />{text ?? t.askAboutThis}
    </Link>
  );
}
