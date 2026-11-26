"use client";

import { BookmarkCheck, X } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Saved = { id: string; kind: string; name: string; label: string; value: any };

/** What this turn saved to the person's health record ("101.0 °F", "2 days"), highlighted so they can see it,
 *  each with a one-tap undo. */
export function SavedFacts({ facts }: { facts?: Saved[] }) {
  const { t } = useI18n();
  const [gone, setGone] = useState<Set<string>>(new Set());
  const shown = (facts ?? []).filter((f) => !gone.has(f.id) && f.kind !== "note");
  if (!shown.length) return null;
  const undo = async (id: string) => {
    setGone((g) => new Set(g).add(id));
    await api(`/me/memory/facts/${id}`, { method: "DELETE" }).catch(() => {});
    window.dispatchEvent(new Event("sushrut:data-changed"));
  };
  return (
    <div role="status" aria-label={t.savingFacts} className="flex flex-wrap items-center gap-2">
      <span className="inline-flex items-center gap-1.5 text-[0.8125rem] font-semibold text-muted">
        <BookmarkCheck aria-hidden className="h-4 w-4 text-ok" />{t.savingFacts}:</span>
      {shown.map((f) => (
        <span key={f.id} className="inline-flex items-center gap-1 rounded-full border border-ok/40 bg-ok-soft py-0.5 ps-3 pe-1 text-[0.875rem] font-semibold text-text">
          {display(f, t)}
          <button type="button" onClick={() => undo(f.id)} aria-label={`${t.undoFact}: ${display(f, t)}`}
            className="grid h-7 w-7 place-items-center rounded-full text-muted hover:bg-surface hover:text-danger"><X aria-hidden className="h-3.5 w-3.5" /></button>
        </span>
      ))}
    </div>
  );
}

function display(f: Saved, t: any): string {
  const v = f.value ?? {};
  if (f.kind === "vital" && v.type === "temp") {  // thermometers in India read °F; show what was said when it was °F
    const fv = typeof v.f === "number" ? v.f : Math.round((v.value * 9 / 5 + 32) * 10) / 10;
    return `${fv} ${t.feverF}`;
  }
  if (f.kind === "vital") return `${f.name} ${v.value}${v.unit ? " " + v.unit : ""}`;
  if (f.kind === "duration") return `${v.amount} ${v.unit}`;
  return f.label;
}
