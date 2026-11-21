"use client";

import {
  ArrowDown, ArrowUp, BellRing, Check, Eye, EyeOff, Frown, Leaf, Meh, Mic, Pill, Plus, Siren, Smile, Stethoscope, Volume2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Avatar, useActiveProfile, type Member } from "@/components/Family";
import { useSOS } from "@/components/Emergency";
import { Card, CardHeader } from "@/components/ui/Card";
import { Sheet } from "@/components/ui/Sheet";
import { api, switchProfile } from "@/lib/api";
import { bcp47, fill, useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";
import { speak } from "@/lib/voice";

// ---------------------------------------------------------------- data hooks
type DoseItem = { medication_id: string; name: string; slot: string; time: string | null; status: string | null; days_left: number | null };

/** Refetch when a voice action or another card changes data. */
export function useDataChanged(fn: () => void) {
  useEffect(() => {
    window.addEventListener("sushrut:data-changed", fn);
    return () => window.removeEventListener("sushrut:data-changed", fn);
  }, [fn]);
}

export function useDoses() {
  const [items, setItems] = useState<DoseItem[] | null>(null);
  const load = useCallback(() => { api<any>("/me/doses").then((d) => setItems(d.items)).catch(() => setItems([])); }, []);
  useEffect(load, [load]);
  useDataChanged(load);
  return { items, reload: load };
}

function minutesUntil(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  const now = new Date();
  return h * 60 + m - (now.getHours() * 60 + now.getMinutes());
}

/** The next scheduled dose today that is not yet marked (earliest first). */
export function nextDose(items: DoseItem[] | null): DoseItem | null {
  return (items ?? []).filter((i) => i.slot !== "as_needed" && i.time && i.status !== "taken" && i.status !== "skipped")
    .sort((a, b) => (a.time! < b.time! ? -1 : 1))[0] ?? null;
}

async function markTaken(d: DoseItem) {
  await api("/me/doses", { method: "POST", body: JSON.stringify({ medication_id: d.medication_id, slot: d.slot, status: "taken" }) });
  window.dispatchEvent(new Event("sushrut:data-changed"));
}

// ---------------------------------------------------------------- Today: check-in + next dose
export function TodayCard() {
  const { t } = useI18n();
  const [mood, setMood] = useState<string | null | undefined>(undefined);
  const { items } = useDoses();
  useEffect(() => { api<any>("/me/checkins?days=1").then((r) => setMood(r.today)).catch(() => setMood(null)); }, []);
  const choose = async (m: string) => {
    setMood(m);
    await api("/me/checkins", { method: "POST", body: JSON.stringify({ mood: m }) }).catch(() => {});
  };
  const moods: [string, string, typeof Smile, string][] = [
    ["good", t.moodGood, Smile, "bg-ok-soft text-ok"], ["okay", t.moodOkay, Meh, "bg-warn-soft text-warn"],
    ["unwell", t.moodUnwell, Frown, "bg-danger-soft text-danger"]];
  const next = nextDose(items);
  const mins = next?.time ? minutesUntil(next.time) : null;
  const when = mins == null ? "" : mins <= 0 ? (mins > -60 ? t.dueNow : t.overdue)
    : mins < 60 ? fill(t.inMinutes, { n: mins }) : fill(t.inHours, { h: Math.floor(mins / 60), m: mins % 60 });

  return (
    <Card className="space-y-4">
      <div role="group" aria-labelledby="checkin-h">
        <h2 id="checkin-h" className="mb-2 text-[1.25rem]">{t.howFeeling}</h2>
        <div className="grid grid-cols-3 gap-2">
          {moods.map(([k, label, Icon, tone]) => (
            <button key={k} onClick={() => choose(k)} aria-pressed={mood === k}
              className={`flex min-h-20 flex-col items-center justify-center gap-1 rounded-[6px] border-2 font-semibold transition-colors
                ${mood === k ? `${tone} stamp border-current` : "border-transparent bg-surface-2 hover:border-border-input"}`}>
              <Icon aria-hidden className="h-7 w-7" />{label}
            </button>
          ))}
        </div>
        {mood === "unwell" && (
          <div className="mt-3 flex flex-wrap gap-2">
            <Link href="/ask/check" className="btn btn-primary"><Stethoscope aria-hidden className="h-5 w-5" />{t.checkSymptoms}</Link>
            <Link href="/ask" className="btn btn-outline"><Mic aria-hidden className="h-5 w-5" />{t.tellAssistant}</Link>
          </div>
        )}
        {mood === "good" && <p className="mt-2 text-muted">{t.moodGoodReply}</p>}
      </div>
      {next && (
        <div className="flex flex-wrap items-center gap-3 border-t border-dashed border-border-input pt-4">
          <span aria-hidden className="grid h-14 min-w-16 place-items-center rounded-[6px] bg-brand-soft px-2 font-mono text-[1.375rem] font-bold text-brand">{next.time}</span>
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-1.5 text-[0.875rem] font-semibold text-muted"><BellRing aria-hidden className="h-4 w-4 text-brand" />{t.nextDose}<span className="sr-only"> {next.time}</span> · {when}</p>
            <p className="truncate text-[1.0625rem] font-semibold">{next.name}</p>
          </div>
          <button onClick={() => markTaken(next)} className="btn btn-primary"><Check aria-hidden className="h-5 w-5" />{t.taken}</button>
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------- family quick switch
export function FamilyStrip() {
  const { t } = useI18n();
  const active = useActiveProfile();
  const [members, setMembers] = useState<Member[] | null>(null);
  useEffect(() => { api<Member[]>("/family").then(setMembers).catch(() => setMembers([])); }, []);
  if (!members?.length) return null;
  const people: (Member | null)[] = [null, ...members];
  return (
    <nav aria-label={t.whoseHealth} className="no-scrollbar -mx-4 flex gap-3 overflow-x-auto px-4 sm:mx-0 sm:px-0">
      {people.map((m) => {
        const current = m ? active?.id === m.id : !active;
        return (
          <button key={m?.id ?? "me"} onClick={() => !current && switchProfile(m && { id: m.id, name: m.name, relation: m.relation })}
            aria-current={current ? "true" : undefined}
            className={`flex shrink-0 flex-col items-center gap-1 rounded-[6px] p-1.5 ${current ? "bg-accent-soft" : ""}`}>
            <span className={`rounded-full ${current ? "ring-[3px] ring-brand ring-offset-2 ring-offset-bg" : ""}`}><Avatar name={m?.name} relation={m?.relation} size={44} /></span>
            <span className="max-w-16 truncate text-[0.8125rem] font-semibold">{m ? m.name : t.myself}</span>
          </button>
        );
      })}
      <Link href="/me/family" className="flex shrink-0 flex-col items-center gap-1 p-1.5" aria-label={t.addMember}>
        <span className="grid h-11 w-11 place-items-center rounded-full border-2 border-dashed border-border-input"><Plus aria-hidden className="h-5 w-5" /></span>
        <span className="text-[0.8125rem] font-semibold text-muted">{t.addShort}</span>
      </Link>
    </nav>
  );
}

// ---------------------------------------------------------------- seasonal tip
export function TipCard() {
  const { t, lang } = useI18n();
  const [tip, setTip] = useState<any>(null);
  useEffect(() => { api(`/me/tip?lang=${lang}`).then(setTip).catch(() => {}); }, [lang]);
  if (!tip) return null;
  return (
    <Card className="space-y-2">
      <CardHeader icon={Leaf} title={t.seasonTip} level={2} action={
        <button aria-label={t.readAloud} onClick={() => speak(tip.text, lang, bcp47(lang))} className="grid h-12 w-12 place-items-center rounded-full bg-surface-2 hover:bg-accent-soft">
          <Volume2 aria-hidden className="h-5 w-5" /></button>} />
      <p className="serif border-l-[3px] border-accent pl-3 text-[1.0625rem] leading-relaxed">{tip.text}</p>
      <p className="text-[0.8125rem] text-muted">{t.source}: <a href={tip.url} target="_blank" rel="noreferrer" className="underline">{tip.source}</a></p>
    </Card>
  );
}

// ---------------------------------------------------------------- customise
export type HomeCardKey = "family" | "today" | "voice" | "attention" | "symptoms" | "medicines" | "tip" | "vitals" | "recent";
export const DEFAULT_ORDER: HomeCardKey[] = ["family", "today", "voice", "attention", "symptoms", "medicines", "tip", "vitals", "recent"];

export function useHomeLayout() {
  const { prefs, setPrefs } = usePrefs();
  const saved = prefs.home?.order?.filter((k): k is HomeCardKey => DEFAULT_ORDER.includes(k as HomeCardKey)) ?? [];
  const order = [...saved, ...DEFAULT_ORDER.filter((k) => !saved.includes(k))];
  const hidden = new Set(prefs.home?.hidden ?? []);
  const save = (o: HomeCardKey[], h: Set<string>) => setPrefs({ home: { order: o, hidden: [...h] } });
  return { order, hidden, save };
}

export function CustomiseHome({ open, onClose, labels }: { open: boolean; onClose: () => void; labels: Record<HomeCardKey, string> }) {
  const { t } = useI18n();
  const { order, hidden, save } = useHomeLayout();
  const move = (i: number, d: -1 | 1) => {
    const o = [...order];
    [o[i], o[i + d]] = [o[i + d], o[i]];
    save(o, hidden);
  };
  const toggle = (k: string) => {
    const h = new Set(hidden);
    h.has(k) ? h.delete(k) : h.add(k);
    save(order, h);
  };
  return (
    <Sheet open={open} onClose={onClose} title={t.customiseHome}>
      <p className="mb-3 text-muted">{t.customiseHelp}</p>
      <ol className="space-y-1.5">
        {order.map((k, i) => (
          <li key={k} className={`flex items-center gap-2 rounded-[6px] border border-border bg-bg/50 p-2 ${hidden.has(k) ? "opacity-60" : ""}`}>
            <span className="min-w-0 flex-1 font-semibold">{labels[k]}</span>
            <button aria-label={`${t.moveUp}: ${labels[k]}`} disabled={i === 0} onClick={() => move(i, -1)}
              className="grid h-12 w-12 place-items-center rounded-full bg-surface-2 disabled:opacity-40"><ArrowUp aria-hidden className="h-5 w-5" /></button>
            <button aria-label={`${t.moveDown}: ${labels[k]}`} disabled={i === order.length - 1} onClick={() => move(i, 1)}
              className="grid h-12 w-12 place-items-center rounded-full bg-surface-2 disabled:opacity-40"><ArrowDown aria-hidden className="h-5 w-5" /></button>
            <button aria-pressed={!hidden.has(k)} aria-label={`${hidden.has(k) ? t.show : t.hide}: ${labels[k]}`} onClick={() => toggle(k)}
              className="grid h-12 w-12 place-items-center rounded-full bg-surface-2">
              {hidden.has(k) ? <EyeOff aria-hidden className="h-5 w-5" /> : <Eye aria-hidden className="h-5 w-5" />}</button>
          </li>
        ))}
      </ol>
      <button onClick={() => save(DEFAULT_ORDER, new Set())} className="btn btn-outline mt-4 w-full">{t.resetLayout}</button>
    </Sheet>
  );
}

// ---------------------------------------------------------------- Simple mode
function SpeakLabel({ text }: { text: string }) {
  const { t, lang } = useI18n();
  return (
    <button aria-label={`${t.readAloud}: ${text}`} onClick={(e) => { e.preventDefault(); e.stopPropagation(); speak(text, lang, bcp47(lang)); }}
      className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-surface/80 text-text">
      <Volume2 aria-hidden className="h-6 w-6" /></button>
  );
}

/** Three huge tiles for elderly or first-time users: speak, today's medicines, emergency. */
export function SimpleHome() {
  const { t } = useI18n();
  const { openSOS } = useSOS();
  const { items } = useDoses();
  const next = nextDose(items);
  const tile = "note flex min-h-32 w-full items-center gap-4 rounded-[10px] p-5 text-left text-[1.5rem] font-bold shadow-[var(--e2)]";
  return (
    <div className="mx-auto max-w-xl space-y-4">
      <Link href="/ask" className={`${tile} bg-brand text-brand-ink`}>
        <Mic aria-hidden className="h-12 w-12 shrink-0" /><span className="flex-1">{t.simpleSpeak}</span><SpeakLabel text={t.simpleSpeak} />
      </Link>
      <div className={`${tile} flex-col items-stretch border border-border bg-surface`} style={{ ["--i" as string]: 1 }}>
        <Link href="/medicines" className="flex items-center gap-4">
          <Pill aria-hidden className="h-12 w-12 shrink-0 text-brand" /><span className="flex-1">{t.simpleMedicines}</span><SpeakLabel text={t.simpleMedicines} />
        </Link>
        {next ? (
          <div className="flex flex-wrap items-center gap-3 rounded-[6px] bg-brand-soft p-3 text-[1.125rem] font-semibold">
            <span className="min-w-0 flex-1"><span className="font-mono text-[1.375rem] font-bold">{next.time}</span> · {next.name}</span>
            <button onClick={() => markTaken(next)} className="btn btn-primary min-h-14 px-6 text-[1.125rem]"><Check aria-hidden className="h-6 w-6" />{t.taken}</button>
          </div>
        ) : <p className="text-[1.125rem] font-normal text-muted">{t.noMedsToday}</p>}
      </div>
      <button onClick={openSOS} className={`${tile} bg-emergency text-white`} style={{ ["--i" as string]: 2 }}>
        <Siren aria-hidden className="h-12 w-12 shrink-0" /><span className="flex-1">{t.simpleEmergency}</span><SpeakLabel text={t.simpleEmergency} />
      </button>
      <Link href="/me/settings" className="block py-3 text-center font-semibold text-info underline">{t.simpleModeOff}</Link>
    </div>
  );
}
