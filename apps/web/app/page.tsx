"use client";

import { AlertTriangle, Droplet, FileText, FlaskConical, HeartPulse, MessageCircle, Scale, Settings2, Stethoscope } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { askHref } from "@/components/AskAbout";
import { DabbaTray } from "@/components/Doses";
import {
  CustomiseHome, FamilyStrip, SimpleHome, TipCard, TodayCard, useDataChanged, useHomeLayout, type HomeCardKey,
} from "@/components/Home";
import { Card, CardHeader, Compartments } from "@/components/ui/Card";
import { DisclaimerChip } from "@/components/ui/Trust";
import { Chip, VitalCard } from "@/components/ui/Medical";
import { VoiceOrb } from "@/components/Voice";
import { api } from "@/lib/api";
import { bcp47, fill, useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";
import { SYMPTOM_CHIPS } from "@/lib/symptoms";
import { HouseholdToday } from "@/components/HouseholdToday";
import { DesktopHome } from "@/components/desktop/DesktopHome";
import { useUiMode } from "@/lib/ui-mode";

function greeting(t: { greetingMorning: string; greetingAfternoon: string; greetingEvening: string }) {
  const h = new Date().getHours();
  return h < 12 ? t.greetingMorning : h < 17 ? t.greetingAfternoon : t.greetingEvening;
}

type Alert = { tone: "danger" | "warning"; text: string; href: string; ask?: string; followUp?: string };
const SIDE: HomeCardKey[] = ["attention", "tip", "vitals", "recent"];

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
  const mode = useUiMode();
  if (prefs.simple) return <SimpleHome />;
  return mode === "desktop" ? <DesktopHome /> : <FullHome />;
}

