"use client";

import { AlertTriangle, Check, ChevronRight, MessageCircle, Moon, Sun, SunDim, Volume2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useActiveProfile } from "@/components/Family";
import { nextDose, useDataChanged, useDoses } from "@/components/Home";
import { Suggestions } from "@/components/Suggestions";
import { Sparkline, VITAL_META } from "@/components/Trends";
import { VoiceOrb } from "@/components/Voice";
import { DisclaimerChip } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { speak } from "@/lib/voice";
import { DayCalendar } from "./DayCalendar";
import { DoseRail } from "./DoseRail";

type Alert = { tone: "danger" | "warning"; text: string; href: string };

/** Desktop dashboard (docs/DESKTOP_UI_SPEC.md §4.1): greeting and a one-line summary over a 12-column bento —
 *  voice, today's medicines, the care calendar, and a side column of attention, vitals, recent chats and the tip. */
export function DesktopHome() {
  const { t, lang } = useI18n();
  const router = useRouter();
  const who = useActiveProfile();
  const { items } = useDoses();
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [toCheck, setToCheck] = useState(0);
  const [vitals, setVitals] = useState<any[]>([]);
  const [chats, setChats] = useState<any[]>([]);
  const [tip, setTip] = useState<any>(null);
  const [typed, setTyped] = useState("");
  const askWith = (text: string) => { try { sessionStorage.setItem("sushrut.pendingAsk", text); } catch { /* ignore */ } router.push("/ask"); };

  const load = useCallback(async () => {
    const [labs, care, follow, lib, vit, ch] = await Promise.all([
      api<any[]>("/reports/labs").catch(() => []), api<any>("/me/care-schedule").catch(() => ({ items: [] })),
      api<any[]>("/me/follow-ups?due=true").catch(() => []), api<any[]>("/reports/library").catch(() => []),
      api<any[]>("/me/vitals").catch(() => []), api<any[]>("/chat/sessions").catch(() => [])]);
    const out: Alert[] = [];
    for (const f of follow.slice(0, 2)) out.push({ tone: "warning", text: f.text, href: "/ask" });
    for (const l of labs.filter((x: any) => x.flag === "H" || x.flag === "L").slice(-3))
      out.push({ tone: "danger", text: `${l.test_name}: ${l.value ?? l.value_text} ${l.unit ?? ""} — ${l.flag === "H" ? t.flagHigh : t.flagLow}`, href: "/records?tab=labs" });
    for (const c of (care.items ?? []).filter((c: any) => c.status === "overdue" || c.status === "due").slice(0, 2))
      out.push({ tone: c.status === "overdue" ? "danger" : "warning", text: fill(c.status === "overdue" ? t.careOverdue : t.careDue, { item: c.title }), href: "/me/care" });
    setAlerts(out.slice(0, 5));
    setToCheck(lib.filter((d: any) => d.kind === "lab_report" && !d.reviewed).length);
    setVitals(vit);
    setChats(ch.slice(0, 4));
  }, [t]);
  useEffect(() => { load(); }, [load]);
  useDataChanged(load);
  useEffect(() => { api(`/me/tip?lang=${lang}`).then(setTip).catch(() => {}); }, [lang]);

  const h = new Date().getHours();
  const hello = h < 12 ? t.greetingMorning : h < 17 ? t.greetingAfternoon : t.greetingEvening;
  const pending = (items ?? []).filter((i) => i.slot !== "as_needed" && !i.status).length;
  const summary = [pending ? fill(pending === 1 ? t.doseLeftToday : t.dosesLeftToday, { n: pending }) : null, toCheck ? fill(t.reportsToCheck, { n: toCheck }) : null]
    .filter(Boolean).join(" · ") || t.allCaughtUp;

  return (
    <div className="space-y-6">
      <header className="pt-2">
        <h1 className="display text-[2.5rem] font-extrabold leading-[1.1] min-[1600px]:text-[2.75rem]">{hello}{who ? `, ${who.name}` : ""}</h1>
        <p className="mt-1.5 text-[1.0625rem] text-muted">{summary}</p>
      </header>

      <div className="d-stagger grid grid-cols-8 gap-4 min-[1280px]:grid-cols-12 min-[1600px]:gap-5">
        {/* Voice: the full breadth — the well on the left, the field and personal suggestions beside it */}
        <section aria-label={t.cardVoice} className="d-tile col-span-full grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-8 gap-y-3 p-6">
          <div className="max-w-[300px]"><VoiceOrb row onText={askWith} /></div>
          <div className="min-w-0 space-y-3">
            <Suggestions sessionId={null} refreshKey={0} onSend={askWith} onEdit={(q) => setTyped(q)} />
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) askWith(typed); }}>
              <label htmlFor="d-ask" className="sr-only">{t.orType}</label>
              <input id="d-ask" className="input min-h-11 flex-1" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={t.orType} />
            </form>
          </div>
        </section>

        {/* Today's medicines, collapsed to one rail: stations and times only; a station opens its medicines */}
        <DoseRail />

        {/* Care calendar */}
        <div className="col-span-8"><DayCalendar /></div>

        {/* Side column */}
        <div className="col-span-8 grid grid-cols-2 gap-4 min-[1280px]:col-span-4 min-[1280px]:grid-cols-1 min-[1600px]:gap-5">
          <section aria-label={t.alerts} className="d-tile space-y-2.5 p-5">
            <h2 className="text-[1.0625rem] font-bold">{t.alerts}</h2>
            {alerts === null ? <div className="skeleton h-16" /> : alerts.length === 0 ? <p className="text-muted">{t.noAlerts}</p> : (
              <ul className="space-y-1.5">
                {alerts.map((a, i) => (
                  <li key={i}>
                    <Link href={a.href} className={`flex min-h-11 items-center gap-2 rounded-[12px] border px-3 py-2 text-[0.9375rem] font-semibold transition-colors duration-[120ms]
                      ${a.tone === "danger" ? "border-danger/40 bg-danger-soft text-danger" : "border-warn/40 bg-warn-soft text-warn"}`}>
                      <AlertTriangle aria-hidden className="h-4 w-4 shrink-0" /><span className="min-w-0 flex-1">{a.text}</span>
                      <ChevronRight aria-hidden className="h-4 w-4 shrink-0" /></Link>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <Vitals vitals={vitals} />
          <section aria-label={t.cardRecent} className="d-tile space-y-2 p-5">
            <h2 className="text-[1.0625rem] font-bold">{t.cardRecent}</h2>
            {chats.length === 0 ? <p className="text-muted">{t.noChats}</p> : (
              <ul className="space-y-0.5">
                {chats.map((c) => (
                  <li key={c.id}><Link href={`/ask?session=${c.id}`} className="flex min-h-11 items-center gap-2.5 rounded-[12px] px-2 hover:bg-[var(--d-tile-hover)]">
                    <MessageCircle aria-hidden className="h-4 w-4 shrink-0 text-muted" /><span className="min-w-0 flex-1 truncate">{c.title}</span></Link></li>
                ))}
              </ul>
            )}
          </section>
          {tip && (
            <section aria-label={t.seasonTip} className="d-tile space-y-2 p-5">
              <div className="flex items-center justify-between gap-2"><h2 className="text-[1.0625rem] font-bold">{t.seasonTip}</h2>
                <button type="button" aria-label={t.readAloud} onClick={() => speak(tip.text, lang)} className="grid h-10 w-10 place-items-center rounded-full hover:bg-[var(--d-tile-hover)]"><Volume2 aria-hidden className="h-4 w-4" /></button></div>
              <p className="text-[0.9375rem] leading-relaxed">{tip.text}</p>
              <p className="text-[0.8125rem] text-muted">{t.source}: <a href={tip.url} target="_blank" rel="noreferrer" className="underline">{tip.source}</a></p>
            </section>
          )}
        </div>
      </div>
      <div className="flex justify-center pb-2"><DisclaimerChip /></div>
    </div>
  );
}

function Vitals({ vitals }: { vitals: any[] }) {
  const { t } = useI18n();
  const series = (type: string) => vitals.filter((v) => v.type === type).map((v) => ({ t: +new Date(v.measured_at), v: v.value })).sort((a, b) => a.t - b.t);
  const rows: [string, string, string | null, any[]][] = [];
  const sys = series("bp_sys"), dia = series("bp_dia");
  if (sys.length && dia.length) rows.push(["bp_sys", "BP", `${sys.at(-1)!.v}/${dia.at(-1)!.v}`, sys]);
  for (const k of ["glucose_f", "weight"]) { const s = series(k); if (s.length) rows.push([k, VITAL_META[k].label, `${s.at(-1)!.v}`, s]); }
  return (
    <section aria-label={t.healthSnapshot} className="d-tile space-y-2 p-5">
      <div className="flex items-baseline justify-between gap-2"><h2 className="text-[1.0625rem] font-bold">{t.healthSnapshot}</h2>
        <Link href="/records?tab=vitals" className="text-[0.875rem] font-semibold text-info underline underline-offset-4">{t.addReading}</Link></div>
      {rows.length === 0 ? <p className="text-muted">{t.noReadings}</p> : (
        <ul className="divide-y divide-[var(--d-tile-line)]">
          {rows.map(([k, label, val, pts]) => (
            <li key={k} className="flex items-center gap-3 py-2">
              <span className="min-w-0 flex-1"><span className="block text-[0.8125rem] font-semibold text-muted">{label}</span>
                <span className="font-mono text-[1.375rem] font-bold">{val} <span className="text-[0.8125rem] font-normal text-muted">{k === "bp_sys" ? "mmHg" : VITAL_META[k].unit}</span></span></span>
              <Sparkline points={pts} lo={VITAL_META[k]?.lo} hi={VITAL_META[k]?.hi} label={label} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
