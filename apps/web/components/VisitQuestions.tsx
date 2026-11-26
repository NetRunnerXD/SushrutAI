"use client";

import { Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Q = { id: string; text: string; source: string };

/** The person's own questions for the doctor: saved from chat ("Ask my doctor") or typed here. Prints as a list. */
export function VisitQuestions() {
  const { t } = useI18n();
  const [items, setItems] = useState<Q[] | null>(null);
  const [text, setText] = useState("");
  useEffect(() => { api<Q[]>("/me/visit-questions").then(setItems).catch(() => setItems([])); }, []);
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (text.trim().length < 3) return;
    setItems(await api<Q[]>("/me/visit-questions", { method: "POST", body: JSON.stringify({ text: text.trim() }) }));
    setText("");
  };
  const remove = async (id: string) => setItems(await api<Q[]>(`/me/visit-questions/${id}`, { method: "DELETE" }));

  return (
    <section className="print-avoid-break border-t border-border pt-3">
      <h2 className="mb-1.5 text-[1.0625rem] font-bold">{t.myVisitQuestions}</h2>
      {items && items.length > 0 ? (
        <ol className="list-decimal space-y-1 ps-5">
          {items.map((q) => (
            <li key={q.id}>
              <span className="flex items-start gap-2">
                <span className="min-w-0 flex-1">{q.text}</span>
                <button onClick={() => remove(q.id)} aria-label={`${t.removeMember}: ${q.text}`}
                  className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-surface-2 print:hidden"><X aria-hidden className="h-4 w-4" /></button>
              </span>
            </li>
          ))}
        </ol>
      ) : <p className="text-muted">{t.myVisitQuestionsEmpty}</p>}
      <form onSubmit={add} className="mt-2 flex gap-2 print:hidden">
        <label htmlFor="vq" className="sr-only">{t.addVisitQuestion}</label>
        <input id="vq" className="input" maxLength={300} value={text} onChange={(e) => setText(e.target.value)} placeholder={t.addVisitQuestion} />
        <button className="btn btn-outline shrink-0" disabled={text.trim().length < 3} aria-label={t.addVisitQuestion}><Plus aria-hidden className="h-5 w-5" /></button>
      </form>
    </section>
  );
}
