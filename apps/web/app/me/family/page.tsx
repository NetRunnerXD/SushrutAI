"use client";

import { Pencil, Plus, ShieldCheck, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Avatar, memberLine, useActiveProfile, type Member } from "@/components/Family";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, EmptyState, PageTitle, Skeleton } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Sheet } from "@/components/ui/Sheet";
import { Banner } from "@/components/ui/Trust";
import { api, switchProfile } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

const RELATIONS = ["child", "parent", "spouse", "sibling", "grandparent", "other"] as const;
type Draft = { name: string; relation: string; dob: string; sex: string };
const EMPTY: Draft = { name: "", relation: "child", dob: "", sex: "" };

export default function FamilyPage() {
  const { t } = useI18n();
  const active = useActiveProfile();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Member | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = () => api<Member[]>("/family").then(setMembers).catch((e) => { setMembers([]); setErr(e.message); });
  useEffect(() => { load(); }, []);

  async function save() {
    if (!draft.name.trim()) return;
    setBusy(true); setErr(null);
    try {
      if (editing) {
        await api(`/family/${editing.id}`, { method: "PATCH", body: JSON.stringify({ name: draft.name.trim(), relation: draft.relation }) });
        if (active?.id === editing.id) switchProfile({ id: editing.id, name: draft.name.trim(), relation: draft.relation }, "/me/family");
      } else {
        await api("/family", { method: "POST", body: JSON.stringify({
          name: draft.name.trim(), relation: draft.relation, dob: draft.dob || null, sex: draft.sex || null }) });
      }
      setAdding(false); setEditing(null); setDraft(EMPTY); load(); window.dispatchEvent(new Event("sushrut:family-changed"));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }
  async function remove(m: Member) {
    if (!confirm(fill(t.removeConfirm, { name: m.name }))) return;
    await api(`/family/${m.id}`, { method: "DELETE" });
    if (active?.id === m.id) switchProfile(null, "/me/family");
    else { load(); window.dispatchEvent(new Event("sushrut:family-changed")); }
  }

  const open = adding || editing !== null;
  const close = () => { setAdding(false); setEditing(null); setDraft(EMPTY); };

  return (
    <div className="mx-auto max-w-3xl space-y-4 desk:max-w-[1100px]">
      <PageTitle sub={t.familyHelp}>{t.family}</PageTitle>
      {err && <Banner tone="warning">{err}</Banner>}

      <Card>
        <CardHeader icon={Users} title={t.whoseHealth} action={<Button icon={Plus} onClick={() => setAdding(true)}>{t.addMember}</Button>} />
        <ul className="divide-y divide-border">
          <li className="flex items-center gap-3 py-3">
            <Avatar />
            <span className="flex-1 font-semibold">{t.myself}</span>
            {active ? <Button variant="secondary" onClick={() => switchProfile(null)}>{t.switchTo}</Button>
              : <span className="inline-flex items-center rounded-full bg-text px-3 py-1 text-[0.875rem] font-bold text-bg">✓</span>}
          </li>
          {members === null ? <li className="py-3"><Skeleton className="h-12" /></li> : members.map((m) => (
            <li key={m.id} className="flex flex-wrap items-center gap-3 py-3">
              <Avatar name={m.name} id={m.id} />
              <span className="min-w-0 flex-1 basis-[8rem]"><span className="block truncate font-semibold">{m.name}</span>
                <span className="block text-[0.9375rem] text-muted">{memberLine(m, t)}</span></span>
              <span className="flex gap-1">
                {active?.id === m.id ? <span className="self-center rounded-full bg-text px-3 py-1 text-[0.875rem] font-bold text-bg">✓</span>
                  : <Button variant="secondary" onClick={() => switchProfile({ id: m.id, name: m.name, relation: m.relation }, "/me")}>{t.switchTo}</Button>}
                <button aria-label={`${t.editLabel} ${m.name}`} onClick={() => { setEditing(m); setDraft({ ...EMPTY, name: m.name, relation: m.relation }); }}
                  className="grid h-12 w-12 place-items-center rounded-[12px] text-muted hover:bg-surface-2"><Pencil aria-hidden className="h-5 w-5" /></button>
                <button aria-label={`${t.removeMember} ${m.name}`} onClick={() => remove(m)}
                  className="grid h-12 w-12 place-items-center rounded-[12px] text-danger hover:bg-danger-soft"><Trash2 aria-hidden className="h-5 w-5" /></button>
              </span>
            </li>
          ))}
        </ul>
        {members?.length === 0 && <EmptyState icon={Users} art="family" text={t.noFamily}
          action={<Button icon={Plus} onClick={() => setAdding(true)}>{t.addMember}</Button>} />}
      </Card>

      <p className="flex items-start gap-2 text-[0.9375rem] text-muted">
        <ShieldCheck aria-hidden className="mt-0.5 h-5 w-5 shrink-0 text-muted" />
        Your privacy choices (Settings → Privacy) also apply to the family members you manage.
      </p>

      <Sheet open={open} onClose={close} title={editing ? `${t.editLabel}: ${editing.name}` : t.addMember}>
        <form onSubmit={(e) => { e.preventDefault(); save(); }} className="space-y-4">
          <label className="block font-semibold">{t.memberName}
            <input className="input mt-1" required maxLength={40} autoComplete="off" value={draft.name}
                   onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <div role="group" aria-label={t.relation}>
            <p className="mb-1.5 font-semibold">{t.relation}</p>
            <div className="flex flex-wrap gap-2">{RELATIONS.map((r) => (
              <Chip key={r} selected={draft.relation === r} onClick={() => setDraft({ ...draft, relation: r })}>{t.rel?.[r] ?? r}</Chip>))}</div>
          </div>
          {!editing && (
            <>
              <label className="block font-semibold">{t.dobLabel}
                <input type="date" className="input mt-1" max={new Date().toISOString().slice(0, 10)} value={draft.dob}
                       onChange={(e) => setDraft({ ...draft, dob: e.target.value })} /></label>
              <div role="group" aria-label={t.sexLabel}>
                <p className="mb-1.5 font-semibold">{t.sexLabel}</p>
                <div className="flex flex-wrap gap-2">{(["female", "male"] as const).map((x) => (
                  <Chip key={x} selected={draft.sex === x} onClick={() => setDraft({ ...draft, sex: draft.sex === x ? "" : x })}>{t[x]}</Chip>))}</div>
              </div>
            </>
          )}
          <Button type="submit" size="lg" className="w-full" loading={busy} disabled={!draft.name.trim()}>{t.save}</Button>
        </form>
      </Sheet>
    </div>
  );
}
