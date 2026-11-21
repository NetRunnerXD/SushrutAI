"use client";

import { ArrowRight, ClipboardList, FlaskConical, Leaf, Lightbulb, ListChecks, Pill, Share2, Stethoscope, ThumbsDown, ThumbsUp, Utensils } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { bcp47, useI18n } from "@/lib/i18n";
import { speak } from "@/lib/voice";
import { useSOS } from "./Emergency";
import { ListenButton } from "./Voice";
import {
  Banner, ConfidencePill, DisclaimerChip, PrivacyBadge, SourceList, UrgencyBanner, type Confidence, type Urgency,
} from "./ui/Trust";

export type RedFlag = { triggered: boolean; kind?: string; messages?: string[]; call_numbers?: string[] };

const ORDER: Urgency[] = ["emergency", "see_doctor_24h", "see_doctor_week", "self_care"];
const LIKELY_W: Record<string, string> = { high: "w-full", moderate: "w-2/3", low: "w-1/3" };

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

/** Render paragraphs and "* / - / 1." bullet lines as real lists (no HTML injection: text nodes only). */
function RichText({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/).map((b) => b.split("\n").filter((l) => l.trim()));
  return (
    <div className="mt-2 space-y-2">
      {blocks.map((lines, i) => {
        const bullet = /^\s*([*\-•]|\d+[.)])\s+/;
        if (lines.length && lines.every((l) => bullet.test(l)))
          return <ul key={i} className="list-disc space-y-1 pl-6">{lines.map((l, j) => <li key={j}>{l.replace(bullet, "")}</li>)}</ul>;
        return <p key={i} className="whitespace-pre-wrap">{lines.map((l) => l.replace(bullet, "• ")).join("\n")}</p>;
      })}
    </div>
  );
}

/** First sentence of the reply — what voice mode reads aloud first. */
export function summaryOf(reply: string): string {
  const m = reply?.match(/^.+?[.!?।](\s|$)/s);
  return (m ? m[0] : reply ?? "").trim();
}

export function confidenceOf(a: any): Confidence {
  const cites = a?.citations?.length ?? 0;
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
            <a key={n} href={`tel:${n}`} className={`inline-flex min-h-12 items-center rounded-[8px] px-4 font-semibold
              ${emergency ? "bg-white text-emergency" : "bg-danger text-on-tone"}`}>{t.call} {n}</a>
          ))}
          {emergency && <button onClick={openSOS} className="min-h-12 rounded-[8px] border-2 border-white px-4 font-semibold">{t.sosOpen}</button>}
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
        <Link key={x.href} href={x.href} className="inline-flex min-h-12 items-center gap-2 rounded-full border-[1.5px] border-brand bg-brand-soft px-4 font-semibold text-brand">
          {(t as any)[x.key] ?? x.key}<ArrowRight aria-hidden className="h-4 w-4" /></Link>
      ))}
    </div>
  );
}

