"use client";

import { Square, Volume2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useI18n } from "@/lib/i18n";
import { isSpeaking, readPage, stopSpeaking } from "@/lib/voice";

/** "Read this page": reads the page's main content aloud with the device voice (voice-first: every page has one).
 *  Pages can mark what matters with data-speak; otherwise headings, paragraphs and list items are read. */
export function ReadPageButton() {
  const { t, lang } = useI18n();
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!on) return;
    const id = setInterval(() => { if (!isSpeaking()) setOn(false); }, 600);
    return () => clearInterval(id);
  }, [on]);
  return (
    <button type="button" onClick={() => { if (on) { stopSpeaking(); setOn(false); } else { readPage(lang); setOn(true); } }}
      aria-pressed={on} aria-label={on ? t.stop : t.readPage} title={on ? t.stop : t.readPage}
      className="inline-flex min-h-12 shrink-0 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold hover:bg-border print:hidden">
      {on ? <Square aria-hidden className="h-4 w-4 fill-current" /> : <Volume2 aria-hidden className="h-4 w-4" />}
      <span className="max-sm:sr-only">{on ? t.stop : t.readPage}</span>
    </button>
  );
}
