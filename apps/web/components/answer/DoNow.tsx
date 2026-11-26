"use client";

import { BellRing, Check, CircleAlert, ClipboardPlus, Leaf } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { AnswerSection, SectionAction } from "./Section";

/** What to do now: home-care steps as a checklist, warning signs, and two hand-offs to the rest of the app —
 *  "check in with me tomorrow" (a follow-up on Home) and "log in my health diary" (today's check-in). */
export function DoNow({ care, watch, question, symptomatic, defaultOpen }: {
  care?: string[]; watch?: string[]; question?: string; symptomatic: boolean; defaultOpen?: boolean;
}) {
  const { t } = useI18n();
  const [done, setDone] = useState<Set<number>>(new Set());
  const [reminded, setReminded] = useState(false);
  const [logged, setLogged] = useState(false);
  if (!care?.length && !watch?.length) return null;
  const topic = (question ?? "").replace(/\s+/g, " ").trim().slice(0, 80);
  const toggle = (i: number) => setDone((s) => { const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n; });
  return (
    <AnswerSection id="donow" icon={Leaf} title={t.selfCare} defaultOpen={defaultOpen}
      summary={care?.length ? fill(t.nOfMDone, { n: done.size, m: care.length }) : undefined}>
      {!!care?.length && (
        <ul className="space-y-1.5">
          {care.map((x, i) => (
            <li key={i}>
              <label className="flex min-h-12 cursor-pointer items-start gap-2.5 rounded-[12px] px-1 py-1 hover:bg-surface">
                <input type="checkbox" checked={done.has(i)} onChange={() => toggle(i)} className="mt-1 h-5 w-5 shrink-0 accent-[var(--brand)]" />
                <span className={done.has(i) ? "text-muted line-through" : ""}>{x}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {!!watch?.length && (
        <div className="rounded-[14px] border-[1.5px] border-warn/50 bg-warn-soft p-3 text-warn">
          <p className="mb-1 flex items-center gap-1.5 font-semibold"><CircleAlert aria-hidden className="h-5 w-5" />{t.watchFor}</p>
          <ul className="list-disc space-y-0.5 ps-6 text-text">{watch.map((x, i) => <li key={i}>{x}</li>)}</ul>
        </div>
      )}
      {symptomatic && topic && (
        <div className="flex flex-wrap gap-2">
          <SectionAction icon={BellRing} done={reminded}
            onClick={() => api("/me/follow-ups", { method: "POST", body: JSON.stringify({ text: fill(t.followUpText, { topic }).slice(0, 200), in_days: 1 }) })
              .then(() => setReminded(true)).catch(() => {})}>{reminded ? t.reminderSet : t.remindCheckIn}</SectionAction>
          <SectionAction icon={ClipboardPlus} done={logged}
            onClick={() => api("/me/checkins", { method: "POST", body: JSON.stringify({ mood: "unwell", note: topic.slice(0, 300) }) })
              .then(() => setLogged(true)).catch(() => {})}>{logged ? t.trackedSymptom : t.trackSymptom}</SectionAction>
          {done.size > 0 && done.size === care?.length && <span className="inline-flex items-center gap-1 text-[0.875rem] font-semibold text-ok"><Check aria-hidden className="h-4 w-4" />{t.allDone}</span>}
        </div>
      )}
    </AnswerSection>
  );
}
