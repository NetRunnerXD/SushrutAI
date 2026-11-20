"use client";

import { Baby, Check, ExternalLink, RotateCcw, Syringe } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useActiveProfile } from "@/components/Family";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, PageTitle, Skeleton } from "@/components/ui/Card";
import { Banner } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

type Item = { code: string; group: string; title: string; title_hi: string; note: string; due_from: string; due_by: string;
              status: "done" | "due" | "overdue" | "soon" | "upcoming"; done_on: string | null };
type Part = { items: Item[]; source: string; url: string };
type Schedule = { child: (Part & { dob: string }) | null; pregnancy: (Part & { lmp: string; weeks: number; due_date: string }) | null; has_dob: boolean };

const TONE: Record<Item["status"], string> = {
  done: "bg-ok-soft text-ok", due: "bg-warn-soft text-warn", overdue: "bg-danger-soft text-danger",
  soon: "bg-info-soft text-info", upcoming: "bg-surface-2 text-muted",
};
const day = (d: string) => new Date(d).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function Timeline({ part, onChange }: { part: Part; onChange: (s: Schedule) => void }) {
  const { t, lang } = useI18n();
  const groups = [...new Set(part.items.map((i) => i.group))];
  const act = async (i: Item) => onChange(await api<Schedule>(`/me/care-schedule/${i.code}`, { method: i.status === "done" ? "DELETE" : "POST",
    body: i.status === "done" ? undefined : JSON.stringify({}) }));
  return (
    <ol className="space-y-4">
      {groups.map((g) => (
        <li key={g}>
          <h3 className="mb-1.5 font-semibold">{g}</h3>
          <ul className="space-y-2">
            {part.items.filter((i) => i.group === g).map((i) => (
              <li key={i.code} className="flex flex-wrap items-center gap-2 rounded-[12px] border border-border p-3">
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{lang === "hi" ? i.title_hi : i.title}</p>
                  <p className="text-[0.875rem] text-muted">
                    {i.status === "done" ? `${t.careDone} ${day(i.done_on!)}` : `${day(i.due_from)} – ${day(i.due_by)}`}</p>
                  {i.note && <p className="text-[0.8125rem] text-muted">{i.note}</p>}
                </div>
                <span className={`rounded-full px-2.5 py-1 text-[0.8125rem] font-semibold ${TONE[i.status]}`}>{t.careStatus[i.status]}</span>
                <button onClick={() => act(i)} aria-label={`${i.status === "done" ? t.careUndo : t.careMarkDone}: ${i.title}`}
                  className={`inline-flex min-h-12 items-center gap-1.5 rounded-full px-3 font-semibold ${i.status === "done" ? "bg-surface-2" : i.status === "due" || i.status === "overdue" ? "bg-brand text-brand-ink" : "border-[1.5px] border-border-input bg-surface"}`}>
                  {i.status === "done" ? <RotateCcw aria-hidden className="h-4 w-4" /> : <Check aria-hidden className="h-4 w-4" />}
                  {i.status === "done" ? t.careUndo : t.careMarkDone}</button>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ol>
  );
}

function SourceLine({ part }: { part: Part }) {
  const { t } = useI18n();
  return (
    <p className="text-[0.8125rem] text-muted">{t.source}: <a href={part.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline">
      {part.source}<ExternalLink aria-hidden className="h-3 w-3" /></a>. {t.careAskHealthWorker}</p>
  );
}

export default function CarePage() {
  const { t } = useI18n();
  const who = useActiveProfile();
  const [s, setS] = useState<Schedule | null>(null);
  const [lmp, setLmp] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api<Schedule>("/me/care-schedule").then(setS).catch((e) => setErr(e.message)); }, []);
  const setPregnancy = async (value: string | null) => {
    setErr(null);
    try { setS(await api<Schedule>("/me/pregnancy", { method: "PUT", body: JSON.stringify({ lmp: value }) })); }
    catch (e: any) { setErr(e.message); }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4 desk:max-w-[1100px]">
      <PageTitle sub={t.careHelp}>{t.careTitle}{who ? ` — ${who.name}` : ""}</PageTitle>
      {err && <Banner tone="warning">{err}</Banner>}
      {!s ? <Skeleton className="h-64" /> : (
        <>
          {s.child ? (
            <Card className="space-y-3">
              <CardHeader icon={Syringe} title={t.careVaccines} sub={fill(t.careBornOn, { date: day(s.child.dob) })} />
              <Timeline part={s.child} onChange={setS} />
              <SourceLine part={s.child} />
            </Card>
          ) : !s.has_dob && (
            <Banner tone="info" title={t.careNeedDob}>
              <Link href="/me" className="inline-flex min-h-12 items-center font-semibold underline">{t.careAddDob}</Link>
            </Banner>
          )}

          <Card className="space-y-3">
            <CardHeader icon={Baby} title={t.carePregnancy}
              sub={s.pregnancy ? fill(t.careWeeks, { n: s.pregnancy.weeks, date: day(s.pregnancy.due_date) }) : t.carePregnancyHelp} />
            {s.pregnancy ? (
              <>
                <Timeline part={s.pregnancy} onChange={setS} />
                <SourceLine part={s.pregnancy} />
                <Button variant="ghost" onClick={() => confirm(t.careEndPregnancyConfirm) && setPregnancy(null)}>{t.careEndPregnancy}</Button>
              </>
            ) : (
              <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (lmp) setPregnancy(lmp); }}>
                <label className="font-semibold">{t.careLmp}
                  <input type="date" required className="input mt-1" value={lmp} max={new Date().toISOString().slice(0, 10)}
                         onChange={(e) => setLmp(e.target.value)} /></label>
                <Button type="submit" disabled={!lmp}>{t.careStartPregnancy}</Button>
              </form>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
