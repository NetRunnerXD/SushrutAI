"use client";

import { Check, Plus, UserRound, Users } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, getProfile, getTokens, switchProfile, type ActiveProfile } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { Sheet } from "./ui/Sheet";

export type Member = { id: string; name: string; relation: string; age: number | null; sex: string | null };

// ---------------------------------------------------------------- spice codes
// Every person in the household has one spice colour (the account holder is always haldi, --sp-0), used on their
// tier, their avatar rim and their record strip, so whose record is open is never in doubt. Members take the next
// spices in the order they were added; the order is remembered on this device so colours never shuffle.
const ORDER_KEY = "sushrut.familyOrder";
function readOrder(): string[] {
  try { return JSON.parse(localStorage.getItem(ORDER_KEY) || "[]"); } catch { return []; }
}
function rememberFamily(ms: Member[]) {
  const order = readOrder();
  for (const m of ms) if (!order.includes(m.id)) order.push(m.id);
  try { localStorage.setItem(ORDER_KEY, JSON.stringify(order)); } catch { /* ignore */ }
}
export function spiceOf(id?: string | null): string {
  if (!id) return "var(--sp-0)";
  let i = typeof window === "undefined" ? -1 : readOrder().indexOf(id);
  if (i < 0) { let h = 0; for (const c of id) h = (h * 31 + c.charCodeAt(0)) | 0; i = Math.abs(h); }
  return `var(--sp-${(i % 5) + 1})`;
}

/** The household list, shared by the header tiers, Home and the family page (refreshes on sushrut:family-changed). */
// One request serves every component that mounts together (header tiers, switcher, page); dropped on change.
let shared: Promise<Member[]> | null = null;
function fetchFamily(fresh = false): Promise<Member[]> {
  if (!getTokens()) return Promise.resolve([]);
  if (fresh || !shared) {
    shared = api<Member[]>("/family").then((ms) => { rememberFamily(ms); return ms; });
    shared.catch(() => { shared = null; });
    setTimeout(() => { shared = null; }, 5000);
  }
  return shared;
}

export function useFamily() {
  const [members, setMembers] = useState<Member[] | null>(null);
  const load = useCallback((fresh = false) => {
    fetchFamily(fresh).then(setMembers).catch(() => setMembers([]));
  }, []);
  useEffect(() => {
    load();
    const onChange = () => load(true);
    window.addEventListener("sushrut:family-changed", onChange);
    window.addEventListener("sushrut:auth-changed", onChange);
    return () => {
      window.removeEventListener("sushrut:family-changed", onChange);
      window.removeEventListener("sushrut:auth-changed", onChange);
    };
  }, [load]);
  return { members, reload: load };
}

/** A person as a small katori: their initial in a steel well with a rim in their spice colour. */
export function Avatar({ name, id, size = 40 }: { name?: string; id?: string | null; relation?: string; size?: number }) {
  return (
    <span aria-hidden style={{ width: size, height: size, boxShadow: `inset 0 0 0 3px ${spiceOf(id)}, var(--well)` }}
      className="grid shrink-0 place-items-center rounded-full bg-surface-2 font-bold text-text">
      {name ? <span style={{ fontSize: size * 0.42 }} className="display leading-none">{name.trim().charAt(0).toUpperCase()}</span>
        : <UserRound style={{ width: size * 0.48, height: size * 0.48 }} />}
    </span>
  );
}

/** Whose record the app is showing; null = the account holder. Read after mount (localStorage). */
export function useActiveProfile(): ActiveProfile | null {
  const [p, setP] = useState<ActiveProfile | null>(null);
  useEffect(() => setP(getProfile()), []);
  return p;
}

export function memberLine(m: Member, t: any) {
  return [t.rel?.[m.relation] ?? m.relation, m.age != null ? fill(t.yearsOld, { n: m.age }) : null].filter(Boolean).join(" · ");
}