function FullHome() {
  const { t, lang } = useI18n();
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
    const [labs, rx, chats, vit, doses, care, follow] = await Promise.all([
      api<any[]>("/reports/labs").catch(() => []), api<any[]>("/prescriptions").catch(() => []),
      api<any[]>("/chat/sessions").catch(() => []), api<any[]>("/me/vitals").catch(() => []),
      api<any>("/me/doses").catch(() => ({ items: [] })), api<any>("/me/care-schedule").catch(() => ({ items: [] })),
      api<any[]>("/me/follow-ups?due=true").catch(() => [])]);
    // "Check in with me" items saved from a chat: due today -> ask now (opens the chat with it) or dismiss
    for (const f of follow.slice(0, 2)) out.push({ tone: "warning", text: f.text, href: "/ask", followUp: f.id });
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
    medicines: t.cardMedicines, tip: t.cardTip, vitals: t.cardVitals, recent: t.cardRecent, household: t.householdToday,
  };

  const cards: Record<HomeCardKey, ReactNode> = {
    family: <FamilyStrip />,
    household: <HouseholdToday />,
    today: <TodayCard />,
    voice: (
      <Card className="space-y-4">
        <VoiceOrb row onText={askWith} />
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) askWith(typed); }}>
          <label htmlFor="home-ask" className="sr-only">{t.orType}</label>
          <input id="home-ask" className="input" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={t.orType} />
          <button className="btn btn-outline shrink-0" disabled={!typed.trim()}>{t.send}</button>
        </form>
      </Card>
    ),
    attention: (
      <Card>
        <CardHeader title={t.alerts} />
        {alerts === null ? <div className="skeleton h-12" /> : alerts.length === 0 ? (
          <p className="text-muted">{t.noAlerts}</p>
        ) : (
          <ul className="space-y-2">
            {alerts.map((a, i) => (
              <li key={i} className={`flex flex-wrap items-center gap-2 rounded-[16px] border-[1.5px] p-2 ps-3 ${a.tone === "danger" ? "border-danger/60 bg-danger-soft text-danger" : "border-warn/50 bg-warn-soft text-warn"}`}>
                {a.followUp ? (
                  <>
                    <span className="flex min-h-12 min-w-0 flex-1 items-center gap-2 font-semibold">
                      <MessageCircle aria-hidden className="h-5 w-5 shrink-0" /><span className="min-w-0">{a.text}</span></span>
                    <button type="button" onClick={() => { api(`/me/follow-ups/${a.followUp}`, { method: "DELETE" }).catch(() => {}); askWith(a.text); }}
                      className="inline-flex min-h-12 items-center rounded-full bg-surface px-3 text-[0.875rem] font-semibold text-info shadow-[var(--rim)]">{t.askNow}</button>
                    <button type="button" onClick={() => api(`/me/follow-ups/${a.followUp}`, { method: "DELETE" }).then(load).catch(() => {})}
                      className="inline-flex min-h-12 items-center rounded-full px-3 text-[0.875rem] font-semibold">{t.dismiss}</button>
                  </>
                ) : (
                <Link href={a.href} className="flex min-h-12 min-w-0 flex-1 items-center gap-2 font-semibold">
                  <AlertTriangle aria-hidden className="h-5 w-5 shrink-0" /><span className="min-w-0">{a.text}</span></Link>
                )}
                {a.ask && <Link href={a.ask} className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface px-3 text-[0.875rem] font-semibold text-info shadow-[var(--rim)]">
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
    medicines: <DabbaTray />,
    tip: <TipCard />,
    vitals: (
      <Card>
        <CardHeader title={t.healthSnapshot}
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
          <Link href="/medicines/rx" className="flex min-h-12 items-center gap-2 rounded-[14px] border-[1.5px] border-border-input bg-surface p-2 text-[0.9375rem] font-semibold shadow-[var(--rim)] hover:bg-surface-2">
            <FileText aria-hidden className="h-5 w-5 shrink-0 text-muted" />{t.addPrescription}</Link>
          <Link href="/records?tab=labs" className="flex min-h-12 items-center gap-2 rounded-[14px] border-[1.5px] border-border-input bg-surface p-2 text-[0.9375rem] font-semibold shadow-[var(--rim)] hover:bg-surface-2">
            <FlaskConical aria-hidden className="h-5 w-5 shrink-0 text-muted" />{t.labUpload}</Link>
        </div>
      </Card>
    ),
    recent: recent.length > 0 ? (
      <Card>
        <CardHeader title={t.recent} />
        <ul className="divide-y divide-border">
          {recent.map((r) => (
            <li key={r.id}>
              <Link href={`/ask?session=${r.id}`} className="flex min-h-12 items-center gap-2 rounded-[12px] px-2 hover:bg-surface-2">
                <MessageCircle aria-hidden className="h-5 w-5 shrink-0 text-muted" />
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
  const render = (keys: HomeCardKey[]) => keys.map((k) => cards[k] ? <div key={k} className="min-w-0">{cards[k]}</div> : null);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-x-3 gap-y-2">
        <div>
          <h1 className="text-[2.125rem] leading-[1.1] lg:text-[2.75rem]">{greeting(t)}</h1>
          <p className="mt-1 text-[1.0625rem] font-semibold text-muted">{new Date().toLocaleDateString(bcp47(lang), { weekday: "long", day: "numeric", month: "long" })}</p>
        </div>

      </div>

      {desktop ? (
        <div className="grid grid-cols-12 gap-5">
          <div className="col-span-8 space-y-5">{render(visible.filter((k) => !SIDE.includes(k)))}</div>
          <div className="col-span-4"><Compartments>{render(visible.filter((k) => SIDE.includes(k)))}</Compartments></div>
        </div>
      ) : (
        <div className="space-y-5">{render(visible)}</div>
      )}
      <div className="flex justify-center pt-2">
          <button onClick={() => setCustomise(true)} className="inline-flex min-h-12 items-center gap-1.5 rounded-full border-[1.5px] border-border-input bg-surface px-3.5 text-[0.9375rem] font-semibold shadow-[var(--rim)] hover:bg-surface-2">
          <Settings2 aria-hidden className="h-4 w-4" />{t.customiseHome}</button>
      </div>
      <div className="flex justify-center"><DisclaimerChip /></div>
      <CustomiseHome open={customise} onClose={() => setCustomise(false)} labels={labels} />
    </div>
  );
}
