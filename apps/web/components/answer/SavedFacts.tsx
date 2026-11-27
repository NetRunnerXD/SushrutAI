"use client";

import { Database, X } from "lucide-react";
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
    <div role="status" aria-label={t.savingFacts} className="flex flex-wrap items-center gap-2 rounded-[12px] border border-ok/35 bg-ok-soft/30 px-3 py-2">
      <span className="inline-flex items-center gap-1.5 text-[0.8125rem] font-bold text-ok">
        <Database aria-hidden className="h-3.5 w-3.5" />{t.savingFacts}:</span>
      {shown.map((f) => (
        <span key={f.id} className="inline-flex items-center gap-1.5 rounded-full border border-ok/45 bg-surface py-0.5 ps-2.5 pe-1 text-[0.8125rem] font-semibold text-text shadow-[var(--rim)]">
          <span className="rounded-full bg-ok-soft px-1.5 py-0.5 text-[0.68rem] font-bold uppercase text-ok">{f.kind}</span>
          <span>{display(f, t)}</span>
          <button type="button" onClick={() => undo(f.id)} aria-label={`${t.undoFact}: ${display(f, t)}`}
            className="grid h-6 w-6 place-items-center rounded-full text-muted hover:bg-danger-soft hover:text-danger"><X aria-hidden className="h-3.5 w-3.5" /></button>
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