export function ProfileSwitcher() {
  const { t } = useI18n();
  const active = useActiveProfile();
  const [open, setOpen] = useState(false);
  const { members } = useFamily();

  const Row = ({ m, current }: { m: Member | null; current: boolean }) => (
    <button onClick={() => (current ? setOpen(false) : switchProfile(m && { id: m.id, name: m.name, relation: m.relation }))}
      aria-current={current ? "true" : undefined}
      className={`flex min-h-14 w-full items-center gap-3 rounded-[14px] px-3 text-start ${current ? "bg-brand-soft" : "hover:bg-surface-2"}`}>
      <Avatar name={m?.name} id={m?.id} />
      <span className="flex-1"><span className="block font-semibold">{m ? m.name : t.myself}</span>
        {m && <span className="block text-[0.9375rem] text-muted">{memberLine(m, t)}</span>}</span>
      {current && <Check aria-hidden className="h-5 w-5 text-muted" strokeWidth={2.6} />}
    </button>
  );

  // With a household, the tiers below the header already say whose record is open and switch it.
  if (members?.length && !open) return null;
  return (
    <>
      <button onClick={() => setOpen(true)} aria-label={`${t.whoseHealth} ${active ? active.name : t.myself}`}
        className="grid h-12 w-12 shrink-0 place-items-center rounded-full">
        <Avatar name={active?.name} id={active?.id} size={40} />
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={t.whoseHealth}>
        <div className="space-y-1">
          <Row m={null} current={!active} />
          {members === null ? <p className="p-3 text-muted">…</p>
            : members.map((m) => <Row key={m.id} m={m} current={active?.id === m.id} />)}
        </div>
        <Link href="/me/family" onClick={() => setOpen(false)} className="btn btn-outline mt-4 w-full">
          <Users aria-hidden className="h-5 w-5" />{members?.length ? t.manageFamily : t.addMember}</Link>
      </Sheet>
    </>
  );
}

/**
 * The tiffin carrier: one tier per person across the top of the page. The open record's tier is lifted and joined to
 * the page below it; the others sit back in the gunmetal. Shown once the household has more than one person.
 */
export function FamilyTiers() {
  const { t } = useI18n();
  const active = useActiveProfile();
  const { members } = useFamily();
  if (!members?.length) return null;
  const people: (Member | null)[] = [null, ...members];
  return (
    <nav aria-label={t.whoseHealth} className="no-scrollbar flex items-end gap-1 overflow-x-auto px-3 pt-2 sm:px-4 lg:px-8">
      {people.map((m) => {
        const on = m ? active?.id === m.id : !active;
        return (
          <button key={m?.id ?? "me"} type="button" aria-current={on ? "true" : undefined}
            onClick={() => !on && switchProfile(m && { id: m.id, name: m.name, relation: m.relation })}
            className={`flex min-h-12 min-w-0 shrink-0 items-center gap-2 rounded-t-[14px] px-3.5 text-[0.9375rem]
              ${on ? "lift bg-bg font-extrabold uppercase tracking-[0.06em] text-text shadow-[var(--rim)]" : "bg-rail-2 font-semibold text-rail-muted hover:text-rail-text"}`}>
            <span aria-hidden className="h-3 w-3 shrink-0 rounded-full" style={{ background: spiceOf(m?.id) }} />
            <span className="max-w-[9rem] truncate">{m ? m.name : t.myself}</span>
          </button>
        );
      })}
      <Link href="/me/family" aria-label={t.addMember}
        className="grid min-h-12 w-12 shrink-0 place-items-center rounded-t-[14px] text-rail-muted hover:bg-rail-2 hover:text-rail-text">
        <Plus aria-hidden className="h-5 w-5" /></Link>
    </nav>
  );
}

/** Always-visible reminder when a family member's record is open, so nothing is added to the wrong person. */
export function ProfileBanner() {
  const { t } = useI18n();
  const active = useActiveProfile();
  if (!active) return null;
  return (
    <div role="status" className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 border-b border-border bg-bg px-4 py-1.5 text-[0.9375rem] font-semibold">
      <span className="flex items-center gap-2">
        <span aria-hidden className="h-3 w-3 rounded-full" style={{ background: spiceOf(active.id) }} />
        {fill(t.viewingFor, { name: active.name })}</span>
      <button onClick={() => switchProfile(null)} className="min-h-12 rounded-full px-3 text-info underline">{t.switchBack}</button>
    </div>
  );
}
