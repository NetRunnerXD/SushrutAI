"use client";

import { Activity, AlertTriangle, Clock } from "lucide-react";
import { useEffect, useState } from "react";
import { Card, CardHeader, Skeleton } from "@/components/ui/Card";
import { api } from "@/lib/api";
import { bcp47, useI18n } from "@/lib/i18n";

export function ClinicalTimelinePanel() {
  const { t, lang } = useI18n();
  const [data, setData] = useState<any>(null);

  useEffect(() => {
    api("/clinical/timeline")
      .then(setData)
      .catch(() => setData({ events: [], observations: [], vitals: [] }));
  }, []);

  if (data === null) return <Skeleton className="h-32" />;
  const events: any[] = data.events ?? [];
  const observations: any[] = data.observations ?? [];
  if (events.length === 0 && observations.length === 0) return null;

  return (
    <Card className="lg:max-w-3xl">
      <CardHeader icon={Activity} title={t.eventTimelineLabel} />
      <ul className="divide-y divide-border">
        {events.slice(0, 30).map((ev: any) => (
          <li key={ev.id} className="flex flex-wrap items-start justify-between gap-2 py-2.5 text-[0.9375rem]">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold">{ev.event_type.replace(/_/g, " ")}</span>
                {ev.discrepancy_flag && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-warn-soft px-2 py-0.5 text-[0.75rem] font-semibold text-warn">
                    <AlertTriangle aria-hidden className="h-3.5 w-3.5" />Discrepancy
                  </span>
                )}
              </div>
              <p className="text-[0.875rem] text-muted">
                {[ev.payload?.name, ev.payload?.status, ev.payload?.duration, ev.payload?.note, ev.payload?.urgency].filter(Boolean).join(" · ") || ev.entity_type}
              </p>
            </div>
            <span className="inline-flex items-center gap-1 text-[0.8125rem] text-muted">
              <Clock aria-hidden className="h-3.5 w-3.5" />
              {ev.occurred_at ? new Date(ev.occurred_at).toLocaleString(bcp47(lang), { dateStyle: "short", timeStyle: "short" }) : ""}
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
