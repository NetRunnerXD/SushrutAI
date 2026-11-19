"use client";

import { ChevronRight, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Avatar, useActiveProfile } from "@/components/Family";
import { useDataChanged } from "@/components/Home";
import { Card, CardHeader } from "@/components/ui/Card";
import { api, switchProfile } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

type Person = { id: string | null; name: string | null; relation: string; doses_total: number; doses_left: number; doses_missed: number;
                next_dose: { name: string; time: string | null } | null; care_due: { title: string; status: string }[] };

/** One look at the whole household's day: doses left and vaccines or check-ups due, for each person. */
export function HouseholdToday() {
  const { t } = useI18n();
  const active = useActiveProfile();
  const [people, setPeople] = useState<Person[] | null>(null);
  const load = useCallback(() => {
    api<{ people: Person[] }>("/me/household-today").then((r) => setPeople(r.people)).catch(() => setPeople([]));
  }, []);
  useEffect(load, [load]);
  useDataChanged(load);
  if (!people || people.length < 2) return null; // only useful when the account manages family members

  return (
    <Card>
      <CardHeader icon={Users} title={t.householdToday} sub={t.householdHelp} />
      <ul className="divide-y divide-border">
        {people.map((p) => {
          const current = p.id ? active?.id === p.id : !active;
          const overdue = p.care_due.filter((c) => c.status === "overdue").length;
          return (
            <li key={p.id ?? "me"}>
              <button disabled={current} onClick={() => switchProfile(p.id ? { id: p.id, name: p.name!, relation: p.relation } : null)}
                className="flex min-h-16 w-full items-center gap-3 py-2 text-left disabled:cursor-default"
                aria-label={`${p.name ?? t.myself}${current ? "" : ` — ${t.switchTo}`}`}>
                <Avatar name={p.name ?? undefined} relation={p.relation} />
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold">{p.name ?? t.myself}</span>
                  <span className="block text-[0.9375rem] text-muted">
                    {p.doses_total ? (p.doses_left ? fill(t.householdDosesLeft, { n: p.doses_left, total: p.doses_total }) : t.allDosesDone) : t.noMedsShort}
                    {p.next_dose?.time ? ` · ${t.nextDose} ${p.next_dose.time}` : ""}
                    {p.doses_missed ? <span className="font-semibold text-warn"> · {fill(t.dosesMissed, { n: p.doses_missed })}</span> : null}
                  </span>
                  {p.care_due.length > 0 && (
                    <span className={`block text-[0.875rem] font-semibold ${overdue ? "text-danger" : "text-warn"}`}>
                      {p.care_due.slice(0, 2).map((c) => c.title).join(", ")}{p.care_due.length > 2 ? ` +${p.care_due.length - 2}` : ""}
                    </span>
                  )}
                </span>
                {!current && <ChevronRight aria-hidden className="h-5 w-5 text-muted" />}
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
