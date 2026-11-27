"use client";

import { ArrowRight, Check, ClipboardList, Pill, Share2, Stethoscope, ThumbsDown, ThumbsUp, Utensils } from "lucide-react";
import { Causes } from "./answer/Causes";
import { CheckInline } from "./answer/CheckInline";
import { DoNow } from "./answer/DoNow";
import { NextSteps } from "./answer/NextSteps";
import { AnswerSection } from "./answer/Section";
import { ReplyText, tidyReply } from "./answer/ReplyText";
import { SavedFacts } from "./answer/SavedFacts";
import { Storylines } from "./answer/Storylines";
import Link from "next/link";
import { useState } from "react";
import { bcp47, useI18n, fill } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";
import { speak } from "@/lib/voice";
import { AnswerCardV2 } from "./AnswerCardV2";
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

const CONVERSATIONAL = new Set(["greeting", "thanks_bye", "about_app", "off_topic", "attachment", "medicine_photo"]);

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

export function AnswerCard(props: {
  a: any; onFollowUp?: (text: string) => void; onAsk?: (text: string) => void; question?: string;
  answered?: boolean;  // a later turn exists: an old question's buttons would answer the current one, so hide them
}) {
  const { prefs } = usePrefs();
  if ((prefs.chatUi ?? "v2") === "v2") return <AnswerCardV2 {...props} />;
  return <AnswerCardV1 {...props} />;
}

