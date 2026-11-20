"use client";

import { ArrowRight, Check, ClipboardList, Pill, Share2, Stethoscope, ThumbsDown, ThumbsUp, Utensils } from "lucide-react";
import { Causes } from "./answer/Causes";
import { CheckInline } from "./answer/CheckInline";
import { DoNow } from "./answer/DoNow";
import { NextSteps } from "./answer/NextSteps";
import { AnswerSection } from "./answer/Section";
import { ReplyText, tidyReply } from "./answer/ReplyText";
import Link from "next/link";
import { useState } from "react";
import { bcp47, useI18n } from "@/lib/i18n";
import { speak } from "@/lib/voice";
import { useSOS } from "./Emergency";
import { ListenButton } from "./Voice";
import { api } from "@/lib/api";
import {
  Banner, ConfidencePill, DisclaimerChip, PrivacyBadge, SourceList, UrgencyBanner, type Confidence, type Urgency,
} from "./ui/Trust";

export type RedFlag = { triggered: boolean; kind?: string; messages?: string[]; call_numbers?: string[] };

const ORDER: Urgency[] = ["emergency", "see_doctor_24h", "see_doctor_week", "self_care"];

/** Model text -> plain text: strip markdown emphasis/headings and internal enum tokens (e.g. "See_doctor_24h"). */
export function plainText(reply: string, t?: any): string {
  return (reply ?? "")
    .replace(/\*\*([^*\n]+?)\*\*/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\b(see_doctor_24h|see_doctor_week|self_care)\b:?/gi, (m) => {
      const k = m.replace(/:$/, "").toLowerCase();
      return (t?.urgency?.[k] ?? k.replace(/_/g, " ")) + (m.endsWith(":") ? ":" : "");
    })
    .trim();
}

/** First sentence of the reply — what voice mode reads aloud first. */
export function summaryOf(reply: string): string {
  const m = reply?.match(/^.+?[.!?।](\s|$)/s);
  return (m ? m[0] : reply ?? "").trim();
}

export function confidenceOf(a: any): Confidence {
  const cites = (a?.citations?.length ?? 0) + Math.min(1, a?.structured?.consulted?.length ?? 0);
  const warns = (a?.structured && a?.warnings?.length) || /cut short/.test(a?.reply ?? "");
  if (cites >= 2 && !warns) return "high";
  if (cites >= 1) return "medium";
  return "low";
}

export function RedFlagBanner({ rf }: { rf: RedFlag }) {
  const { t } = useI18n();
  const { openSOS } = useSOS();
  if (!rf?.triggered) return null;
  const emergency = rf.kind === "emergency" || rf.kind === "mental_health";
  return (
    <Banner tone={emergency ? "emergency" : "danger"} title={emergency ? t.sosTitle : t.urgency.see_doctor_24h}
      action={
        <>
          {rf.call_numbers?.map((n) => (
            <a key={n} href={`tel:${n}`} className={`inline-flex min-h-12 items-center rounded-[12px] px-4 font-semibold
              ${emergency ? "bg-white text-emergency" : "bg-danger text-on-tone"}`}>{t.call} {n}</a>
          ))}
          {emergency && <button onClick={openSOS} className="min-h-12 rounded-[12px] border-2 border-white px-4 font-semibold">{t.sosOpen}</button>}
        </>
      }>
      {rf.messages?.map((m, i) => <p key={i}>{m}</p>)}
    </Banner>
  );
}

const CONVERSATIONAL = new Set(["greeting", "thanks_bye", "about_app", "off_topic", "attachment"]);

/** Shortcut buttons returned with conversational replies and attachment routing ({key, href}). */
function ActionLinks({ actions }: { actions?: { key: string; href: string }[] }) {
  const { t } = useI18n();
  if (!actions?.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {actions.map((x) => (
        <Link key={x.href} href={x.href} className="inline-flex min-h-12 items-center gap-2 rounded-full border-[1.5px] border-border-input bg-surface px-4 font-semibold shadow-[var(--rim)] hover:bg-surface-2">
          {(t as any)[x.key] ?? x.key}<ArrowRight aria-hidden className="h-4 w-4" /></Link>
      ))}
    </div>
  );
}

/** "Ask my doctor": keep this question for the next visit (it appears on the doctor-visit summary). */
function SaveForDoctor({ text }: { text: string }) {
  const { t } = useI18n();
  const [saved, setSaved] = useState(false);
  return (
    <button disabled={saved} aria-pressed={saved}
      onClick={() => api("/me/visit-questions", { method: "POST", body: JSON.stringify({ text: text.slice(0, 300), source: "chat" }) })
        .then(() => setSaved(true)).catch(() => {})}
      className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.9375rem] font-semibold disabled:opacity-100">
      {saved ? <Check aria-hidden className="h-4 w-4 text-ok" /> : <Stethoscope aria-hidden className="h-4 w-4" />}
      {saved ? t.savedForDoctor : t.saveForDoctor}</button>
  );
}

