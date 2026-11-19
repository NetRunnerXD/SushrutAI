"use client";

import { Check, Languages } from "lucide-react";
import { useState } from "react";
import { LANGS, useI18n, type Lang } from "@/lib/i18n";
import { Sheet } from "./ui/Sheet";

export function LanguageGrid({ onPick }: { onPick?: (l: Lang) => void }) {
  const { lang, setLang } = useI18n();
  return (
    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {LANGS.map((l) => {
        const active = l.code === lang;
        return (
          <li key={l.code}>
            <button lang={l.code} onClick={() => { setLang(l.code); onPick?.(l.code); }} aria-pressed={active}
              className={`flex min-h-16 w-full flex-col items-start justify-center rounded-[16px] border-2 px-4 py-2 text-left transition-colors
                ${active ? "border-brand bg-brand-soft" : "border-border bg-surface shadow-[var(--rim)] hover:border-border-input"}`}>
              <span className="flex w-full items-center justify-between text-[1.25rem] font-semibold">
                {l.label}{active && <Check aria-hidden className="h-5 w-5 text-muted" strokeWidth={2.6} />}
              </span>
              <span lang="en" className="text-[0.875rem] text-muted">{l.english}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** Header language button: always visible, shows the current language in its own script. */
export function LanguageButton() {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const cur = LANGS.find((l) => l.code === lang)!;
  return (
    <>
      <button onClick={() => setOpen(true)} aria-label={`${t.language}: ${cur.english}`}
        className="inline-flex min-h-12 items-center gap-1.5 shrink-0 rounded-full bg-rail-2 px-3 font-semibold text-rail-text hover:bg-rail-muted/30 max-[479px]:w-12 max-[479px]:justify-center max-[479px]:px-0">
        <Languages aria-hidden className="h-5 w-5 text-rail-muted" /><span lang={lang} className="max-[479px]:sr-only">{cur.label}</span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={t.chooseLanguage}>
        <LanguageGrid onPick={() => setOpen(false)} />
      </Sheet>
    </>
  );
}
