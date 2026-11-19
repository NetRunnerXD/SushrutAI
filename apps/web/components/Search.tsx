"use client";

import { FileText, FlaskConical, HeartPulse, IdCard, MessageCircle, Pill, Search as SearchIcon, ShieldAlert, Stethoscope, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { Sheet } from "./ui/Sheet";

type Results = { my_medicines: any[]; labs: any[]; conditions: string[]; allergies: string[]; chats: any[]; medicines: any[] };
type Row = { href: string; label: string; sub?: string; icon: LucideIcon };

/** Pages and actions, matched on the client in the current language and in English. */
function pages(t: any): (Row & { words: string })[] {
  const P = (href: string, label: string, icon: LucideIcon, words = "") => ({ href, label, icon, words: `${label} ${words}`.toLowerCase() });
  return [
    P("/", t.navHome, FileText, "home dashboard today"),
    P("/ask", t.navAsk, MessageCircle, "ask chat assistant question"),
    P("/ask/check", t.checkSymptoms, Stethoscope, "symptom check triage"),
    P("/medicines", t.navMedicines, Pill, "medicines dose reminders cheaper generic"),
    P("/medicines/rx", t.rxTitle, FileText, "prescription upload read"),
    P("/records?tab=labs", t.labResults, FlaskConical, "lab report blood test"),
    P("/records?tab=vitals", t.vitals, HeartPulse, "bp sugar weight pulse vitals chart"),
    P("/me", t.profileTitle, FileText, "profile me allergies conditions"),
    P("/me/family", t.family, FileText, "family child parent"),
    P("/me/visit", t.visitTitle, Stethoscope, "doctor visit summary print pdf"),
    P("/me/card", t.cardTitle, IdCard, "patient card image share whatsapp png jpg photo emergency blood group"),
    P("/me/care", t.careTitle ?? "Vaccines & check-ups", ShieldAlert, "vaccine immunisation pregnancy check-up anc"),
    P("/me/settings", t.settingsTitle, FileText, "settings language text size dark privacy"),
  ];
}

export function SearchButton({ field = false }: { field?: boolean }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = el?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el?.tagName);
      if ((e.key === "/" && !typing) || (e.key.toLowerCase() === "k" && (e.ctrlKey || e.metaKey))) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <>
      {field ? (
        <button onClick={() => setOpen(true)} aria-label={`${t.searchLabel} (Ctrl+K)`}
          className="flex h-11 w-full max-w-[460px] items-center gap-2.5 rounded-full border border-[var(--d-tile-line)] bg-[var(--d-tile)] px-4 text-left text-muted transition-colors duration-[120ms] hover:border-[var(--d-tile-line-hover)] hover:bg-[var(--d-tile-hover)]">
          <SearchIcon aria-hidden className="h-[18px] w-[18px]" /><span className="flex-1 truncate">{t.searchLabel}…</span>
          <kbd className="rounded-[6px] border border-[var(--d-tile-line)] px-1.5 font-mono text-[0.75rem]">Ctrl K</kbd>
        </button>
      ) : (
      <button onClick={() => setOpen(true)} aria-label={`${t.searchLabel} (/)`} title={`${t.searchLabel} (/)`}
        className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-rail-2 text-rail-text hover:bg-rail-muted/30">
        <SearchIcon aria-hidden className="h-5 w-5" /></button>
      )}
      <Sheet open={open} onClose={() => setOpen(false)} title={t.searchLabel}>
        {open && <SearchPanel onPick={() => setOpen(false)} />}
      </Sheet>
    </>
  );
}

function SearchPanel({ onPick }: { onPick: () => void }) {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Results | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => { setTimeout(() => input.current?.focus(), 30); }, []);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return; }
    const id = setTimeout(() => api<Results>(`/search?q=${encodeURIComponent(q.trim())}`).then(setRes).catch(() => setRes(null)), 250);
    return () => clearTimeout(id);
  }, [q]);

  const ql = q.trim().toLowerCase();
  const groups: [string, Row[]][] = [
    [t.grpPages, ql ? pages(t).filter((p) => p.words.includes(ql)).slice(0, 4) : pages(t).slice(0, 6)],
  ];
  if (res) {
    groups.push(
      [t.grpMyMedicines, res.my_medicines.map((m) => ({ href: "/medicines", label: m.name, icon: Pill }))],
      [t.grpLabs, res.labs.map((l) => ({ href: "/records?tab=labs", label: l.name, sub: `${l.value ?? ""} ${l.unit ?? ""}`.trim(), icon: FlaskConical }))],
      [t.grpConditions, res.conditions.map((c) => ({ href: "/me", label: c, icon: HeartPulse }))],
      [t.grpAllergies, res.allergies.map((a) => ({ href: "/me", label: a, icon: ShieldAlert }))],
      [t.grpChats, res.chats.map((c) => ({ href: `/ask?session=${c.id}`, label: c.snippet ?? c.title, sub: c.snippet ? c.title.slice(0, 28) : undefined, icon: MessageCircle }))],
      [t.grpMedicines, res.medicines.map((m) => ({ href: `/medicines/${m.id}`, label: m.name,
        sub: m.per_unit_inr != null ? `₹${m.per_unit_inr.toFixed(2)}` : undefined, icon: Pill }))],
    );
  }
  const shown = groups.filter(([, rows]) => rows.length);
  const empty = res && shown.length <= 1 && !shown.some(([g]) => g !== t.grpPages);

  // arrow keys move through results; Enter follows the focused link (native)
  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const links = Array.from(list.current?.querySelectorAll<HTMLAnchorElement>("a") ?? []);
    if (!links.length) return;
    e.preventDefault();
    const i = links.indexOf(document.activeElement as HTMLAnchorElement);
    const next = e.key === "ArrowDown" ? (i + 1) % links.length : i <= 0 ? -1 : i - 1;
    (next === -1 ? input.current : links[next])?.focus();
  };

  return (
    <div onKeyDown={onKey} className="space-y-3">
      <label htmlFor="global-search" className="sr-only">{t.searchPlaceholder}</label>
      <input id="global-search" ref={input} type="search" value={q} onChange={(e) => setQ(e.target.value)}
        placeholder={t.searchPlaceholder} className="input" autoComplete="off" enterKeyHint="search" />
      <div ref={list} className="max-h-[60dvh] space-y-3 overflow-y-auto" aria-live="polite">
        {shown.map(([g, rows]) => (
          <section key={g}>
            <h3 className="display mb-1 text-[0.9375rem] font-bold text-muted">{g}</h3>
            <ul>{rows.map((r, i) => (
              <li key={r.href + i}>
                <Link href={r.href} onClick={onPick} className="flex min-h-12 items-center gap-3 rounded-[12px] px-2 hover:bg-surface-2 focus:bg-surface-2">
                  <r.icon aria-hidden className="h-5 w-5 shrink-0 text-muted" />
                  <span className="min-w-0 flex-1 truncate">{r.label}</span>
                  {r.sub && <span className="font-mono text-[0.875rem] text-muted">{r.sub}</span>}
                </Link></li>))}</ul>
          </section>
        ))}
        {empty && <p className="text-muted">{fill(t.searchNone, { q })}</p>}
      </div>
    </div>
  );
}
