"use client";

import { BookOpen, HeartPulse, Pill, ShieldAlert, UserRound } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useActiveProfile } from "@/components/Family";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

type CardData = { age: number | null; sex: string | null; blood_group: string | null; allergies: string[]; conditions: string[]; medicines: string[] };

/** Desktop side panel beside the chat: what the assistant knows about this person, and where the last answer came from. */
export function ContextPanel({ sources }: { sources?: { n: number; title: string; url?: string; source?: string }[] }) {
  const { t } = useI18n();
  const who = useActiveProfile();
  const [c, setC] = useState<CardData | null>(null);
  useEffect(() => { api<CardData>("/me/emergency-card").then(setC).catch(() => {}); }, []);
  const Section = ({ icon: Icon, title, items, tone = "" }: { icon: any; title: string; items?: string[]; tone?: string }) => (
    <section>
      <h3 className="mb-1 flex items-center gap-1.5 text-[0.875rem] font-semibold text-muted"><Icon aria-hidden className="h-4 w-4" />{title}</h3>
      {items?.length ? <ul className={`space-y-0.5 text-[0.9375rem] ${tone}`}>{items.map((x) => <li key={x}>{x}</li>)}</ul>
        : <p className="text-[0.9375rem] text-muted">{t.visitNoneRecorded}</p>}
    </section>
  );
  return (
    <aside aria-label={t.contextPanel} className="hidden xl:block">
      <div className="note sticky top-20 space-y-4 rounded-[6px] border border-border border-t-[5px] border-t-accent bg-surface p-4 shadow-[var(--e1)]">
        <div className="flex items-center gap-2">
          <UserRound aria-hidden className="h-5 w-5 text-brand" />
          <p className="font-semibold">{who ? who.name : t.myself}
            {c && <span className="block text-[0.875rem] font-normal text-muted">
              {[c.age != null && fill(t.yearsOld, { n: c.age }), c.sex, c.blood_group].filter(Boolean).join(" · ")}</span>}</p>
        </div>
        <p className="text-[0.8125rem] text-muted">{t.contextPanelHelp}</p>
        <Section icon={ShieldAlert} title={t.visitAllergies} items={c?.allergies} tone="font-semibold text-danger" />
        <Section icon={HeartPulse} title={t.visitConditions} items={c?.conditions} />
        <Section icon={Pill} title={t.visitMeds} items={c?.medicines} />
        <Link href="/me" className="inline-block text-[0.875rem] font-semibold text-info underline">{t.editLabel}</Link>
        {sources && sources.length > 0 && (
          <section className="border-t border-border pt-3">
            <h3 className="mb-1 flex items-center gap-1.5 text-[0.875rem] font-semibold text-muted"><BookOpen aria-hidden className="h-4 w-4" />{t.sources}</h3>
            <ol className="list-decimal space-y-1 pl-5 text-[0.875rem]">
              {sources.map((s) => <li key={s.n}>{s.url ? <a href={s.url} target="_blank" rel="noreferrer" className="underline">{s.title}</a> : s.title}</li>)}
            </ol>
          </section>
        )}
      </div>
    </aside>
  );
}
