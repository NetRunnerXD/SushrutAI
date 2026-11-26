"use client";

import { BadgeCheck, CircleHelp, Pill } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useI18n, bcp47 } from "@/lib/i18n";
import { speak } from "@/lib/voice";
import { ListenButton } from "../Voice";

type Story = {
  text: string;
  resolved: { query: string; salt: string; verified: boolean; source: string };
  alternatives: { id: string; name: string; price?: { per_unit_inr?: number } | null }[];
  warnings: string[];
};

/** Medicine storyline cards: the salt (verified, or "please check the strip"), one narrative that can be listened
 *  to, and the cheaper same-salt options as links. */
export function Storylines({ items }: { items?: Story[] }) {
  const { t, lang } = useI18n();
  if (!items?.length) return null;
  return (
    <div className="space-y-3">
      {items.map((s, i) => (
        <section key={i} aria-label={s.resolved.query} className="space-y-2.5 rounded-[18px] border border-border bg-surface p-4 shadow-[var(--rim),var(--e1)]">
          <div className="flex flex-wrap items-center gap-2">
            <span aria-hidden className="grid h-10 w-10 place-items-center rounded-full bg-brand-soft text-brand"><Pill className="h-5 w-5" /></span>
            <h3 className="text-[1.125rem] font-bold">{s.resolved.query}</h3>
            {s.resolved.salt && (
              <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[0.8125rem] font-semibold
                ${s.resolved.verified ? "bg-ok-soft text-ok" : "bg-warn-soft text-warn"}`}>
                {s.resolved.verified ? <BadgeCheck aria-hidden className="h-3.5 w-3.5" /> : <CircleHelp aria-hidden className="h-3.5 w-3.5" />}
                {s.resolved.salt}{s.resolved.verified ? "" : ` · ${t.saltUnverified}`}
              </span>
            )}
          </div>
          <p className="max-w-[68ch] text-[1.0625rem] leading-[1.7]">{s.text}</p>
          {s.alternatives.length > 0 && (
            <ul className="flex flex-wrap gap-2" aria-label={t.sameSaltOptions}>
              {s.alternatives.map((a) => (
                <li key={a.id}><Link href={`/medicines/${a.id}`}
                  className="inline-flex min-h-11 items-center gap-1.5 rounded-full border-[1.5px] border-border-input bg-surface px-3 text-[0.9375rem] font-semibold hover:bg-surface-2">
                  {a.name}{a.price?.per_unit_inr ? <span className="font-mono text-muted">₹{a.price.per_unit_inr.toFixed(2)}</span> : null}
                </Link></li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <ListenButton label={t.readAloud} onClick={() => speak(s.text, lang, bcp47(lang))} />
            <WorkedForMe name={s.resolved.query} />
          </div>
        </section>
      ))}
    </div>
  );
}

/** Light, skippable: how this medicine went. Remembered as the person's own experience for later stories. */
function WorkedForMe({ name }: { name: string }) {
  const { t } = useI18n();
  const [done, setDone] = useState<string | null>(null);
  if (done) return <span className="text-[0.875rem] font-semibold text-ok">{t.thanksNoted}</span>;
  const send = (v: string) => {
    setDone(v);
    api("/me/memory/feedback", { method: "POST", body: JSON.stringify({ kind: "medicine", name, value: v }) }).catch(() => {});
  };
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-[0.875rem]">
      <span className="text-muted">{t.didItWork}</span>
      {([["better", t.workedBetter], ["same", t.workedSame], ["worse", t.workedWorse]] as const).map(([v, l]) => (
        <button key={v} type="button" onClick={() => send(v)}
          className="min-h-9 rounded-full border border-border-input px-2.5 font-semibold hover:bg-surface-2">{l}</button>
      ))}
    </span>
  );
}