export function AnswerCard({ a, onFollowUp, question }: { a: any; onFollowUp?: (text: string) => void; question?: string }) {
  const { t, lang } = useI18n();
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const s = a?.structured;
  const conditions: any[] = s?.possible_conditions ?? [];
  const top = conditions.map((c) => c.urgency as Urgency).sort((x, y) => ORDER.indexOf(x) - ORDER.indexOf(y))[0];
  const clean = plainText(a.reply, t);
  const summary = summaryOf(clean);
  const rest = clean.slice(summary.length).trim();
  const kind: string = s?.kind ?? (conditions.length ? "symptoms" : "general_health");

  if (CONVERSATIONAL.has(kind)) {
    return (
      <article className="space-y-3" aria-label="SushrutAI answer">
        <p className="text-[1.0625rem] leading-relaxed">{clean}</p>
        <ActionLinks actions={s?.actions} />
        <div className="flex flex-wrap gap-2"><ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} /></div>
      </article>
    );
  }

  return (
    <article className="space-y-4" aria-label="SushrutAI answer">
      {top && <UrgencyBanner urgency={top} specialist={s?.specialist} />}

      <div>
        <p className="serif text-[1.25rem] font-bold leading-snug">{summary}</p>
        {rest && <RichText text={rest} />}
      </div>

      {conditions.length > 0 && (
        <section aria-labelledby={`pc-${a.message_id ?? ""}`} className="rounded-[6px] border border-dashed border-border-input p-3">
          <h3 id={`pc-${a.message_id ?? ""}`} className="mb-2 font-semibold">{t.possibleConditions}</h3>
          <ol className="space-y-2.5">
            {conditions.map((c, i) => (
              <li key={i}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <span className="font-semibold">{i + 1}. {c.name}{c.name_en && c.name_en !== c.name ? <span lang="en" className="font-normal text-muted"> ({c.name_en})</span> : null}</span>
                  <span className="text-[0.875rem] text-muted">{t.likelihood[c.likelihood] ?? c.likelihood}</span>
                </div>
                <div aria-hidden className="mt-1 h-2 rounded-full bg-surface-2">
                  <div className={`h-2 rounded-full bg-brand ${LIKELY_W[c.likelihood] ?? "w-1/3"}`} />
                </div>
              </li>
            ))}
          </ol>
        </section>
      )}

      {s?.tips?.length > 0 && (
        <section className="space-y-2">
          <Row icon={Lightbulb} title={t.goodToKnow} items={s.tips} />
        </section>
      )}

      {s?.medicine_links?.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {s.medicine_links.map((m: any) => (
            <Link key={m.id} href={`/medicines/${m.id}`} className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 font-semibold">
              <Pill aria-hidden className="h-4 w-4 text-brand" />{t.aboutMedicine}: {m.name}</Link>))}
        </div>
      )}

      {kind === "symptoms" && s?.suggest_check !== false && question && (
        <Link href={`/ask/check?c=${encodeURIComponent(question.slice(0, 300))}`}
          className="flex min-h-14 items-center gap-3 rounded-[6px] border-2 border-brand bg-surface p-3 font-semibold text-brand">
          <ListChecks aria-hidden className="h-6 w-6 shrink-0" />
          <span className="flex-1">{t.checkStepByStep}<span className="block text-[0.875rem] font-normal text-muted">{t.checkStepByStepHelp}</span></span>
          <ArrowRight aria-hidden className="h-5 w-5" /></Link>
      )}

      {(s?.suggested_tests?.length || s?.self_care?.length || s?.nutrition?.length) ? (
        <section className="space-y-3">
          <h3 className="font-semibold">{t.whatNext}</h3>
          {s?.specialist && <Row icon={Stethoscope} title={t.specialist} items={[s.specialist]} />}
          <Row icon={FlaskConical} title={t.tests} items={s?.suggested_tests} />
          <Row icon={Leaf} title={t.selfCare} items={s?.self_care} />
          <Row icon={Utensils} title={t.nutrition} items={s?.nutrition} />
          {top && top !== "self_care" && (
            <a href="https://esanjeevani.mohfw.gov.in" target="_blank" rel="noreferrer"
               className="inline-flex min-h-12 items-center gap-2 rounded-[8px] bg-info-soft px-4 font-semibold text-info">
              <Stethoscope aria-hidden className="h-5 w-5" />{t.findDoctor}</a>
          )}
        </section>
      ) : null}

      {s?.follow_up_questions?.length > 0 && (
        <section>
          <h3 className="mb-1.5 flex items-center gap-1.5 font-semibold"><ClipboardList aria-hidden className="h-5 w-5" />{t.followUps}</h3>
          <ul className="space-y-1.5">
            {s.follow_up_questions.map((q: string, i: number) => (
              <li key={i}>
                <button onClick={() => onFollowUp?.(q)} className="min-h-12 w-full rounded-[8px] border-[1.5px] border-border-input bg-surface px-3 py-2 text-left">
                  {q}</button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {kind === "symptoms" && <ConfidencePill level={confidenceOf(a)} />}
        {a.tier && <PrivacyBadge local={a.tier === "L"} />}
        {a.model && <span className="text-[0.8125rem] text-muted">{t.answeredBy}: {modelLabel(a.model, t)}</span>}
      </div>
      <SourceList sources={a.citations ?? []} />
      <DisclaimerChip />

      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        <ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} />
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
function modelLabel(model: string, t: any): string {
  if (/medgemma/i.test(model)) return t.modelBalanced;
  if (/gpt-oss|groq|llama-3|70b|120b/i.test(model)) return t.modelCloud;
  return t.modelFast;
}

function Row({ icon: Icon, title, items }: { icon: any; title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="flex gap-3">
      <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-brand-soft text-brand"><Icon className="h-5 w-5" /></span>
      <div><p className="text-[0.9375rem] font-semibold text-muted">{title}</p>
        <ul className="list-disc space-y-0.5 pl-5">{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
    </div>
  );
}
