"use client";

import { BookOpen, ChevronDown, ListOrdered, MessageCircle, Stethoscope } from "lucide-react";
import { useState } from "react";
import { UrgencyPill, type Urgency } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { AnswerSection, SectionAction } from "./Section";

const LIKELY_W: Record<string, string> = { high: "w-full", moderate: "w-2/3", low: "w-1/3" };
type Topic = { title: string; url: string; source: string; snippet: string } | null;

/** Possible causes: each one opens to what fits / what doesn't, and actions (ask about it, learn more from a trusted
 *  page, save it for the doctor). */
export function Causes({ conditions, onAsk, defaultOpen }: { conditions: any[]; onAsk?: (q: string) => void; defaultOpen?: boolean }) {
  const { t } = useI18n();
  if (!conditions.length) return null;
  return (
    <AnswerSection id="causes" icon={ListOrdered} title={t.possibleConditions} summary={conditions.length} defaultOpen={defaultOpen}>
      <ol className="space-y-2">
        {conditions.map((c, i) => <Cause key={`${c.name}-${i}`} c={c} i={i} onAsk={onAsk} />)}
      </ol>
    </AnswerSection>
  );
}

function Cause({ c, i, onAsk }: { c: any; i: number; onAsk?: (q: string) => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [topic, setTopic] = useState<Topic | undefined>(undefined);
  const [saved, setSaved] = useState(false);
  const en = c.name_en || c.name;
  const [loading, setLoading] = useState(false);
  const learn = () => {
    setLoading(true);
    api<Topic>(`/search/topic?q=${encodeURIComponent(en)}`).then((x) => setTopic(x ?? null)).catch(() => setTopic(null))
      .finally(() => setLoading(false));
  };
  return (
    <li className="rounded-[14px] bg-surface p-2.5 shadow-[var(--rim)]">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="w-full text-start">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-semibold">{i + 1}. {c.name}{c.name_en && c.name_en !== c.name ? <span lang="en" className="font-normal text-muted"> ({c.name_en})</span> : null}</span>
          <span className="flex items-center gap-2">
            {c.urgency && <UrgencyPill urgency={c.urgency as Urgency} />}
            <span className="text-[0.875rem] text-muted">{t.likelihood[c.likelihood] ?? c.likelihood}</span>
            <ChevronDown aria-hidden className={`h-4 w-4 text-muted transition-transform ${open ? "rotate-180" : ""}`} />
          </span>
        </div>
        <div aria-hidden className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface-2 shadow-[var(--well)]">
          <div className={`h-full rounded-full bg-text ${LIKELY_W[c.likelihood] ?? "w-1/3"}`} />
        </div>
      </button>
      {open && (
        <div className="mt-2.5 space-y-2.5 text-[0.9375rem]">
          {!!c.supporting?.length && <p><span className="font-semibold">{t.whatFits}: </span>{c.supporting.join("; ")}</p>}
          {!!c.against?.length && <p className="text-muted"><span className="font-semibold">{t.whatDoesntFit}: </span>{c.against.join("; ")}</p>}
          <div className="flex flex-wrap gap-2">
            {onAsk && <SectionAction icon={MessageCircle} onClick={() => onAsk(`${t.tellMeAbout} ${c.name}`)}>{t.askAboutThis}</SectionAction>}
            <SectionAction icon={BookOpen} onClick={learn} disabled={loading || topic !== undefined}>{t.learnMore}</SectionAction>
            <SectionAction icon={Stethoscope} done={saved}
              onClick={() => api("/me/visit-questions", { method: "POST", body: JSON.stringify({ text: `${t.couldItBe} ${c.name}?`.slice(0, 300), source: "chat" }) })
                .then(() => setSaved(true)).catch(() => {})}>{saved ? t.savedForDoctor : t.saveForDoctor}</SectionAction>
          </div>
          {topic !== undefined && (
            topic ? (
              <div className="rounded-[12px] border border-border bg-surface-2 p-2.5">
                <a href={topic.url} target="_blank" rel="noreferrer" className="font-semibold text-info underline">{topic.title}</a>
                <p className="mt-1 text-[0.875rem] text-muted">{topic.snippet}</p>
              </div>
            ) : <p className="text-[0.875rem] text-muted">{t.noTrustedPage}</p>
          )}
        </div>
      )}
    </li>
  );
}
