"use client";

import {
  Check, ChevronDown, ClipboardList, FileText, FlaskConical, Pill, Share2,
  Stethoscope, ThumbsDown, ThumbsUp, Utensils, Video,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { bcp47, fill, useI18n } from "@/lib/i18n";
import { speak } from "@/lib/voice";
import { confidenceOf, plainText } from "./AnswerCard";
import { Causes } from "./answer/Causes";
import { DoNow } from "./answer/DoNow";
import { matchLabs, type ClinicalInvestigationItem, type SpecialistRouting } from "./answer/NextSteps";
import { ReplyText, tidyReply } from "./answer/ReplyText";
import { SavedFacts } from "./answer/SavedFacts";
import { Storylines } from "./answer/Storylines";
import {
  Banner, ConfidencePill, DisclaimerChip, PrivacyBadge, SourceList, UrgencyBanner, type Urgency,
} from "./ui/Trust";
import { ListenButton } from "./Voice";

const ORDER: Urgency[] = ["emergency", "see_doctor_24h", "see_doctor_week", "self_care"];
const CONVERSATIONAL = new Set(["greeting", "thanks_bye", "about_app", "off_topic", "attachment", "medicine_photo"]);

type Lab = { id: string; test_name: string; value: number | null; unit?: string; flag?: string; observed_at: string };

export function AnswerCardV2({ a, onFollowUp, onAsk, question, answered }: {
  a: any;
  onFollowUp?: (text: string) => void;
  onAsk?: (text: string) => void;
  question?: string;
  answered?: boolean;
}) {
  const { t, lang } = useI18n();
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const [activeBox, setActiveBox] = useState<"doctor" | "tests" | null>(null);
  const [addedTests, setAddedTests] = useState(false);
  const [labs, setLabs] = useState<Lab[] | null | undefined>(undefined);
  const [savedDoc, setSavedDoc] = useState(false);

  const s = a?.structured;
  const conditions: any[] = s?.possible_conditions ?? [];
  const top = conditions.map((c) => c.urgency as Urgency).sort((x, y) => ORDER.indexOf(x) - ORDER.indexOf(y))[0];
  const clean = plainText(a.reply, t);
  const rich = tidyReply(a.reply, t);
  const terms = conditions.flatMap((c) => [c.name, c.name_en]).filter(Boolean);
  const kind: string = s?.kind ?? (conditions.length ? "symptoms" : "general_health");

  const sendVote = (v: "up" | "down") => {
    const topic = (s?.possible_conditions?.[0]?.name_en || s?.kind || "answer").slice(0, 80);
    api("/me/memory/feedback", { method: "POST", body: JSON.stringify({ kind: "answer", name: topic, value: v }) }).catch(() => {});
  };

  if (kind === "clarify" && s?.clarify) {
    const c = s.clarify;
    const stepLabel = c.mode === "negotiate"
      ? t.yourChoice
      : lang === "hi"
        ? `परामर्श · सवाल ${c.step}`
        : lang === "bn"
          ? `পরামর্শ · প্রশ্ন ${c.step}`
          : `Consultation · Question ${c.step}`;
    return (
      <article className="v2-stagger space-y-3" aria-label="SushrutAI question">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand/25 bg-brand-soft/50 px-3 py-1 text-[0.75rem] font-bold text-brand">
            <Stethoscope aria-hidden className="h-3.5 w-3.5" />
            {stepLabel}
          </span>
          <ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} />
        </div>
        <div className="rounded-[16px] border border-border/80 bg-surface-2/50 p-4">
          <p className="display w-full text-[1.2rem] font-bold leading-snug whitespace-pre-line">
            {c.question.replace(/ • /g, "\n• ")}
          </p>
        </div>
        <SavedFacts facts={a.saved_facts} />
        {!answered && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              {c.options.map((o: any) => (
                <button key={o.value} type="button" onClick={() => onAsk?.(o.label)}
                  className={`btn v2-tile min-h-12 px-4 text-[0.95rem] ${o.value === "yes" || o.value === "more" ? "btn-primary" : "btn-outline"}`}>
                  {o.label}
                </button>
              ))}
            </div>
            <p className="text-[0.875rem] text-muted">{t.orJustSay}</p>
          </div>
        )}
      </article>
    );
  }

  if (CONVERSATIONAL.has(kind)) {
    return (
      <article className="v2-stagger space-y-3" aria-label="SushrutAI answer">
        <div className="w-full space-y-2 rounded-[14px] bg-surface-2/40 p-3.5 text-[1.0625rem] leading-[1.7]">
          {clean.split(/\n{2,}/).map((x, i) => <p key={i} className="whitespace-pre-wrap">{x}</p>)}
        </div>
        {kind === "medicine_photo" && <Storylines items={s?.storylines} />}
        <div className="flex flex-wrap gap-2">
          <ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} />
        </div>
      </article>
    );
  }

  const routing = (a.clinical?.specialist_routing ?? null) as SpecialistRouting | null;
  const invList = (a.clinical?.investigations?.length ? a.clinical.investigations : null) as ClinicalInvestigationItem[] | null;
  const fallbackTests = (s?.suggested_tests ?? []) as string[];
  const testNames = invList ? invList.map((x) => x.investigation_name) : fallbackTests;
  const doctorTitle = routing?.recommended_title || s?.specialist || "";
  const isGeneralInfo =
    conditions.length === 0 &&
    !a.clinical?.followup_question &&
    (kind === "general_health" ||
      kind === "nutrition" ||
      kind === "lab_report" ||
      /\b(?:diet|nutrition|vitamin|protein|calorie|food|hydration|exercise|sleep|normal range|lab report|test result)\b/i.test(
        `${question || ""} ${a.reply || ""}`,
      ));
  const showDoctorOrTests = !isGeneralInfo && Boolean(doctorTitle || testNames.length);
  const responseStyle =
    typeof window !== "undefined" && window.localStorage.getItem("sushrut.responseStyle") === "detailed"
      ? "detailed"
      : "direct";

  const stage = a.clinical?.consultation_stage as string | undefined;
  const stageLabel =
    stage === "urgent_care" ? (lang === "hi" ? "तुरंत देखभाल सलाह" : lang === "bn" ? "জরুরি পরামর্শ" : "Urgent Care Guidance")
    : stage === "gathering_history" ? (lang === "hi" ? "परामर्श · लक्षण समझ रहे हैं" : lang === "bn" ? "পরামর্শ · লক্ষণ বোঝা হচ্ছে" : "Consultation · Understanding Symptoms")
    : stage === "evaluating" ? (lang === "hi" ? "आकलन और संभावित कारण" : lang === "bn" ? "মূল্যায়ন ও সম্ভাব্য কারণ" : "Assessment & Possibilities")
    : stage === "care_plan" ? (lang === "hi" ? "देखभाल और अगले कदम" : lang === "bn" ? "যত্ন ও পরবর্তী ধাপ" : "Care Plan & Next Steps")
    : null;

  const addTests = () => Promise.all(testNames.slice(0, 5).map((x) =>
    api("/me/visit-questions", { method: "POST", body: JSON.stringify({ text: fill(t.shouldIGetTest, { test: x }).slice(0, 300), source: "chat" }) })))
    .then(() => setAddedTests(true)).catch(() => {});
  const showLabs = () => api<Lab[]>("/reports/labs").then((l) => setLabs(matchLabs(testNames, l))).catch(() => setLabs(null));

  const priorityLabel = (p?: string) => {
    if (p === "urgent_evaluation") return lang === "hi" ? "तुरंत जांच" : lang === "bn" ? "জরুরি পরীক্ষা" : "Urgent";
    if (p === "considered_later") return lang === "hi" ? "बाद में विचार" : lang === "bn" ? "পরে বিবেচ্য" : "Consider later";
    return lang === "hi" ? "प्रारंभिक जांच" : lang === "bn" ? "প্রাথমিক পরীক্ষা" : "Initial";
  };

  return (
    <article className="v2-stagger space-y-4" aria-label="SushrutAI answer">
      {/* 1. Clinical Summary & Main Doctor Reply (or General Health / Nutrition Guide) */}
      <div className="space-y-3 rounded-[16px] border border-border/75 bg-surface-2/35 p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {isGeneralInfo ? (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[0.75rem] font-bold text-emerald-700">
                <Utensils aria-hidden className="h-3.5 w-3.5" />
                {lang === "hi" ? "स्वास्थ्य और पोषण जानकारी" : lang === "bn" ? "স্বাস্থ্য ও পুষ্টি তথ্য" : "Health & Nutrition Guide"}
              </span>
            ) : (
              stageLabel && (
                <span className="inline-flex items-center gap-1.5 rounded-full border border-brand/25 bg-brand-soft/50 px-2.5 py-0.5 text-[0.75rem] font-bold text-brand">
                  <Stethoscope aria-hidden className="h-3.5 w-3.5" />
                  {stageLabel}
                </span>
              )
            )}
            {!isGeneralInfo && top && <UrgencyBanner urgency={top} specialist={doctorTitle || undefined} />}
          </div>
          <ListenButton label={t.readAloud} onClick={() => speak(clean, lang, bcp47(lang))} />
        </div>

        {s?.clinical_recap && (
          <p className="rounded-[10px] border border-border/60 bg-surface/80 px-3 py-2 text-[0.875rem] text-muted">
            <span className="font-semibold text-text">{lang === "hi" ? "अभी तक समझा: " : lang === "bn" ? "এখন পর্যন্ত যা বোঝা গেছে: " : "What I've understood: "}</span>
            {s.clinical_recap}
          </p>
        )}
        <ReplyText text={rich} terms={terms} label={t.whenToSeeDoctor} />
        <SavedFacts facts={a.saved_facts} />
        <Storylines items={s?.storylines} />
      </div>

      {/* 2. Separate Clickable Cards: Doctor to Visit & Specific Tests (only for clinical/disease turns) */}
      {!isGeneralInfo && (Boolean(doctorTitle) || testNames.length > 0 || showDoctorOrTests) && (
        <div className="space-y-2.5">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {Boolean(doctorTitle) && (
              <button
                type="button"
                onClick={() => setActiveBox((cur) => cur === "doctor" ? null : "doctor")}
                aria-expanded={activeBox === "doctor"}
                className={`v2-tile flex w-full min-w-0 flex-col justify-between gap-2 rounded-[16px] border p-3.5 text-start shadow-[var(--e1)] ${
                  activeBox === "doctor"
                    ? "border-brand bg-brand-soft/35"
                    : "border-border/75 bg-surface hover:border-brand/50"
                }`}
              >
                <div className="flex w-full items-center justify-between gap-2">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-brand-soft text-brand">
                    <Stethoscope aria-hidden className="h-4 w-4" />
                  </span>
                  <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-surface-2 px-2.5 py-0.5 text-[0.72rem] font-bold text-brand">
                    {routing?.tier_label?.split(" ")[0] || (lang === "hi" ? "डॉक्टर" : lang === "bn" ? "ডাক্তার" : "Doctor")}
                    <ChevronDown aria-hidden className={`h-3.5 w-3.5 transition-transform duration-200 ${activeBox === "doctor" ? "rotate-180" : ""}`} />
                  </span>
                </div>
                <div className="min-w-0 space-y-0.5 break-words">
                  <p className="text-[0.7rem] font-bold uppercase tracking-wider text-muted">
                    {lang === "hi" ? "किस डॉक्टर को दिखाएं" : lang === "bn" ? "কোন ডাক্তার দেখাবেন" : "Doctor to Visit"}
                  </p>
                  <p className="text-[0.95rem] font-bold leading-snug text-text">
                    {doctorTitle || "General Physician"}
                  </p>
                  {routing?.primary_specialty && (
                    <p className="text-[0.78rem] leading-snug text-muted">
                      {routing.primary_specialty}
                      {routing.secondary_specialties?.length ? ` · ${routing.secondary_specialties[0]}` : ""}
                    </p>
                  )}
                </div>
              </button>
            )}

            {(testNames.length > 0 || showDoctorOrTests) && (
              <button
                type="button"
                onClick={() => setActiveBox((cur) => cur === "tests" ? null : "tests")}
                aria-expanded={activeBox === "tests"}
                className={`v2-tile flex w-full min-w-0 flex-col justify-between gap-2 rounded-[16px] border p-3.5 text-start shadow-[var(--e1)] ${
                  activeBox === "tests"
                    ? "border-info bg-info-soft/35"
                    : "border-border/75 bg-surface hover:border-info/50"
                }`}
              >
                <div className="flex w-full items-center justify-between gap-2">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] bg-info-soft text-info">
                    <FlaskConical aria-hidden className="h-4 w-4" />
                  </span>
                  <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-surface-2 px-2.5 py-0.5 text-[0.72rem] font-bold text-info">
                    {testNames.length ? `${testNames.length} ${lang === "hi" ? "जांच" : lang === "bn" ? "পরীক্ষা" : testNames.length === 1 ? "test" : "tests"}` : "Optional"}
                    <ChevronDown aria-hidden className={`h-3.5 w-3.5 transition-transform duration-200 ${activeBox === "tests" ? "rotate-180" : ""}`} />
                  </span>
                </div>
                <div className="min-w-0 space-y-0.5 break-words">
                  <p className="text-[0.7rem] font-bold uppercase tracking-wider text-muted">
                    {lang === "hi" ? "संभावित जांच" : lang === "bn" ? "সম্ভাব্য পরীক্ষা" : "Specific Tests"}
                  </p>
                  {testNames.length ? (
                    <p className="text-[0.95rem] font-bold leading-snug text-text">
                      {testNames.slice(0, 2).join(" · ")}{testNames.length > 2 ? ` +${testNames.length - 2}` : ""}
                    </p>
                  ) : (
                    <p className="text-[0.875rem] font-semibold text-muted">
                      {lang === "hi" ? "डॉक्टर तय करेंगे" : lang === "bn" ? "চিকিৎসক নির্ধারণ করবেন" : "Decided after exam"}
                    </p>
                  )}
                  <p className="text-[0.78rem] leading-snug text-muted">
                    {lang === "hi" ? "विवरण के लिए टैप करें" : lang === "bn" ? "বিস্তারিত দেখতে ট্যাপ করুন" : "Tap for details & rationale"}
                  </p>
                </div>
              </button>
            )}
          </div>

          {activeBox === "doctor" && (
            <div className="v2-panel-in space-y-2.5 rounded-[16px] border border-brand/35 bg-brand-soft/25 p-3.5 text-[0.9375rem]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="font-bold text-text">{doctorTitle || "General Physician"}</p>
                {routing?.tier_label && (
                  <span className="rounded-full bg-surface px-2.5 py-0.5 text-[0.75rem] font-bold text-brand shadow-[var(--rim)]">
                    {routing.tier_label}
                  </span>
                )}
              </div>
              {routing?.secondary_specialties?.length ? (
                <p className="text-[0.8125rem] text-muted">
                  <strong className="text-text">Multidisciplinary:</strong> {routing.primary_specialty}, {routing.secondary_specialties.join(", ")}
                </p>
              ) : null}
              {routing?.why_appropriate && <p className="text-[0.875rem] leading-relaxed text-text/90">{routing.why_appropriate}</p>}
              {routing?.credential_notice && <p className="text-[0.8125rem] text-muted">{routing.credential_notice}</p>}
              <div className="flex flex-wrap gap-2 pt-1">
                <a href="https://esanjeevani.mohfw.gov.in" target="_blank" rel="noreferrer"
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-full bg-brand px-3.5 text-[0.8125rem] font-semibold text-brand-ink">
                  <Video aria-hidden className="h-4 w-4" />{t.findDoctor}
                </a>
                <Link href="/me/visit"
                  className="inline-flex min-h-10 items-center gap-1.5 rounded-full border border-border-input bg-surface px-3.5 text-[0.8125rem] font-semibold hover:bg-surface-2">
                  <Stethoscope aria-hidden className="h-4 w-4" />{t.prepareVisit}
                </Link>
              </div>
            </div>
          )}

          {activeBox === "tests" && (
            <div className="v2-panel-in space-y-2.5 rounded-[16px] border border-info/35 bg-info-soft/25 p-3.5 text-[0.9375rem]">
              {invList ? (
                <ul className="space-y-2">
                  {invList.map((inv, idx) => (
                    <li key={idx} className="rounded-[12px] border border-border/70 bg-surface p-2.5">
                      <div className="flex flex-wrap items-center justify-between gap-1.5">
                        <span className="font-bold text-text">{inv.investigation_name}</span>
                        <span className={`rounded-full px-2 py-0.5 text-[0.72rem] font-semibold ${
                          inv.priority_context === "urgent_evaluation" ? "bg-danger-soft text-danger"
                          : inv.priority_context === "considered_later" ? "bg-surface-2 text-muted"
                          : "bg-brand-soft/60 text-brand"
                        }`}>{priorityLabel(inv.priority_context)}</span>
                      </div>
                      {inv.why_relevant && <p className="mt-1 text-[0.875rem] text-text/90">{inv.why_relevant}</p>}
                      {inv.what_it_evaluates && <p className="mt-0.5 text-[0.8125rem] text-muted"><strong>Evaluates:</strong> {inv.what_it_evaluates}</p>}
                      {inv.safety_considerations && <p className="mt-0.5 text-[0.8125rem] font-medium text-warn">{inv.safety_considerations}</p>}
                    </li>
                  ))}
                </ul>
              ) : fallbackTests.length ? (
                <ul className="list-disc space-y-1 ps-5 text-[0.9375rem]">
                  {fallbackTests.map((x, i) => <li key={i}>{x}</li>)}
                </ul>
              ) : (
                <p className="text-[0.875rem] text-muted">No specific tests are required right now; your doctor will advise after examination.</p>
              )}
              {!!testNames.length && (
                <div className="flex flex-wrap gap-2 pt-1">
                  <button type="button" onClick={addTests} disabled={addedTests}
                    className="inline-flex min-h-10 items-center gap-1.5 rounded-full bg-surface px-3 text-[0.8125rem] font-semibold shadow-[var(--rim)] hover:bg-surface-2 disabled:opacity-70">
                    <ClipboardList aria-hidden className="h-4 w-4" />{addedTests ? t.testsAdded : t.addTestsToList}
                  </button>
                  <button type="button" onClick={showLabs} disabled={labs !== undefined}
                    className="inline-flex min-h-10 items-center gap-1.5 rounded-full bg-surface px-3 text-[0.8125rem] font-semibold shadow-[var(--rim)] hover:bg-surface-2 disabled:opacity-70">
                    <FileText aria-hidden className="h-4 w-4" />{t.showMyResults}
                  </button>
                </div>
              )}
              {labs !== undefined && (
                labs?.length ? (
                  <ul className="space-y-1 rounded-[12px] border border-border bg-surface p-2.5 text-[0.875rem]">
                    {labs.map((l) => (
                      <li key={l.id} className="flex justify-between gap-2">
                        <span>{l.test_name}</span>
                        <span className={l.flag === "H" || l.flag === "L" ? "font-semibold text-danger" : ""}>
                          {l.value ?? "—"} {l.unit ?? ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-[0.8125rem] text-muted">{t.noMatchingResults}</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* 3. Possible Causes (only for clinical/disease turns) */}
      {!isGeneralInfo && <Causes conditions={conditions} onAsk={onAsk} defaultOpen={responseStyle === "detailed"} />}

      {/* 4. Space-Optimized What to Do Now & Food / Lifestyle */}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <div className="min-w-0">
          <DoNow care={kind === "symptoms" ? s?.self_care : s?.tips} watch={s?.watch_for} question={question}
            symptomatic={kind === "symptoms"} defaultOpen />
        </div>
        {s?.nutrition?.length > 0 && (
          <div className="min-w-0 rounded-[16px] border border-border/75 bg-surface p-3.5 shadow-[var(--e1)]">
            <h3 className="mb-1.5 flex items-center gap-1.5 font-bold text-text">
              <Utensils aria-hidden className="h-4 w-4 text-ok" />{t.nutrition}
            </h3>
            <ul className="list-disc space-y-1 ps-5 text-[0.9375rem] leading-snug">
              {s.nutrition.map((x: string, i: number) => <li key={i}>{x}</li>)}
            </ul>
          </div>
        )}
      </div>

      {/* 5. Medicine links, Follow-up Question & Doctor Note */}
      {s?.medicine_links?.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {s.medicine_links.map((m: any) => (
            <Link key={m.id} href={`/medicines/${m.id}`} className="inline-flex min-h-11 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">
              <Pill aria-hidden className="h-4 w-4 text-muted" />{t.aboutMedicine}: {m.name}
            </Link>
          ))}
        </div>
      )}

      {a.clinical?.followup_question && (
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
          <p className="text-[1.0625rem] font-semibold leading-snug">{a.clinical.followup_question.question}</p>
          {a.clinical.followup_question.why_asking && (
            <p className="mt-1 text-[0.8125rem] text-muted">{a.clinical.followup_question.why_asking}</p>
          )}
          {!answered && (
            <div className="mt-2.5 flex flex-wrap gap-2">
              <button type="button" onClick={() => onAsk?.(t.yes)} className="btn btn-primary min-h-11 px-4">{t.yes}</button>
              <button type="button" onClick={() => onAsk?.(t.no)} className="btn btn-outline min-h-11 px-4">{t.no}</button>
              <button type="button" onClick={() => onAsk?.(t.unsure)} className="btn btn-outline min-h-11 px-4">{t.unsure}</button>
              <button type="button" onClick={() => onFollowUp?.("")} className="btn btn-outline min-h-11">{t.replyToQuestion}</button>
            </div>
          )}
        </section>
      )}

      {!a.clinical?.followup_question && !answered && s?.follow_up_questions?.length > 0 && onAsk && (
        <section className="rounded-[16px] border border-brand/25 bg-brand-soft/20 p-3.5">
          <p className="mb-2 text-[0.8125rem] font-bold text-brand">
            {lang === "hi" ? "बातचीत जारी रखें" : lang === "bn" ? "পরামর্শ চালিয়ে যান" : "Continue the consultation"}
          </p>
          <div className="flex flex-wrap gap-2">
            {s.follow_up_questions.slice(0, 3).map((qText: string, idx: number) => (
              <button
                key={idx}
                type="button"
                onClick={() => onAsk(qText)}
                className="rounded-full border border-border-input bg-surface px-3.5 py-2 text-left text-[0.875rem] font-medium text-text transition-colors hover:border-brand/50 hover:bg-brand-soft/30"
              >
                {qText}
              </button>
            ))}
          </div>
        </section>
      )}

      {s?.doctor_note && (
        <section className="rounded-[14px] border border-info/30 bg-info-soft/40 p-3 text-[0.875rem]">
          <p className="font-semibold text-info">{lang === "hi" ? "डॉक्टर को दिखाते समय यह बताएं" : lang === "bn" ? "ডাক্তারকে দেখানোর সময় এটি বলুন" : "What to mention to your doctor"}</p>
          <p className="mt-0.5 text-text">{s.doctor_note}</p>
        </section>
      )}

      {/* 6. Footer Trust & Actions */}
      <div className="flex flex-wrap items-center gap-2">
        {kind === "symptoms" && <ConfidencePill level={confidenceOf(a)} />}
        {a.tier && <PrivacyBadge local={a.tier === "L"} />}
        {a.clinical?.encounter_id && (
          <Link href="/context" className="rounded-full bg-surface-2 px-3 py-1 text-[0.8125rem] font-semibold hover:bg-border">
            {t.navContext} →
          </Link>
        )}
      </div>
      <SourceList sources={a.citations ?? []} consulted={s?.consulted ?? []} />
      <DisclaimerChip />

      <div className="flex flex-wrap gap-2 border-t border-border pt-3">
        {question && (
          <button disabled={savedDoc} aria-pressed={savedDoc}
            onClick={() => api("/me/visit-questions", { method: "POST", body: JSON.stringify({ text: question.slice(0, 300), source: "chat" }) })
              .then(() => setSavedDoc(true)).catch(() => {})}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">
            {savedDoc ? <Check aria-hidden className="h-4 w-4 text-ok" /> : <Stethoscope aria-hidden className="h-4 w-4" />}
            {savedDoc ? t.savedForDoctor : t.saveForDoctor}
          </button>
        )}
        <button onClick={() => { setVote("up"); sendVote("up"); }} aria-pressed={vote === "up"} aria-label={t.helpful}
          className={`grid h-11 w-11 place-items-center rounded-full ${vote === "up" ? "bg-ok-soft text-ok" : "bg-surface-2"}`}>
          <ThumbsUp aria-hidden className="h-4 w-4" />
        </button>
        <button onClick={() => { setVote("down"); sendVote("down"); }} aria-pressed={vote === "down"} aria-label={t.notHelpful}
          className={`grid h-11 w-11 place-items-center rounded-full ${vote === "down" ? "bg-danger-soft text-danger" : "bg-surface-2"}`}>
          <ThumbsDown aria-hidden className="h-4 w-4" />
        </button>
        {typeof navigator !== "undefined" && "share" in navigator && (
          <button onClick={() => navigator.share({ title: "SushrutAI", text: `${a.reply}\n\n${t.disclaimerShort}` }).catch(() => {})}
            className="inline-flex min-h-11 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.875rem] font-semibold">
            <Share2 aria-hidden className="h-4 w-4" />{t.share}
          </button>
        )}
      </div>
    </article>
  );
}
