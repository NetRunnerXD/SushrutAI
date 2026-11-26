"use client";

import { ChevronDown, ChevronRight, FileText, Mic, MessageSquare, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, EmptyState, Skeleton } from "@/components/ui/Card";
import { UrgencyPill } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { bcp47, useI18n } from "@/lib/i18n";

export function EncountersPanel() {
  const { t, lang } = useI18n();
  const [list, setList] = useState<any[] | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Record<string, any>>({});

  useEffect(() => {
    api<{ encounters: any[] }>("/clinical/encounters")
      .then((r) => setList(r.encounters ?? []))
      .catch(() => setList([]));
  }, []);

  async function toggle(id: string) {
    if (openId === id) {
      setOpenId(null);
      return;
    }
    setOpenId(id);
    if (!detail[id]) {
      try {
        const d = await api(`/clinical/encounters/${id}`);
        setDetail((prev) => ({ ...prev, [id]: d }));
      } catch {
        setDetail((prev) => ({ ...prev, [id]: { turns: [], followups: [], observations: [] } }));
      }
    }
  }

  if (list === null) return <Skeleton className="h-44" />;
  if (list.length === 0) return <EmptyState icon={FileText} art="records" text={t.noEncountersYet} />;

  return (
    <div className="space-y-3 lg:max-w-4xl">
      {list.map((e) => {
        const isOpen = openId === e.id;
        const d = detail[e.id];
        return (
          <Card key={e.id} className="overflow-hidden">
            <button
              type="button"
              onClick={() => toggle(e.id)}
              aria-expanded={isOpen}
              className="flex min-h-14 w-full items-center gap-3 text-start"
            >
              <span aria-hidden className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-surface-2 shadow-[var(--well)]">
                {e.channel === "voice" ? <Mic className="h-5 w-5 text-brand" /> : <MessageSquare className="h-5 w-5" />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[0.8125rem] font-semibold text-muted">
                  {new Date(e.started_at).toLocaleString(bcp47(lang), { dateStyle: "medium", timeStyle: "short" })}
                  {" · "}{e.language?.toUpperCase()}
                  {" · "}{e.turn_count} turns
                </span>
                <span className="display block truncate text-[1.0625rem] font-bold">
                  {e.chief_complaint || "Consultation"}
                </span>
              </span>
              {e.urgency && <UrgencyPill urgency={e.urgency} />}
              {isOpen ? <ChevronDown aria-hidden className="h-5 w-5 shrink-0 text-muted" /> : <ChevronRight aria-hidden className="h-5 w-5 shrink-0 text-muted" />}
            </button>

            {isOpen && (
              <div className="mt-3 space-y-3 border-t border-border pt-3">
                {!d ? <Skeleton className="h-20" /> : (
                  <>
                    {d.safety_alerts?.length > 0 && (
                      <div className="rounded-[12px] border border-danger bg-danger-soft p-3 text-[0.9375rem] text-danger">
                        <p className="flex items-center gap-1.5 font-bold"><ShieldAlert className="h-4 w-4" />Safety escalation</p>
                        {d.safety_alerts.map((a: any) => <p key={a.id}>{a.guidance}</p>)}
                      </div>
                    )}

                    {d.turns?.length > 0 && (
                      <div className="space-y-2">
                        {d.turns.map((turn: any) => (
                          <div key={turn.id} className={`rounded-[12px] p-3 text-[0.9375rem] ${turn.role === "user" ? "bg-surface-2" : "border border-border bg-surface"}`}>
                            <p className="text-[0.75rem] font-semibold uppercase tracking-wide text-muted">
                              {turn.role} · {turn.detected_lang}
                              {turn.code_switched ? ` · ${t.codeSwitchedBadge}` : ""}
                              {turn.asr_confidence != null ? ` · conf ${(turn.asr_confidence * 100).toFixed(0)}%` : ""}
                            </p>
                            {turn.role === "user" && turn.normalized_transcript && turn.normalized_transcript !== turn.raw_transcript ? (
                              <div className="mt-1 space-y-1">
                                <p><span className="text-[0.8125rem] text-muted">{t.rawTranscriptLabel}: </span>{turn.raw_transcript}</p>
                                <p><span className="text-[0.8125rem] text-muted">{t.normalizedTranscriptLabel}: </span>{turn.normalized_transcript}</p>
                              </div>
                            ) : (
                              <p className="mt-1 whitespace-pre-wrap">{turn.raw_transcript}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}

                    {d.followups?.length > 0 && (
                      <div className="space-y-1.5">
                        <p className="text-[0.875rem] font-bold text-muted">{t.followUps}</p>
                        {d.followups.map((fq: any) => (
                          <div key={fq.id} className="rounded-[12px] bg-surface-2 px-3 py-2 text-[0.9375rem]">
                            <p className="font-semibold">Q: {fq.question}</p>
                            <p className="text-muted">{fq.answer ? `A: ${fq.answer}` : "Pending"}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}
