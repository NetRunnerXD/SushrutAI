"use client";

import { Check, UserRound, Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, getProfile, switchProfile, type ActiveProfile } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";
import { Sheet } from "./ui/Sheet";

export type Member = { id: string; name: string; relation: string; age: number | null; sex: string | null };

const TONE: Record<string, string> = {
  child: "bg-accent text-accent-ink", parent: "bg-info-soft text-info", grandparent: "bg-info-soft text-info",
  spouse: "bg-brand-soft text-brand-strong", sibling: "bg-ok-soft text-ok", other: "bg-surface-2 text-text",
};

export function Avatar({ name, relation, size = 40 }: { name?: string; relation?: string; size?: number }) {
  return (
    <span aria-hidden style={{ width: size, height: size }}
      className={`grid shrink-0 place-items-center rounded-full font-bold ${name ? TONE[relation ?? "other"] ?? TONE.other : "bg-brand text-brand-ink"}`}>
      {name ? name.trim().charAt(0).toUpperCase() : <UserRound className="h-5 w-5" />}
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
  const [members, setMembers] = useState<Member[] | null>(null);

  useEffect(() => {
    if (open && members === null) api<Member[]>("/family").then(setMembers).catch(() => setMembers([]));
  }, [open, members]);

  const Row = ({ m, current }: { m: Member | null; current: boolean }) => (
    <button onClick={() => (current ? setOpen(false) : switchProfile(m && { id: m.id, name: m.name, relation: m.relation }))}
      aria-current={current ? "true" : undefined}
      className={`flex min-h-14 w-full items-center gap-3 rounded-[8px] px-3 text-left ${current ? "bg-brand-soft" : "hover:bg-surface-2"}`}>
      <Avatar name={m?.name} relation={m?.relation} />
      <span className="flex-1"><span className="block font-semibold">{m ? m.name : t.myself}</span>
        {m && <span className="block text-[0.9375rem] text-muted">{memberLine(m, t)}</span>}</span>
      {current && <Check aria-hidden className="h-5 w-5 text-brand" />}
    </button>
  );

  return (
    <>
      <button onClick={() => setOpen(true)} aria-label={`${t.whoseHealth} ${active ? active.name : t.myself}`}
        className={`grid h-12 w-12 shrink-0 place-items-center rounded-full ${active ? "ring-[3px] ring-accent" : ""}`}>
        <Avatar name={active?.name} relation={active?.relation} />
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

/** Always-visible reminder when a family member's record is open, so nothing is added to the wrong person. */
export function ProfileBanner() {
  const { t } = useI18n();
  const active = useActiveProfile();
  if (!active) return null;
  return (
    <div role="status" className="flex items-center justify-center gap-3 bg-accent px-4 py-1.5 text-[0.9375rem] font-semibold text-accent-ink">
      <span>{fill(t.viewingFor, { name: active.name })}</span>
      <button onClick={() => switchProfile(null)} className="min-h-12 rounded-full border-2 border-accent-ink px-3 underline-offset-2">{t.switchBack}</button>
    </div>
  );
}
