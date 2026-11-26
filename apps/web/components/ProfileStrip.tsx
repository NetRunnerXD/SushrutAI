"use client";

import { ChevronDown, HeartPulse, Pill, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Avatar, useActiveProfile, useFamily } from "@/components/Family";
import { api, switchProfile } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";

type CardData = { age: number | null; sex: string | null; blood_group: string | null; allergies: string[]; conditions: string[]; medicines: string[] };

/** Who the assistant is answering for, as a one-line strip above the chat (collapsed by default, remembered per
 *  device). Expanded: allergies, conditions, medicines, and the household's people to switch to. */
export function ProfileStrip() {
  const { t } = useI18n();
  const { prefs, setPrefs } = usePrefs();
  const who = useActiveProfile();
  const { members } = useFamily();
  const [c, setC] = useState<CardData | null>(null);
  useEffect(() => { api<CardData>("/me/emergency-card").then(setC).catch(() => {}); }, []);
  const open = prefs.profileStrip;
  const name = who ? who.name : t.myself;
  const bits = [
    c?.age != null ? fill(t.yearsOld, { n: c.age }) : null,
    c?.allergies?.length ? fill(t.nAllergies, { n: c.allergies.length }) : null,
    c?.conditions?.length ? fill(t.nConditions, { n: c.conditions.length }) : null,
    c?.medicines?.length ? fill(t.nMedicines, { n: c.medicines.length }) : null,
  ].filter(Boolean) as string[];

  const List = ({ icon: Icon, title, items, tone = "" }: { icon: any; title: string; items?: string[]; tone?: string }) => (
    <div className="min-w-0">
      <p className="mb-0.5 flex items-center gap-1.5 text-[0.8125rem] font-semibold text-muted"><Icon aria-hidden className="h-4 w-4" />{title}</p>
      {items?.length ? <p className={`text-[0.9375rem] ${tone}`}>{items.join(", ")}</p>
        : <p className="text-[0.9375rem] text-muted">{t.visitNoneRecorded}</p>}
    </div>
  );

  return (
    <section aria-label={t.contextPanel} className="mt-3 rounded-[16px] border border-border bg-surface shadow-[var(--rim)]">
      <button type="button" onClick={() => setPrefs({ profileStrip: !open })} aria-expanded={open}
        className="flex min-h-12 w-full items-center gap-2.5 rounded-[16px] px-3 py-1.5 text-start hover:bg-surface-2">
        <Avatar name={who?.name} id={who?.id} size={30} />
        <span className="min-w-0 flex-1 truncate text-[0.9375rem]">
          <span className="font-semibold">{name}</span>
          {bits.length > 0 && <span className="text-muted max-sm:hidden"> · {bits.join(" · ")}</span>}
          {!!c?.allergies?.length && <span className="ms-1.5 font-semibold text-danger sm:hidden">⚠ {c.allergies.length}</span>}
        </span>
        <ChevronDown aria-hidden className={`h-5 w-5 shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="space-y-3 border-t border-border px-3 pb-3 pt-2.5">
          <p className="text-[0.8125rem] text-muted">{t.contextPanelHelp}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <List icon={ShieldAlert} title={t.visitAllergies} items={c?.allergies} tone="font-semibold text-danger" />
            <List icon={HeartPulse} title={t.visitConditions} items={c?.conditions} />
            <List icon={Pill} title={t.visitMeds} items={c?.medicines} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!!members?.length && (
              <>
                <span className="text-[0.875rem] text-muted">{t.askingFor}</span>
                {[null, ...members].map((m) => {
                  const current = m ? who?.id === m.id : !who;
                  return (
                    <button key={m?.id ?? "me"} type="button" aria-pressed={current} disabled={current}
                      onClick={() => switchProfile(m && { id: m.id, name: m.name, relation: m.relation }, "/ask")}
                      className={`inline-flex min-h-12 items-center gap-1.5 rounded-full border-[1.5px] px-2.5 text-[0.875rem] font-semibold ${current ? "border-brand bg-brand-soft" : "border-border-input hover:bg-surface-2"}`}>
                      <Avatar name={m?.name} id={m?.id} size={22} />{m ? m.name : t.myself}</button>
                  );
                })}
              </>
            )}
            <Link href="/me" className="ms-auto inline-flex min-h-12 items-center text-[0.875rem] font-semibold text-info underline">{t.editLabel}</Link>
          </div>
        </div>
      )}
    </section>
  );
}
