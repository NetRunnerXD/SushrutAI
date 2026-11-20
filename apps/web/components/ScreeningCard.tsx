"use client";

import { ScanSearch } from "lucide-react";
import { ConfidenceBand, type ScreenFinding } from "@/components/ScanViewer";
import { fill, useI18n } from "@/lib/i18n";

/** What a dedicated screening model (e.g. the chest X-ray model) noticed before MedGemma described the image.
 *  Plain words and a validated band ("possible" / "likely"), never raw scores; always framed as hints for a doctor. */
export type Screening = { model: string; scan_type: string; flagged: ScreenFinding[]; none_flagged: boolean };

export function ScreeningCard({ s }: { s: Screening }) {
  const { t } = useI18n();
  return (
    <section aria-labelledby="screen-title" className="rounded-[16px] border-[1.5px] border-border bg-surface p-3 shadow-[var(--rim)] sm:p-4">
      <h3 id="screen-title" className="display flex items-center gap-2 text-[1.125rem] font-bold">
        <ScanSearch aria-hidden className="h-5 w-5 text-muted" />{t.screenTitle}
      </h3>
      <p className="text-[0.875rem] text-muted">{fill(t.screenBy, { model: s.model })}</p>
      {s.none_flagged ? (
        <p className="mt-2">{t.screenNone}</p>
      ) : (
        <>
          <p className="mt-2 font-semibold">{t.screenFlagged}</p>
          <ul className="mt-1.5 divide-y divide-border">
            {s.flagged.map((f) => (
              <li key={f.text} className="flex flex-col gap-1 py-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
                <span>{f.text}</span>
                <ConfidenceBand band={f.band} />
              </li>
            ))}
          </ul>
          <p className="mt-1 text-[0.8125rem] text-muted">{t.bandHelp}</p>
        </>
      )}
      <p className="mt-2 text-[0.875rem] text-muted">{t.screenConfirm}</p>
    </section>
  );
}
