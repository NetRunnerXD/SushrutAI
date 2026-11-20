"use client";

import { Check, Download, MessageCircle, Pencil, Pin, PinOff, Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

export type ChatRow = { id: string; title: string; pinned: boolean; updated_at: string; count: number; urgency?: string | null };
const URG_DOT: Record<string, string> = { emergency: "bg-emergency", see_doctor_24h: "bg-danger", see_doctor_week: "bg-warn", self_care: "bg-ok" };

/** Save a chat as a plain-text file (questions and answers, with the disclaimer). */
async function exportChat(id: string, title: string, note: string) {
  const s = await api<any>(`/chat/sessions/${id}`);
  const text = [title, new Date().toLocaleString(), "",
    ...s.messages.map((m: any) => `${m.role === "user" ? "Q" : "A"}: ${m.content}\n`), note].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  a.download = `${title.slice(0, 40).replace(/[^\wऀ-ॿ -]+/g, "") || "chat"}.txt`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

/** Past chats: search, rename, pin, delete. Used as a column on wide screens and inside a sheet on phones. */
export function ChatHistory({ current, onNew, onPick, refreshKey = 0 }: {
  current: string | null; onNew: () => void; onPick?: () => void; refreshKey?: number;
}) {
  const { t, lang } = useI18n();
  const [rows, setRows] = useState<ChatRow[] | null>(null);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [confirm, setConfirm] = useState<string | null>(null);

  const load = useCallback(() => { api<ChatRow[]>("/chat/sessions").then(setRows).catch(() => setRows([])); }, []);
  useEffect(load, [load, refreshKey]);

  const patch = async (id: string, body: { title?: string; pinned?: boolean }) => {
    await api(`/chat/sessions/${id}`, { method: "PATCH", body: JSON.stringify(body) }).catch(() => {});
    load();
  };
  const remove = async (id: string) => {
    await api(`/chat/sessions/${id}`, { method: "DELETE" }).catch(() => {});
    setConfirm(null);
    if (id === current) onNew();
    load();
  };

  const shown = (rows ?? []).filter((r) => r.title.toLowerCase().includes(q.trim().toLowerCase()));
  const icon = "grid h-12 w-12 shrink-0 place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-text";

  return (
    <div className="space-y-3">
      <button type="button" onClick={onNew} className="btn btn-primary w-full">
        <Plus aria-hidden className="h-5 w-5" />{t.newChat}</button>
      <label htmlFor="chat-search" className="sr-only">{t.searchChats}</label>
      <input id="chat-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t.searchChats}
        className="input" autoComplete="off" />
      {rows === null ? <div className="skeleton h-24" /> : shown.length === 0 ? (
        <p className="px-1 text-[0.9375rem] text-muted">{rows.length ? `${t.noChatsFound}: “${q}”` : t.noChats}</p>
      ) : (
        <div className="space-y-3">
        {groups(shown, t).map(([label, list]) => (
        <section key={label} aria-label={label}>
        <h3 className="mb-1 px-2 text-[0.8125rem] font-semibold uppercase tracking-wide text-muted">{label}</h3>
        <ul className="space-y-1">
          {list.map((r) => {
            const on = r.id === current;
            if (editing === r.id) {
              return (
                <li key={r.id}>
                  <form className="flex items-center gap-1" onSubmit={(e) => { e.preventDefault(); setEditing(null); patch(r.id, { title: name }); }}>
                    <label htmlFor={`name-${r.id}`} className="sr-only">{t.chatName}</label>
                    <input id={`name-${r.id}`} autoFocus value={name} maxLength={120} onChange={(e) => setName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }} className="input min-w-0 flex-1" />
                    <button type="submit" aria-label={t.saveName} className={icon}><Check aria-hidden className="h-5 w-5" /></button>
                  </form>
                </li>
              );
            }
            if (confirm === r.id) {
              return (
                <li key={r.id} role="alert" className="space-y-2 rounded-[16px] border-[1.5px] border-danger bg-danger-soft p-3 text-danger">
                  <p className="text-[0.9375rem] font-semibold">{t.confirmDeleteChat}</p>
                  <div className="flex gap-2">
                    <button type="button" onClick={() => remove(r.id)} className="btn bg-danger text-on-tone">
                      <Trash2 aria-hidden className="h-5 w-5" />{t.deleteChat}</button>
                    <button type="button" onClick={() => setConfirm(null)} className="btn btn-outline">{t.cancel}</button>
                  </div>
                </li>
              );
            }
            return (
              <li key={r.id} className={`group flex items-center rounded-[12px] ${on ? "bg-brand-soft" : "hover:bg-surface-2"}`}>
                <Link href={`/ask?session=${r.id}`} onClick={onPick} aria-current={on ? "page" : undefined}
                  className="flex min-h-12 min-w-0 flex-1 items-center gap-2 px-2">
                  {r.pinned ? <Pin aria-hidden className="h-4 w-4 shrink-0 text-muted" />
                    : <MessageCircle aria-hidden className="h-4 w-4 shrink-0 text-muted" />}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold">{r.title}</span>
                    <span className="flex items-center gap-1.5 text-[0.8125rem] text-muted">
                      {r.urgency && <span aria-label={t.urgency?.[r.urgency] ?? r.urgency} className={`h-2 w-2 rounded-full ${URG_DOT[r.urgency] ?? ""}`} />}
                      <span className="font-mono">{new Date(r.updated_at).toLocaleDateString(lang === "hi" ? "hi-IN" : "en-IN", { day: "numeric", month: "short" })}</span>
                      · {Math.ceil(r.count / 2)} Q</span>
                  </span>
                </Link>
                {/* actions: always reachable by keyboard/touch; on wide screens they show on hover/focus */}
                <span className="flex shrink-0 lg:hidden lg:group-focus-within:flex lg:group-hover:flex">
                  <button type="button" aria-label={`${t.renameChat}: ${r.title}`} title={t.renameChat}
                    onClick={() => { setEditing(r.id); setName(r.title); }} className={icon}><Pencil aria-hidden className="h-4 w-4" /></button>
                  <button type="button" aria-label={`${r.pinned ? t.unpinChat : t.pinChat}: ${r.title}`} title={r.pinned ? t.unpinChat : t.pinChat}
                    aria-pressed={r.pinned} onClick={() => patch(r.id, { pinned: !r.pinned })} className={icon}>
                    {r.pinned ? <PinOff aria-hidden className="h-4 w-4" /> : <Pin aria-hidden className="h-4 w-4" />}</button>
                  <button type="button" aria-label={`${t.exportChat}: ${r.title}`} title={t.exportChat}
                    onClick={() => exportChat(r.id, r.title, t.disclaimerShort).catch(() => {})} className={icon}><Download aria-hidden className="h-4 w-4" /></button>
                  <button type="button" aria-label={`${t.deleteChat}: ${r.title}`} title={t.deleteChat}
                    onClick={() => setConfirm(r.id)} className={icon}><Trash2 aria-hidden className="h-4 w-4" /></button>
                </span>
              </li>
            );
          })}
        </ul>
        </section>
        ))}
        </div>
      )}
      {editing && <button type="button" onClick={() => setEditing(null)} className="sr-only"><X />{t.cancel}</button>}
    </div>
  );
}

/** Pinned, Today, This week, Earlier. */
function groups(rows: ChatRow[], t: any): [string, ChatRow[]][] {
  const now = new Date(), day = 86_400_000;
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const g: [string, ChatRow[]][] = [[t.pinned, []], [t.today, []], [t.thisWeek, []], [t.earlier, []]];
  for (const r of rows) {
    const at = +new Date(r.updated_at);
    g[r.pinned ? 0 : at >= start ? 1 : at >= start - 6 * day ? 2 : 3][1].push(r);
  }
  return g.filter(([, l]) => l.length);
}
