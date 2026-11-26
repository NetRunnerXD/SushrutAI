"use client";

import { useEffect, useState } from "react";
import { API, authFetch } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** region: how far the picture can be trusted (services/api/app/vision/screen.py REGION_QUALITY):
 *  "tested" = checked against doctors' markings, "anatomy" = the organ's own outline, "rough" = not checked. */
export type ScreenFinding = {
  key?: string; text: string; band: "possible" | "likely" | null; overlay: string | null;
  region?: "tested" | "anatomy" | "rough" | null;
};

/** Heat-map band colours, the same three as services/api/app/vision/overlay.py (amber -> red). */
export const HEAT = [
  { key: "heatLow", color: "rgb(226 167 42 / 55%)" },
  { key: "heatMid", color: "rgb(217 89 38 / 70%)" },
  { key: "heatHigh", color: "rgb(192 38 28 / 85%)" },
] as const;

/** The uploaded scan on a dark light-box, switchable to the area each flagged finding points at. The original is
 *  always one tap away; each picture carries its confidence band, how far its area can be trusted, and a legend. */
export function ScanViewer({ docId, findings, label }: { docId: string; findings: ScreenFinding[]; label: string }) {
  const { t } = useI18n();
  const [original, setOriginal] = useState<string | null>(null);
  const withPic = findings.filter((f) => f.overlay);
  const [view, setView] = useState<number>(withPic.length ? 0 : -1); // -1 = original

  useEffect(() => {
    let url: string | null = null;
    authFetch(`${API}/documents/${docId}/content`)
      .then((r) => (r.ok ? r.blob() : null)).then((b) => { if (b) { url = URL.createObjectURL(b); setOriginal(url); } })
      .catch(() => {});
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [docId]);
  useEffect(() => setView(withPic.length ? 0 : -1), [withPic.length]);

  const short = (f: ScreenFinding) => (f.key && (t.findShort as Record<string, string>)[f.key]) || f.text;
  const cur = view >= 0 ? withPic[view] : null;
  const src = cur ? cur.overlay : original;
  return (
    <figure className="space-y-2">
      {withPic.length > 0 && (
        <div role="group" aria-label={t.viewChoose} className="flex flex-wrap gap-1.5">
          <ViewChip on={view === -1} onClick={() => setView(-1)}>{t.viewOriginal}</ViewChip>
          {withPic.map((f, i) => (
            <ViewChip key={f.text} on={view === i} onClick={() => setView(i)}>
              <span className="inline-flex items-center gap-1.5">{short(f)}<BandDots band={f.band} /></span>
            </ViewChip>
          ))}
        </div>
      )}
      <div className="grid aspect-[4/3] place-items-center overflow-hidden rounded-[18px] bg-rail shadow-[var(--well)] lg:aspect-square">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt={cur ? `${short(cur)}: ${cur.text}` : label} className="max-h-full max-w-full object-contain" />
        ) : <span className="text-[0.9375rem] text-rail-muted">{t.loading}</span>}
      </div>
      {cur && (
        <figcaption className="space-y-1.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[0.9375rem] font-semibold">{cur.text}</p>
            <ConfidenceBand band={cur.band} />
          </div>
          <p className={`rounded-[10px] px-2.5 py-1.5 text-[0.875rem] ${cur.region === "rough" ? "bg-warn-soft text-warn" : "bg-surface-2 text-muted"}`}>
            {cur.region === "tested" ? t.regionTested : cur.region === "anatomy" ? t.regionAnatomy : t.regionRough}
          </p>
          {cur.region === "anatomy" ? (
            <p className="inline-flex items-center gap-1.5 text-[0.875rem]">
              <span aria-hidden className="h-3.5 w-5 rounded-[4px] border-2" style={{ background: HEAT[1].color, borderColor: "rgb(192 38 28)" }} />
              {t.heartOutline}
            </p>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.875rem]">
                <span className="text-muted">{t.heatLegend}</span>
                {HEAT.map((h) => (
                  <span key={h.key} className="inline-flex items-center gap-1.5">
                    <span aria-hidden className="h-3.5 w-5 rounded-[4px] border border-border" style={{ background: h.color }} />
                    {t[h.key]}
                  </span>
                ))}
              </div>
              <p className="text-[0.8125rem] text-muted">{t.heatNote}</p>
            </>
          )}
        </figcaption>
      )}
    </figure>
  );
}

function ViewChip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick}
      className={`min-h-11 rounded-full border-[1.5px] px-3.5 text-[0.9375rem] font-semibold transition-colors
        ${on ? "border-brand bg-brand-soft text-text" : "border-border-input bg-surface text-muted hover:text-text"}`}>
      {children}
    </button>
  );
}

/** The three-step band as small dots, for chips. */
function BandDots({ band }: { band: "possible" | "likely" | null }) {
  if (!band) return null;
  const n = band === "likely" ? 3 : 2;
  const tone = band === "likely" ? "bg-danger" : "bg-warn";
  return (
    <span aria-hidden className="flex gap-0.5">
      {[0, 1, 2].map((i) => <span key={i} className={`h-1.5 w-1.5 rounded-full ${i < n ? tone : "bg-border"}`} />)}
    </span>
  );
}

/** "Possible" / "Likely" as a three-step band: how strongly the screening model points to a finding. */
export function ConfidenceBand({ band }: { band: "possible" | "likely" | null }) {
  const { t } = useI18n();
  if (!band) return null;
  const n = band === "likely" ? 3 : 2;
  const tone = band === "likely" ? "bg-danger" : "bg-warn";
  return (
    <span className="inline-flex items-center gap-2" title={t.bandHelp}>
      <span aria-hidden className="flex gap-0.5">
        {[0, 1, 2].map((i) => <span key={i} className={`h-2 w-5 rounded-full ${i < n ? tone : "bg-surface-2 shadow-[var(--well)]"}`} />)}
      </span>
      <span className={`text-[0.875rem] font-semibold ${band === "likely" ? "text-danger" : "text-warn"}`}>
        {band === "likely" ? t.bandLikely : t.bandPossible}
      </span>
    </span>
  );
}
