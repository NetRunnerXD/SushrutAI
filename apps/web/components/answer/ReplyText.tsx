"use client";

import { Stethoscope } from "lucide-react";
import { Fragment, type ReactNode } from "react";

/** Model reply -> tidy text that keeps **bold**: no markdown headings, no internal enum tokens. */
export function tidyReply(reply: string, t?: any): string {
  return (reply ?? "")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\b(see_doctor_24h|see_doctor_week|self_care)\b:?/gi, (m) => {
      const k = m.replace(/:$/, "").toLowerCase();
      return (t?.urgency?.[k] ?? k.replace(/_/g, " ")) + (m.endsWith(":") ? ":" : "");
    })
    .replace(/[ \t]+\n/g, "\n")
    .trim();
}

const SENTENCE = /(?<=[.!?।])\s+(?=\S)/;
const BULLET = /^\s*([*\-•]|\d+[.)])\s+/;
// "See a doctor if…", "go to hospital", "call 108": the step that matters most, shown as its own line
// whole words only: "clinic" must not match "clinical"
const DOCTOR = /(\b(?:doctors?|physicians?|hospitals?|clinics?|emergency|108|112|specialists?|daktar)\b|डॉक्टर|अस्पताल|चिकित्सक)/i;
// durations and counts a reader scans for: "2 days", "48 hours", "3 दिन"
// (no trailing \b: JavaScript's \b does not see Devanagari letters as word characters)
const DURATION = /\b\d+(?:[.,]\d+)?\s?(?:days?|hours?|weeks?|months?|minutes?|mins?|din|ghante|hafte|दिनों|दिन|घंटों|घंटे|हफ़्तों|हफ़्ते|हफ्ते|महीनों|महीने|मिनट)(?![A-Za-z])/gi;

function esc(s: string) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }

/** Inline emphasis: **bold** from the model, plus the condition names and durations in the text. Text nodes only. */
export function Inline({ text, terms = [] }: { text: string; terms?: string[] }): ReactNode {
  const names = terms.filter((x) => x && x.length > 2).sort((a, b) => b.length - a.length).map(esc);
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((p, i) => {
    if (/^\*\*[^*]+\*\*$/.test(p)) return <strong key={i} className="font-bold text-text">{p.slice(2, -2)}</strong>;
    const re = new RegExp(`(${[...(names.length ? [names.join("|")] : []), DURATION.source].join("|")})`, "gi");
    const bits = p.split(re).filter((b) => b !== undefined);
    return <Fragment key={i}>{bits.map((b, j) => (j % 2 === 1 && b ? <strong key={j} className="font-semibold text-text">{b}</strong> : b))}</Fragment>;
  });
}

/** The answer's text, organised: a lead sentence, then short paragraphs (two sentences each), any lists as real
 *  lists, and the "when to see a doctor" sentence pulled out onto its own highlighted line. */
export function ReplyText({ text, terms = [], label }: { text: string; terms?: string[]; label: string }) {
  const lead = text.match(/^.+?[.!?।](\s|$)/s)?.[0]?.trim() ?? text;
  const rest = text.slice(lead.length).trim();
  const blocks = rest.split(/\n{2,}/).map((b) => b.split("\n").map((l) => l.trim()).filter(Boolean)).filter((b) => b.length);
  const paras: string[][] = [];
  const lists: string[][] = [];
  const order: ("p" | "l")[] = [];
  const doctor: string[] = [];
  for (const lines of blocks) {
    if (lines.every((l) => BULLET.test(l))) { lists.push(lines.map((l) => l.replace(BULLET, ""))); order.push("l"); continue; }
    const sentences = lines.join(" ").split(SENTENCE).map((x) => x.trim()).filter(Boolean);
    const keep: string[] = [];
    for (const s of sentences) (DOCTOR.test(s) && doctor.length < 2 ? doctor : keep).push(s);
    for (let i = 0; i < keep.length; i += 2) { paras.push(keep.slice(i, i + 2)); order.push("p"); }
  }
  let pi = 0, li = 0;
  return (
    <div className="max-w-[68ch] space-y-3">
      <p className="display text-[1.375rem] font-bold leading-snug"><Inline text={lead} terms={terms} /></p>
      {order.map((k, i) => k === "p" ? (
        <p key={i} className="text-[1.0625rem] leading-[1.7]"><Inline text={paras[pi++].join(" ")} terms={terms} /></p>
      ) : (
        <ul key={i} className="list-disc space-y-1 pl-6 text-[1.0625rem] leading-[1.6]">
          {lists[li++].map((l, j) => <li key={j}><Inline text={l} terms={terms} /></li>)}
        </ul>
      ))}
      {doctor.length > 0 && (
        <p className="flex items-start gap-2.5 rounded-[14px] border border-info/30 bg-info-soft px-3.5 py-2.5 text-[1rem] leading-[1.6]">
          <Stethoscope aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-info" />
          <span><span className="sr-only">{label}: </span><Inline text={doctor.join(" ")} terms={terms} /></span>
        </p>
      )}
    </div>
  );
}
