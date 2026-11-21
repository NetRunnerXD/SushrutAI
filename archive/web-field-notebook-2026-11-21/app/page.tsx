"use client";

import { AlertTriangle, Droplet, FileText, FlaskConical, HeartPulse, MessageCircle, Scale, Settings2, Stethoscope } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { askHref } from "@/components/AskAbout";
import { DoseTimeline } from "@/components/Doses";
import {
  CustomiseHome, FamilyStrip, SimpleHome, TipCard, TodayCard, useDataChanged, useHomeLayout, type HomeCardKey,
} from "@/components/Home";
import { Card, CardHeader, Scribble } from "@/components/ui/Card";
import { Chip, VitalCard } from "@/components/ui/Medical";
import { VoiceOrb } from "@/components/Voice";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";
import { SYMPTOM_CHIPS } from "@/lib/symptoms";

function greeting(t: { greetingMorning: string; greetingAfternoon: string; greetingEvening: string }) {
  const h = new Date().getHours();
  return h < 12 ? t.greetingMorning : h < 17 ? t.greetingAfternoon : t.greetingEvening;
}

type Alert = { tone: "danger" | "warning"; text: string; href: string; ask?: string };
const SIDE: HomeCardKey[] = ["medicines", "tip", "vitals"];

function useIsDesktop() {
  const [d, setD] = useState(false);
  useEffect(() => {
    const m = window.matchMedia("(min-width: 1024px)");
    const on = () => setD(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return d;
}

export default function Home() {
  const { prefs } = usePrefs();
  return prefs.simple ? <SimpleHome /> : <FullHome />;
}

function FullHome() {
  const { t } = useI18n();
  const router = useRouter();
  const desktop = useIsDesktop();
  const { order, hidden } = useHomeLayout();
  const [customise, setCustomise] = useState(false);
  const [typed, setTyped] = useState("");
  const [alerts, setAlerts] = useState<Alert[] | null>(null);
  const [recent, setRecent] = useState<any[]>([]);
  const [vitals, setVitals] = useState<Record<string, any>>({});

  const askWith = (text: string) => {
    try { sessionStorage.setItem("sushrut.pendingAsk", text); } catch { /* ignore */ }
    router.push("/ask");
  };

  const load = useCallback(async () => {
    const out: Alert[] = [];
    const [labs, rx, chats, vit, doses, care] = await Promise.all([
      api<any[]>("/reports/labs").catch(() => []), api<any[]>("/prescriptions").catch(() => []),
      api<any[]>("/chat/sessions").catch(() => []), api<any[]>("/me/vitals").catch(() => []),
      api<any>("/me/doses").catch(() => ({ items: [] })), api<any>("/me/care-schedule").catch(() => ({ items: [] }))]);
    for (const r of rx) for (const a of (r.alerts ?? []).filter((a: any) => a.level === "high").slice(0, 1)) {
      out.push({ tone: "danger", text: a.text, href: `/medicines/rx?id=${r.id}` });
    }
    for (const l of labs.filter((x) => x.flag === "H" || x.flag === "L").slice(-3)) {
      const label = `${l.test_name}: ${l.value} ${l.unit ?? ""} — ${l.flag === "H" ? t.flagHigh : t.flagLow}`;
      out.push({ tone: "danger", text: label, href: "/records?tab=labs", ask: askHref(`lab:${l.id}`, label) });
    }
    for (const c of (care.items ?? []).filter((c: any) => c.status === "overdue" || c.status === "due").slice(0, 2)) {
      out.push({ tone: c.status === "overdue" ? "danger" : "warning",
                 text: fill(c.status === "overdue" ? t.careOverdue : t.careDue, { item: c.title }), href: "/me/care" });
    }
    const seen = new Set<string>();
    for (const d of (doses.items ?? []).filter((d: any) => d.days_left != null && d.days_left <= 3)) {
      if (seen.has(d.medication_id)) continue;
      seen.add(d.medication_id);
      out.push({ tone: "warning", text: fill(t.refillSoon, { name: d.name, n: Math.max(0, d.days_left) }), href: "/medicines" });
    }
    for (const r of rx.filter((x) => x.status === "draft").slice(0, 2)) {
      out.push({ tone: "warning", text: `${t.rxReview}: ${r.medications?.map((m: any) => m.name).join(", ") || t.rxTitle}`, href: `/medicines/rx?id=${r.id}` });
    }
    setAlerts(out.slice(0, 5));
    setRecent(chats.slice(0, 3));
    const latest: Record<string, any> = {};
    for (const v of vit) latest[v.type] = v;
    setVitals(latest);
  }, [t]);
  useEffect(() => { load(); }, [load]);
  useDataChanged(load);

  const bp = vitals.bp_sys && vitals.bp_dia ? `${vitals.bp_sys.value}/${vitals.bp_dia.value}` : "—";
  const labels: Record<HomeCardKey, string> = {
    family: t.cardFamily, today: t.cardToday, voice: t.cardVoice, attention: t.cardAttention, symptoms: t.cardSymptoms,
    medicines: t.cardMedicines, tip: t.cardTip, vitals: t.cardVitals, recent: t.cardRecent,
  };

  const cards: Record<HomeCardKey, ReactNode> = {
    family: <FamilyStrip />,
    today: <TodayCard />,
    voice: (
      <Card className="relative pt-6 text-center">
        <span aria-hidden className="absolute -top-2.5 left-1/2 h-5 w-24 -translate-x-1/2 rotate-[-2deg] rounded-[2px] bg-accent/70" />
        <h2 className="mb-1 text-[1.375rem]">{t.cardVoice}</h2>
        <VoiceOrb size="md" onText={askWith} />
        <form className="mx-auto mt-4 flex max-w-lg gap-2" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) askWith(typed); }}>
          <label htmlFor="home-ask" className="sr-only">{t.orType}</label>
          <input id="home-ask" className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={t.orType} />
          <button className="btn btn-primary shrink-0" disabled={!typed.trim()}>{t.send}</button>
        </form>
      </Card>
    ),
    attention: (
      <Card>
        <CardHeader icon={AlertTriangle} title={t.alerts} />
        {alerts === null ? <div className="skeleton h-12" /> : alerts.length === 0 ? (
          <p className="text-muted">{t.noAlerts}</p>
        ) : (
          <ul className="space-y-2">
            {alerts.map((a, i) => (
              <li key={i} className={`flex flex-wrap items-center gap-2 rounded-[3px_8px_8px_3px] border-l-[5px] p-2 pl-3 ${a.tone === "danger" ? "border-danger bg-danger-soft text-danger" : "border-warn bg-warn-soft text-warn"}`}>
                <Link href={a.href} className="flex min-h-12 min-w-0 flex-1 items-center gap-2 font-semibold">
                  <AlertTriangle aria-hidden className="h-5 w-5 shrink-0" /><span className="min-w-0">{a.text}</span></Link>
                {a.ask && <Link href={a.ask} className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface px-3 text-[0.875rem] font-semibold text-info">
                  <MessageCircle aria-hidden className="h-4 w-4" />{t.explainThis}</Link>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    ),
    symptoms: (
      <section aria-labelledby="chips-h">
        <h2 id="chips-h" className="mb-2">{t.quickSymptoms}</h2>
        <div className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
          {SYMPTOM_CHIPS.map((c) => (
            <Chip key={c.key} icon={c.icon} onClick={() => router.push(`/ask/check?c=${encodeURIComponent(c.complaint)}`)}>
              {t.symptoms[c.key]}
            </Chip>
          ))}
        </div>
      </section>
    ),
    medicines: <DoseTimeline compact />,
    tip: <TipCard />,
    vitals: (
      <Card>
        <CardHeader icon={Stethoscope} title={t.healthSnapshot}
          action={<Link href="/records?tab=vitals" className="inline-flex min-h-12 items-center text-[0.9375rem] font-semibold text-info underline">{t.addReading}</Link>} />
        <div className="grid grid-cols-2 gap-2">
          <VitalCard icon={HeartPulse} label="BP" value={bp} unit="mmHg"
            status={vitals.bp_sys ? (vitals.bp_sys.value >= 140 || vitals.bp_dia?.value >= 90 ? "high" : "ok") : undefined} />
          <VitalCard icon={Droplet} label="Sugar (F)" value={vitals.glucose_f ? `${vitals.glucose_f.value}` : "—"} unit="mg/dL"
            status={vitals.glucose_f ? (vitals.glucose_f.value >= 126 ? "high" : vitals.glucose_f.value < 70 ? "low" : "ok") : undefined} />
          <VitalCard icon={Scale} label="Weight" value={vitals.weight ? `${vitals.weight.value}` : "—"} unit="kg" />
          <VitalCard icon={HeartPulse} label="Pulse" value={vitals.hr ? `${vitals.hr.value}` : "—"} unit="bpm" />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2">
          <Link href="/medicines/rx" className="flex min-h-12 items-center gap-2 rounded-[6px] border-[1.5px] border-dashed border-border-input p-2 text-[0.9375rem] font-semibold hover:bg-accent-soft">
            <FileText aria-hidden className="h-5 w-5 shrink-0 text-brand" />{t.addPrescription}</Link>
          <Link href="/records?tab=labs" className="flex min-h-12 items-center gap-2 rounded-[6px] border-[1.5px] border-dashed border-border-input p-2 text-[0.9375rem] font-semibold hover:bg-accent-soft">
            <FlaskConical aria-hidden className="h-5 w-5 shrink-0 text-brand" />{t.labUpload}</Link>
        </div>
      </Card>
    ),
    recent: recent.length > 0 ? (
      <Card>
        <CardHeader icon={MessageCircle} title={t.recent} />
        <ul className="space-y-1">
          {recent.map((r) => (
            <li key={r.id}>
              <Link href={`/ask?session=${r.id}`} className="flex min-h-12 items-center gap-2 border-b border-dashed border-border px-2 hover:bg-surface-2">
                <MessageCircle aria-hidden className="h-5 w-5 shrink-0 text-brand" />
                <span className="min-w-0 flex-1 truncate">{r.title ?? t.continueChat}</span>
                <span className="font-mono text-[0.875rem] text-muted">{new Date(r.started_at).toLocaleDateString()}</span>
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    ) : null,
  };

  const visible = order.filter((k) => !hidden.has(k));
  // each card settles a beat after the one before it (CSS var inherited by the Card's .note animation)
  const render = (keys: HomeCardKey[], from = 0) => keys.map((k, i) => cards[k]
    ? <div key={k} className="min-w-0" style={{ ["--i" as string]: Math.min(from + i, 6) }}>{cards[k]}</div> : null);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-x-3 gap-y-2">
        <div>
          <p className="font-mono text-[0.9375rem] text-muted">{new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}</p>
          <h1 className="text-[2rem] leading-tight lg:text-[2.5rem]">{greeting(t)}</h1>
          <Scribble />
        </div>
        <button onClick={() => setCustomise(true)} className="inline-flex min-h-12 items-center gap-1.5 rounded-full border border-border-input bg-surface px-3.5 text-[0.9375rem] font-semibold hover:bg-accent-soft">
          <Settings2 aria-hidden className="h-4 w-4" />{t.customiseHome}</button>
      </div>

      {desktop ? (
        <div className="grid grid-cols-12 gap-5">
          <div className="col-span-8 space-y-5">{render(visible.filter((k) => !SIDE.includes(k)))}</div>
          <div className="col-span-4 space-y-5">{render(visible.filter((k) => SIDE.includes(k)), 1)}</div>
        </div>
      ) : (
        <div className="space-y-5">{render(visible)}</div>
      )}
      <CustomiseHome open={customise} onClose={() => setCustomise(false)} labels={labels} />
    </div>
  );
}