export function AnswerCard({ a, onFollowUp, onAsk, question }: {
  a: any; onFollowUp?: (text: string) => void; onAsk?: (text: string) => void; question?: string;
}) {
  const { t, lang } = useI18n();
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const s = a?.structured;
  const conditions: any[] = s?.possible_conditions ?? [];
  const top = conditions.map((c) => c.urgency as Urgency).sort((x, y) => ORDER.indexOf(x) - ORDER.indexOf(y))[0];
  const clean = plainText(a.reply, t);        // for reading aloud, copying and sharing
  const rich = tidyReply(a.reply, t);         // for the screen: keeps **bold** for the formatter
  const terms = conditions.flatMap((c) => [c.name, c.name_en]).filter(Boolean);
  const kind: string = s?.kind ?? (conditions.length ? "symptoms" : "general_health");

  if (CONVERSATIONAL.has(kind)) {
    return (
      <article className="space-y-3" aria-label="SushrutAI answer">
        <div className="max-w-[68ch] space-y-2 text-[1.0625rem] leading-[1.7]">
          {clean.split(/\n{2,}/).map((x, i) => <p key={i} className="whitespace-pre-wrap">{x}</p>)}</div>
        <ActionLinks actions={s?.actions} />
        <div className="flex flex-wrap gap-2"><ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} /></div>
      </article>
    );
  }

  return (
    <article className="space-y-4" aria-label="SushrutAI answer">
      {top && <UrgencyBanner urgency={top} specialist={s?.specialist} />}

      <ReplyText text={rich} terms={terms} label={t.whenToSeeDoctor} />

      <div className="space-y-2.5">
        <Causes conditions={conditions} onAsk={onAsk} defaultOpen />
        {kind === "symptoms" ? (
          <DoNow care={s?.self_care} watch={s?.watch_for} question={question} symptomatic defaultOpen />
        ) : (
          <DoNow care={s?.tips} watch={s?.watch_for} question={question} symptomatic={false} defaultOpen />
        )}
        <NextSteps specialist={s?.specialist} tests={s?.suggested_tests} urgent={!!top && top !== "self_care"} />
        {s?.nutrition?.length > 0 && (
          <AnswerSection id="food" icon={Utensils} title={t.nutrition} defaultOpen={false} summary={s.nutrition.length}>
            <ul className="list-disc space-y-0.5 pl-6">{s.nutrition.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
          </AnswerSection>
        )}
        {kind === "symptoms" && s?.suggest_check !== false && question && <CheckInline complaint={question} />}
      </div>

      {s?.medicine_links?.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {s.medicine_links.map((m: any) => (
            <Link key={m.id} href={`/medicines/${m.id}`} className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 font-semibold">
              <Pill aria-hidden className="h-4 w-4 text-muted" />{t.aboutMedicine}: {m.name}</Link>))}
        </div>
      )}

      {s?.follow_up_questions?.length > 0 && (
        <section>
          <h3 className="mb-1.5 flex items-center gap-1.5 font-semibold"><ClipboardList aria-hidden className="h-5 w-5" />{t.followUps}</h3>
          <ul className="space-y-1.5">
            {s.follow_up_questions.map((q: string, i: number) => (
              <li key={i}>
                <button onClick={() => onFollowUp?.(q)} className="min-h-12 w-full rounded-[12px] border-[1.5px] border-border-input bg-surface px-3 py-2 text-left">
                  {q}</button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {kind === "symptoms" && <ConfidencePill level={confidenceOf(a)} />}
        {a.tier && <PrivacyBadge local={a.tier === "L"} />}
        {a.model && <span className="text-[0.8125rem] text-muted">{t.answeredBy}: {a.auto ? `${t.autoPicked} ` : ""}{modelLabel(a.model, t, (a as any).speed)}</span>}
      </div>
      <SourceList sources={a.citations ?? []} consulted={s?.consulted ?? []} />
      <DisclaimerChip />

      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        <ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} />
        {question && <SaveForDoctor text={question} />}
        <button onClick={() => setVote("up")} aria-pressed={vote === "up"} aria-label={t.helpful}
                className={`grid h-12 w-12 place-items-center rounded-full ${vote === "up" ? "bg-ok-soft text-ok" : "bg-surface-2"}`}>
          <ThumbsUp aria-hidden className="h-4 w-4" /></button>
        <button onClick={() => setVote("down")} aria-pressed={vote === "down"} aria-label={t.notHelpful}
                className={`grid h-12 w-12 place-items-center rounded-full ${vote === "down" ? "bg-danger-soft text-danger" : "bg-surface-2"}`}>
          <ThumbsDown aria-hidden className="h-4 w-4" /></button>
        {typeof navigator !== "undefined" && "share" in navigator && (
          <button onClick={() => navigator.share({ title: "SushrutAI", text: `${a.reply}\n\n${t.disclaimerShort}` }).catch(() => {})}
                  className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.9375rem] font-semibold">
            <Share2 aria-hidden className="h-4 w-4" />{t.share}</button>
        )}
      </div>
    </article>
  );
}

/** Plain name for the model that wrote an answer (matches the Answer speed choices in Settings). */
function modelLabel(model: string, t: any, speed?: string | null): string {
  if (speed === "instant") return t.modelInstant;
  if (speed === "fast") return t.modelFast;
  if (speed === "cloud") return t.modelCloud;
  if (/medgemma/i.test(model)) return t.modelBalanced;
  if (/gpt-oss|groq|llama-3|70b|120b/i.test(model)) return t.modelCloud;
  return t.modelFast;
}

function Row({ icon: Icon, title, items }: { icon: any; title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="flex gap-3">
      <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-full well text-text"><Icon className="h-5 w-5" /></span>
      <div><p className="text-[0.9375rem] font-semibold text-muted">{title}</p>
        <ul className="list-disc space-y-0.5 pl-5">{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
    </div>
  );
}