function AnswerCardV1({ a, onFollowUp, onAsk, question, answered }: {
  a: any; onFollowUp?: (text: string) => void; onAsk?: (text: string) => void; question?: string;
  answered?: boolean;
}) {
  const { t, lang } = useI18n();
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const sendVote = (v: "up" | "down") => {
    const topic = (a?.structured?.possible_conditions?.[0]?.name_en || a?.structured?.kind || "answer").slice(0, 80);
    api("/me/memory/feedback", { method: "POST", body: JSON.stringify({ kind: "answer", name: topic, value: v }) }).catch(() => {});
  };
  const s = a?.structured;
  const conditions: any[] = s?.possible_conditions ?? [];
  const top = conditions.map((c) => c.urgency as Urgency).sort((x, y) => ORDER.indexOf(x) - ORDER.indexOf(y))[0];
  const clean = plainText(a.reply, t);        // for reading aloud, copying and sharing
  const rich = tidyReply(a.reply, t);         // for the screen: keeps **bold** for the formatter
  const terms = conditions.flatMap((c) => [c.name, c.name_en]).filter(Boolean);
  const kind: string = s?.kind ?? (conditions.length ? "symptoms" : "general_health");

  if (kind === "clarify" && s?.clarify) {
    // Dynamic doctor-patient consultation: ask one focused question at a time until narrowed down
    const c = s.clarify;
    const stepLabel = c.mode === "negotiate"
      ? t.yourChoice
      : lang === "hi"
        ? `परामर्श · सवाल ${c.step}`
        : `Consultation · Question ${c.step}`;
    return (
      <article className="space-y-3" aria-label="SushrutAI question">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand/25 bg-brand-soft/45 px-2.5 py-0.5 text-[0.75rem] font-semibold text-brand">
            <Stethoscope aria-hidden className="h-3.5 w-3.5" />
            {stepLabel}
          </span>
          {typeof c.plausible_count === "number" && c.plausible_count > 3 && (
            <span className="text-[0.75rem] font-medium text-muted">
              {lang === "hi" ? "संभावित कारण सीमित कर रहे हैं" : "Narrowing possibilities"}
            </span>
          )}
        </div>
        <p className="display max-w-[60ch] text-[1.25rem] font-bold leading-snug whitespace-pre-line">{c.question.replace(/ • /g, "\n• ")}</p>
        <SavedFacts facts={a.saved_facts} />
        {!answered && (
          <>
            <div className="flex flex-wrap gap-2">
              {c.options.map((o: any) => (
                <button key={o.value} type="button" onClick={() => onAsk?.(o.label)}
                  className={`btn min-h-12 px-4 text-[0.95rem] ${o.value === "yes" || o.value === "more" ? "btn-primary" : "btn-outline"}`}>{o.label}</button>
              ))}
            </div>
            <p className="text-[0.875rem] text-muted">{t.orJustSay}</p>
          </>
        )}
      </article>
    );
  }

  if (CONVERSATIONAL.has(kind)) {
    return (
      <article className="space-y-3" aria-label="SushrutAI answer">
        <div className="max-w-[68ch] space-y-2 text-[1.0625rem] leading-[1.7]">
          {clean.split(/\n{2,}/).map((x, i) => <p key={i} className="whitespace-pre-wrap">{x}</p>)}</div>
        <ActionLinks actions={s?.actions} />
        {kind === "medicine_photo" && <Storylines items={s?.storylines} />}
        <div className="flex flex-wrap gap-2"><ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} /></div>
      </article>
    );
  }

  const stage = a.clinical?.consultation_stage as string | undefined;
  const stageLabel =
    stage === "urgent_care" ? (lang === "hi" ? "तुरंत देखभाल सलाह" : "Urgent Care Guidance")
    : stage === "gathering_history" ? (lang === "hi" ? "परामर्श · लक्षण समझ रहे हैं" : "Consultation · Understanding Symptoms")
    : stage === "evaluating" ? (lang === "hi" ? "आकलन और संभावित कारण" : "Assessment & Possibilities")
    : stage === "care_plan" ? (lang === "hi" ? "देखभाल और अगले कदम" : "Care Plan & Next Steps")
    : null;

  return (
    <article className="space-y-4" aria-label="SushrutAI answer">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {stageLabel && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand/25 bg-brand-soft/45 px-2.5 py-0.5 text-[0.75rem] font-semibold text-brand">
            <Stethoscope aria-hidden className="h-3.5 w-3.5" />
            {stageLabel}
          </span>
        )}
      </div>
      {top && <UrgencyBanner urgency={top} specialist={s?.specialist} />}
      {a.clinical?.returning_patient && (
        <Banner tone="info" title={t.returningPatientNote}>
          {a.clinical.prior_encounter_summary && <p>{a.clinical.prior_encounter_summary}</p>}
        </Banner>
      )}
      {s?.clinical_recap && (
        <p className="rounded-[12px] border border-border/70 bg-surface-2/70 px-3 py-2 text-[0.875rem] text-muted">
          <span className="font-semibold text-text">{lang === "hi" ? "अभी तक समझा: " : "What I've understood: "}</span>
          {s.clinical_recap}
        </p>
      )}

      <ReplyText text={rich} terms={terms} label={t.whenToSeeDoctor} />
      <SavedFacts facts={a.saved_facts} />
      <Storylines items={s?.storylines} />

      <div className="space-y-2.5">
        <Causes conditions={conditions} onAsk={onAsk} defaultOpen />
        {kind === "symptoms" ? (
          <DoNow care={s?.self_care} watch={s?.watch_for} question={question} symptomatic defaultOpen />
        ) : (
          <DoNow care={s?.tips} watch={s?.watch_for} question={question} symptomatic={false} defaultOpen />
        )}
        <NextSteps specialist={s?.specialist} tests={s?.suggested_tests} urgent={!!top && top !== "self_care"}
          routing={a.clinical?.specialist_routing} investigations={a.clinical?.investigations} />
        {s?.nutrition?.length > 0 && (
          <AnswerSection id="food" icon={Utensils} title={t.nutrition} defaultOpen={false} summary={s.nutrition.length}>
            <ul className="list-disc space-y-0.5 ps-6">{s.nutrition.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
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

      {a.clinical?.followup_question && (() => {
        const fq = a.clinical.followup_question;
        const qStr = String(fq.question || "");
        const ansType = fq.answer_type as string | undefined;
        const isYesNo = ansType === "yes_no" || (
          /^(?:are|is|do|does|did|have|has|was|were|will|would|could|can|with\b|when it starts|since |क्या|আপনার কি|এর সাথে কি)/i.test(qStr.trim()) &&
          !/\b(?:roughly when|when did|how much|how long|where exactly|which timeline)\b/i.test(qStr)
        );
        const isDuration = ansType === "duration";
        const isSeverity = ansType === "severity";
        const durationChips = lang === "hi"
          ? ["आज से", "2–3 दिन से", "1 हफ़्ते से ज़्यादा"]
          : ["Since today", "2–3 days", "More than a week"];
        const severityChips = lang === "hi"
          ? ["हल्का", "मध्यम", "तेज़ / गंभीर"]
          : ["Mild", "Moderate", "Severe"];
        return (
          <section className={`rounded-[16px] p-3.5 ${answered ? "border border-border bg-surface-2/60" : "border-2 border-brand/40 bg-brand-soft/30"}`}>
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
              <h3 className={`flex items-center gap-1.5 font-bold ${answered ? "text-muted" : "text-brand"}`}>
                <ClipboardList aria-hidden className="h-5 w-5" />{t.prioritizedQuestion}
              </h3>
              {answered && (
                <span className="inline-flex items-center gap-1 rounded-full bg-ok-soft px-2.5 py-0.5 text-[0.75rem] font-semibold text-ok">
                  <Check aria-hidden className="h-3.5 w-3.5" />Answered
                </span>
              )}
            </div>
            <p className="text-[1.0625rem] font-semibold leading-snug">{qStr}</p>
            {fq.why_asking && (
              <p className="mt-1 text-[0.8125rem] text-muted">{fq.why_asking}</p>
            )}
            {!answered && (
              <div className="mt-2.5 flex flex-wrap gap-2">
                {isYesNo && (
                  <>
                    <button type="button" onClick={() => onAsk?.(t.yes)} className="btn btn-primary min-h-12 px-4">{t.yes}</button>
                    <button type="button" onClick={() => onAsk?.(t.no)} className="btn btn-outline min-h-12 px-4">{t.no}</button>
                    <button type="button" onClick={() => onAsk?.(t.unsure)} className="btn btn-outline min-h-12 px-4">{t.unsure}</button>
                  </>
                )}
                {isDuration && durationChips.map((lbl) => (
                  <button key={lbl} type="button" onClick={() => onAsk?.(lbl)} className="btn btn-outline min-h-12 px-4">{lbl}</button>
                ))}
                {isSeverity && severityChips.map((lbl) => (
                  <button key={lbl} type="button" onClick={() => onAsk?.(lbl)} className="btn btn-outline min-h-12 px-4">{lbl}</button>
                ))}
                <button type="button" onClick={() => onFollowUp?.("")}
                  className="btn btn-outline min-h-12">{t.replyToQuestion}</button>
              </div>
            )}
          </section>
        );
      })()}

      {s?.doctor_note && (
        <section className="rounded-[14px] border border-info/30 bg-info-soft/40 p-3 text-[0.875rem]">
          <p className="font-semibold text-info">{lang === "hi" ? "डॉक्टर को दिखाते समय यह बताएं" : "What to mention to your doctor"}</p>
          <p className="mt-0.5 text-text">{s.doctor_note}</p>
        </section>
      )}

      {s?.follow_up_questions?.length > 0 && (answered || !a.clinical?.followup_question) && (
        <section>
          <h3 className="mb-1.5 flex items-center gap-1.5 font-semibold"><ClipboardList aria-hidden className="h-5 w-5" />{t.followUps}</h3>
          <ul className="space-y-1.5">
            {s.follow_up_questions
              .filter((q: string) => q !== a.clinical?.followup_question?.question)
              .slice(0, 1)
              .map((q: string, i: number) => (
              <li key={i}>
                <button onClick={() => onFollowUp?.(q)} className="min-h-12 w-full rounded-[12px] border-[1.5px] border-border-input bg-surface px-3 py-2 text-start">
                  {q}</button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {a.clinical?.needs_clarification && a.clinical?.clarification_prompt && (
        <Banner tone="warning">{a.clinical.clarification_prompt}</Banner>
      )}
      {a.clinical?.investigations?.length > 0 && (
        <section className="space-y-1.5 rounded-[14px] border border-border bg-surface-2 p-3 text-[0.875rem]">
          <p className="font-bold">Tests to discuss with a doctor</p>
          <ul className="space-y-1">
            {a.clinical.investigations.map((inv: any, idx: number) => (
              <li key={idx}>
                <span className="font-semibold">{inv.investigation_name}</span> ({inv.category}, {inv.urgency}) — {inv.why_relevant}
                {inv.already_in_records && <span className="ms-1 font-semibold text-ok">[Already in records]</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {kind === "symptoms" && <ConfidencePill level={confidenceOf(a)} />}
        {a.tier && <PrivacyBadge local={a.tier === "L"} />}
        {a.clinical?.encounter_id && (
          <Link href="/context" className="rounded-full bg-surface-2 px-3 py-1 text-[0.8125rem] font-semibold hover:bg-border">
            {t.navContext} →
          </Link>
        )}
        {a.model && <span className="text-[0.8125rem] text-muted">{t.answeredBy}: {a.auto ? `${t.autoPicked} ` : ""}{modelLabel(a.model, t, (a as any).speed)}</span>}
      </div>
      <SourceList sources={a.citations ?? []} consulted={s?.consulted ?? []} />
      <DisclaimerChip />

      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        <ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} />
        {question && <SaveForDoctor text={question} />}
        <button onClick={() => { setVote("up"); sendVote("up"); }} aria-pressed={vote === "up"} aria-label={t.helpful}
                className={`grid h-12 w-12 place-items-center rounded-full ${vote === "up" ? "bg-ok-soft text-ok" : "bg-surface-2"}`}>
          <ThumbsUp aria-hidden className="h-4 w-4" /></button>
        <button onClick={() => { setVote("down"); sendVote("down"); }} aria-pressed={vote === "down"} aria-label={t.notHelpful}
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
  if (speed === "gemini_fast" || /flash-lite/i.test(model)) return t.modelGeminiFast;
  if (speed === "gemini" || /gemini/i.test(model)) return t.modelGemini;
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
        <ul className="list-disc space-y-0.5 ps-5">{items.map((x, i) => <li key={i}>{x}</li>)}</ul></div>
    </div>
  );
}
