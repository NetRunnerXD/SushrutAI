"use client";

import {
  ArrowDown, Check, Copy, Headphones, History, Paperclip, PanelLeftClose, PanelLeftOpen, Pencil, Plus, RotateCcw,
  SendHorizonal, Square, Stethoscope, X,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { AnswerCard, RedFlagBanner, summaryOf } from "@/components/AnswerCard";
import { ChatHistory } from "@/components/ChatHistory";
import { ContextPanel } from "@/components/ContextPanel";
import { Sheet } from "@/components/ui/Sheet";
import { useSOS } from "@/components/Emergency";
import { PageTitle } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Banner } from "@/components/ui/Trust";
import { ActionCard, HeardConfirm, InkBlot, VoiceError, VoiceOrb, Waveform, useVoiceCapture, useVoiceCommands } from "@/components/Voice";
import { api, postSSE } from "@/lib/api";
import { bcp47, useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";
import { SYMPTOM_CHIPS } from "@/lib/symptoms";
import { speak, startHandsFree, stopSpeaking, type HandsFree } from "@/lib/voice";

type Turn = { role: "user" | "assistant"; text: string; answer?: any; redFlag?: any; error?: string; photos?: string[];
              stopped?: boolean };
type Attached = { id: string; name: string; preview?: string };
const MAX_FILES = 3;
const THINKING_KEYS = ["checkingSources", "thinking"] as const;

function AskInner() {
  const { t, lang } = useI18n();
  const params = useSearchParams();
  const router = useRouter();
  const { prefs, setPrefs } = usePrefs();
  const { openSOS } = useSOS();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  // "Ask about this": a record attached from another tab (?ref=lab:<id>&about=HbA1c 9.5 %)
  const [about, setAbout] = useState<{ ref: string; label: string } | null>(null);
  useEffect(() => {
    const ref = params.get("ref");
    if (ref) setAbout({ ref, label: params.get("about") ?? ref });
  }, [params]);
  const [busy, setBusy] = useState(false);
  const [thinkIdx, setThinkIdx] = useState(0);
  const [files, setFiles] = useState<Attached[]>([]);
  const [draft, setDraft] = useState<string | null>(null);   // reply text while the model is still writing
  const abort = useRef<AbortController | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);     // phones/tablets: chats in a sheet
  const [listKey, setListKey] = useState(0);                 // bump to refresh the chat list
  const [copied, setCopied] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [attachErr, setAttachErr] = useState<string | null>(null);
  const [stage, setStage] = useState<string>("thinking");
  const [handsFree, setHandsFree] = useState(false);
  const [hfState, setHfState] = useState<"idle" | "speech" | "transcribing">("idle");
  const hf = useRef<HandsFree | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const sendRef = useRef<(m: string, spoken?: boolean) => void>(() => {});

  // session: resume ?session=… or start a new one. Changing language keeps the chat: each message carries its
  // language, so the next reply simply comes in the new one.
  const langRef = useRef(lang);
  langRef.current = lang;
  const startNew = useCallback(() => {
    abort.current?.abort();
    setTurns([]); setDraft(null); setFiles([]); setText("");
    api<{ id: string }>("/chat/sessions", { method: "POST", body: JSON.stringify({ lang: langRef.current }) })
      .then((s) => setSessionId(s.id)).catch(() => {});
  }, []);
  const resumeId = params.get("session");
  useEffect(() => {
    if (resumeId) {
      api<any>(`/chat/sessions/${resumeId}`).then((s) => {
        setSessionId(s.id);
        setTurns(s.messages.map((m: any) => m.role === "user" ? { role: "user", text: m.content }
          : { role: "assistant", text: m.content, answer: { reply: m.content, structured: m.structured, citations: m.citations } }));
      }).catch(() => startNew());
      return;
    }
    startNew();
  }, [resumeId, startNew]);
  const newChat = () => { setHistoryOpen(false); if (resumeId) router.push("/ask"); else startNew(); };

  // Follow new messages only when the reader is already near the end; otherwise offer a "new reply" button.
  const nearEnd = useRef(true);
  const [newReply, setNewReply] = useState(false);
  useEffect(() => {
    const onScroll = () => {
      nearEnd.current = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 200;
      if (nearEnd.current) setNewReply(false);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  const toEnd = () => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); setNewReply(false); };
  useEffect(() => {
    if (!turns.length) return;
    const last = turns[turns.length - 1];
    if (nearEnd.current || last?.role === "user") toEnd();
    else setNewReply(true);
  }, [turns, busy]);
  useEffect(() => { if (draft && nearEnd.current) bottom.current?.scrollIntoView({ block: "end" }); }, [draft]);
  // the message box grows with its text, up to about five lines
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight + 2, 168)}px`;
  }, [text]);
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => setThinkIdx((i) => (i + 1) % THINKING_KEYS.length), 3000);
    return () => clearInterval(id);
  }, [busy]);

  // prior: the conversation to send with (for Retry / Answer again / Edit, the turns before the replaced question)
  const send = useCallback(async (msg: string, spoken = false, prior?: Turn[], withFiles: Attached[] = files) => {
    if ((!msg.trim() && !withFiles.length) || !sessionId || busy || uploading) return;
    const base = prior ?? turns;
    const history = base.filter((x) => !x.error && !x.stopped)
      .map((x) => ({ role: x.role, content: x.role === "user" ? x.text : x.answer?.reply ?? "" }));
    const photos = withFiles.map((f) => f.preview).filter((x): x is string => !!x);
    setTurns([...base, { role: "user", text: msg.trim() || "📎 " + withFiles.map((f) => f.name).join(", "), photos }]);
    setStage(withFiles.length ? "readingAttachment" : "thinking");
    setText("");
    setFiles([]);
    setBusy(true);
    setDraft(null);
    const ctrl = new AbortController();
    abort.current = ctrl;
    let redFlag: any = null;
    let written = "";
    try {
      await postSSE(`/chat/sessions/${sessionId}/messages`, { text: msg, lang, history,
        image_document_ids: withFiles.map((f) => f.id), context_refs: about ? [about.ref] : [] },
        (ev, data) => {
          if (ev === "status" && data.stage === "thinking") setStage("thinking");
          if (ev === "delta") { written = data.text; setDraft(data.text); }
          if (ev === "red_flag") {
            redFlag = data;
            if (data.kind === "emergency" || data.kind === "mental_health") openSOS();
          }
          if (ev === "answer") {
            setDraft(null);
            setTurns((p) => [...p, { role: "assistant", text: data.reply, answer: data, redFlag }]);
            if (spoken) speak(summaryOf(data.reply), lang, bcp47(lang)); // summary first; full answer on request
          }
          if (ev === "error") setTurns((p) => [...p, { role: "assistant", text: "", error: t.thinking.replace("…", "") + " — " + (data.hint ?? data.message) }]);
        }, ctrl.signal);
    } catch (e: any) {
      if (e?.name === "AbortError") {
        // Stopped by the person: keep what was written, clearly marked as incomplete
        if (written) setTurns((p) => [...p, { role: "assistant", text: written, answer: { reply: written }, stopped: true }]);
      } else {
        setTurns((p) => [...p, { role: "assistant", text: "", error: e.message }]);
      }
    } finally { setBusy(false); setDraft(null); abort.current = null; setListKey((k) => k + 1); }
  }, [sessionId, busy, uploading, turns, lang, files, about, openSOS, t]);
  const stop = () => abort.current?.abort();
  useEffect(() => () => abort.current?.abort(), []);

  // Retry / Answer again: ask the last question again, replacing the answer (or error) after it
  const lastUser = (() => { for (let i = turns.length - 1; i >= 0; i--) if (turns[i].role === "user") return i; return -1; })();
  const askAgain = () => {
    if (lastUser < 0) return;
    send(turns[lastUser].text, false, turns.slice(0, lastUser), []);
  };
  // Edit: put the last question back in the box and remove it (and its answer) from the conversation
  const editLast = () => {
    if (lastUser < 0) return;
    setText(turns[lastUser].text.startsWith("📎") ? "" : turns[lastUser].text);
    setTurns(turns.slice(0, lastUser));
    box.current?.focus();
  };
  const copyAnswer = async (s: string) => {
    try { await navigator.clipboard.writeText(s); setCopied(true); setTimeout(() => setCopied(false), 1800); } catch { /* ignore */ }
  };
  sendRef.current = send;

  // question handed over from Home / floating mic
  useEffect(() => {
    if (!sessionId) return;
    try {
      const pending = sessionStorage.getItem("sushrut.pendingAsk");
      if (pending) { sessionStorage.removeItem("sushrut.pendingAsk"); sendRef.current(pending, true); }
    } catch { /* ignore */ }
  }, [sessionId]);

  const voice = useVoiceCapture((said) => send(said, true));
  const commands = useVoiceCommands();
  const actionsRef = useRef(voice.actions);
  actionsRef.current = voice.actions;

  async function toggleHandsFree() {
    if (handsFree) { hf.current?.stop(); hf.current = null; setHandsFree(false); setHfState("idle"); return; }
    try {
      hf.current = await startHandsFree(lang, { onUtterance: async (s) => {
          if (commands.run(s)) return;
          if (await actionsRef.current.handle(s)) return; // "BP 130 over 85", "did I take my tablets?"
          sendRef.current(s, true);
        }, onState: setHfState,
        onError: () => setHfState("idle") });
      setHandsFree(true);
    } catch { /* mic blocked: VoiceOrb shows guidance */ }
  }
  useEffect(() => () => hf.current?.stop(), []);

  async function attach(list: File[]) {
    setAttachErr(null);
    const ok = list.filter((f) => f.type.startsWith("image/") || f.type === "application/pdf");
    const room = MAX_FILES - files.length;
    if (ok.length > room) setAttachErr(t.maxAttachments);
    if (room <= 0 || !ok.length) return;
    setUploading(true);
    try {
      for (const f of ok.slice(0, room)) {
        const fd = new FormData();
        fd.append("file", f);
        fd.append("kind", "other");
        const d = await api<{ id: string }>("/documents", { method: "POST", body: fd });
        setFiles((cur) => [...cur, { id: d.id, name: f.name || "photo", preview: f.type.startsWith("image/") ? URL.createObjectURL(f) : undefined }]);
      }
    } catch (e: any) {
      setAttachErr(`${t.attachFailed}: ${e.message}`);
    } finally {
      setUploading(false);
    }
  }
  const unattach = (id: string) => setFiles((cur) => {
    const f = cur.find((x) => x.id === id);
    if (f?.preview) URL.revokeObjectURL(f.preview);
    return cur.filter((x) => x.id !== id);
  });

  return (
    <div className={`mx-auto max-w-3xl lg:grid lg:max-w-none lg:gap-6 ${prefs.chatList
        ? "lg:grid-cols-[250px_minmax(0,1fr)] 2xl:grid-cols-[260px_minmax(0,1fr)_280px]" : "lg:grid-cols-[minmax(0,1fr)] xl:grid-cols-[minmax(0,1fr)_280px]"}`}>
      {prefs.chatList && (
        <aside aria-label={t.chatHistory} className="hidden lg:block">
          <div className="sticky top-20 max-h-[calc(100dvh-6rem)] overflow-y-auto pr-1">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="text-[1.125rem]">{t.chatHistory}</h2>
              <button type="button" onClick={() => setPrefs({ chatList: false })} aria-label={t.hideChats} title={t.hideChats}
                className="grid h-12 w-12 place-items-center rounded-full text-muted hover:bg-surface-2 hover:text-text">
                <PanelLeftClose aria-hidden className="h-5 w-5" /></button>
            </div>
            <ChatHistory current={sessionId} onNew={newChat} refreshKey={listKey} />
          </div>
        </aside>
      )}
      <div className="min-w-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PageTitle>{t.askTitle}</PageTitle>
        <div className="flex flex-wrap gap-2">
          {/* chats: a sheet on narrow screens; a column on wide ones (re-open it here when hidden) */}
          <button type="button" onClick={() => setHistoryOpen(true)}
            className={`inline-flex min-h-12 items-center gap-2 rounded-[10px] border-[1.5px] border-border-input bg-surface px-3 font-semibold hover:bg-accent-soft ${prefs.chatList ? "lg:hidden" : ""}`}
            aria-haspopup="dialog">
            <History aria-hidden className="h-5 w-5" /><span className="max-sm:sr-only">{t.chatHistory}</span></button>
          {!prefs.chatList && (
            <button type="button" onClick={() => setPrefs({ chatList: true })} aria-label={t.showChats} title={t.showChats}
              className="hidden h-12 w-12 place-items-center rounded-[10px] border-[1.5px] border-border-input bg-surface hover:bg-accent-soft lg:grid">
              <PanelLeftOpen aria-hidden className="h-5 w-5" /></button>
          )}
          {turns.length > 0 && (
            <button type="button" onClick={newChat}
              className="inline-flex min-h-12 items-center gap-2 rounded-[10px] border-[1.5px] border-border-input bg-surface px-3 font-semibold hover:bg-accent-soft">
              <Plus aria-hidden className="h-5 w-5" /><span className="max-sm:sr-only">{t.newChat}</span></button>
          )}
          <Link href="/ask/check" className="inline-flex min-h-12 items-center gap-2 rounded-[10px] border-[1.5px] border-brand px-4 font-semibold text-brand hover:bg-brand-soft">
            <Stethoscope aria-hidden className="h-5 w-5" />{t.checkSymptoms}</Link>
        </div>
      </div>
      <Sheet open={historyOpen} onClose={() => setHistoryOpen(false)} title={t.chatHistory} closeLabel={t.close}>
        {historyOpen && <ChatHistory current={sessionId} onNew={newChat} onPick={() => setHistoryOpen(false)} refreshKey={listKey} />}
      </Sheet>

      {turns.length === 0 && (
        <section className="note space-y-5 rounded-[6px] border border-border bg-surface p-5 text-center shadow-[var(--e1)]">
          <VoiceOrb onText={(s) => send(s, true)} />
          <div className="text-left">
            <p className="mb-2 font-semibold">{t.exampleQuestions}</p>
            <ul className="grid gap-2 sm:grid-cols-2">
              {t.examples.map((q) => (
                <li key={q}><button onClick={() => send(q)} className="serif min-h-12 w-full rounded-[6px] border border-dashed border-border-input px-3 py-2 text-left hover:border-solid hover:bg-accent-soft">“{q}”</button></li>
              ))}
            </ul>
          </div>
          <div className="no-scrollbar -mx-5 flex gap-2 overflow-x-auto px-5 md:mx-0 md:flex-wrap md:px-0">
            {SYMPTOM_CHIPS.slice(0, 5).map((c) => (
              <Chip key={c.key} icon={c.icon} onClick={() => send(t.symptoms[c.key])}>{t.symptoms[c.key]}</Chip>
            ))}
          </div>
        </section>
      )}

      <section aria-live="polite" aria-label="Conversation" className="mt-4 space-y-4">
        {turns.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="ml-auto flex max-w-[85%] flex-col items-end">
              <div className="w-fit max-w-full space-y-2 whitespace-pre-wrap break-words rounded-[10px] rounded-br-[2px] bg-brand-soft px-4 py-2.5 text-text">
                {!!m.photos?.length && (
                  <div className="flex flex-wrap gap-2">{m.photos.map((p) => <img key={p} src={p} alt="" className="max-h-40 rounded-[8px] object-cover" />)}</div>
                )}{m.text}</div>
              {i === lastUser && !busy && (
                <div className="mt-1 flex justify-end">
                  <button type="button" onClick={editLast} className="inline-flex min-h-12 items-center gap-1.5 rounded-full px-3 text-[0.9375rem] font-semibold text-muted hover:bg-surface-2 hover:text-text">
                    <Pencil aria-hidden className="h-4 w-4" />{t.editQuestion}</button>
                </div>
              )}
            </div>
          ) : m.error ? (
            <div key={i}>
              <Banner tone="warning" action={i === turns.length - 1 && !busy ? (
                <button type="button" onClick={askAgain} className="btn btn-outline"><RotateCcw aria-hidden className="h-5 w-5" />{t.retry}</button>
              ) : undefined}>{m.error}</Banner>
            </div>
          ) : (
            <div key={i} className="note space-y-3 rounded-[6px] border border-border bg-surface p-4 shadow-[var(--e1)] sm:p-5">
              {m.redFlag && <RedFlagBanner rf={m.redFlag} />}
              {m.stopped ? (
                <>
                  <p className="whitespace-pre-wrap text-[1.0625rem] leading-relaxed text-muted">{m.text}</p>
                  <Banner tone="warning">{t.answerStopped}</Banner>
                </>
              ) : m.answer?.structured || !m.redFlag?.triggered ? <AnswerCard a={m.answer} onFollowUp={(q) => setText(q + " ")} question={turns[i - 1]?.role === "user" ? turns[i - 1].text : undefined} /> : null}
              {i === turns.length - 1 && !busy && (
                <div className="flex flex-wrap gap-2 border-t border-dashed border-border pt-3">
                  <button type="button" onClick={() => copyAnswer(m.text)} aria-live="polite"
                    className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.9375rem] font-semibold hover:bg-accent-soft">
                    {copied ? <Check aria-hidden className="h-4 w-4 text-ok" /> : <Copy aria-hidden className="h-4 w-4" />}{copied ? t.copiedAnswer : t.copyAnswer}</button>
                  {lastUser >= 0 && (
                    <button type="button" onClick={askAgain}
                      className="inline-flex min-h-12 items-center gap-1.5 rounded-full bg-surface-2 px-3 text-[0.9375rem] font-semibold hover:bg-accent-soft">
                      <RotateCcw aria-hidden className="h-4 w-4" />{t.regenerate}</button>
                  )}
                </div>
              )}
            </div>
          ),
        )}
        {busy && (draft ? (
          <div className="space-y-2 rounded-[6px] border border-border bg-surface p-4 shadow-[var(--e1)] sm:p-5">
            <p className="flex items-center gap-2 text-[0.875rem] font-semibold text-muted">
              <svg aria-hidden viewBox="0 0 48 16" className="h-4 w-10 shrink-0"><path pathLength={1} className="ink-write" d="M2 10c4-7 7-7 9 0s5 7 8 0 6-7 9 0 5 7 8 0 6-6 10-2" fill="none" stroke="var(--brand)" strokeWidth="2.4" strokeLinecap="round" /></svg>
              {t.writingDraft}</p>
            {/* the draft is not announced word by word; the finished answer is (aria-live on the conversation) */}
            <p aria-hidden className="whitespace-pre-wrap text-[1.0625rem] leading-relaxed">{draft}<span className="ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-[3px] animate-pulse bg-brand" /></p>
          </div>
        ) : (
          <div role="status" className="flex items-center gap-3 rounded-[6px] border border-dashed border-border-input bg-surface/70 p-4">
            <svg aria-hidden viewBox="0 0 48 16" className="h-4 w-12 shrink-0"><path pathLength={1} className="ink-write" d="M2 10c4-7 7-7 9 0s5 7 8 0 6-7 9 0 5 7 8 0 6-6 10-2" fill="none" stroke="var(--brand)" strokeWidth="2.4" strokeLinecap="round" /></svg>{stage === "readingAttachment" ? t.readingAttachment : t[THINKING_KEYS[thinkIdx]]}
          </div>
        ))}
        <div ref={bottom} />
      </section>

      {/* Composer */}
      <form className="composer sticky bottom-[calc(5rem+env(safe-area-inset-bottom))] z-10 mt-4 md:bottom-4" onSubmit={(e) => { e.preventDefault(); send(text); }}
        onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDragging(true); } }}
        onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }}
        onDrop={(e) => { e.preventDefault(); setDragging(false); attach(Array.from(e.dataTransfer.files)); }}>
        {newReply && (
          <div className="pointer-events-none mb-2 flex justify-center">
            <button type="button" onClick={toEnd}
              className="pointer-events-auto inline-flex min-h-12 items-center gap-1.5 rounded-full bg-brand px-4 font-semibold text-brand-ink shadow-[var(--e2)]">
              <ArrowDown aria-hidden className="h-5 w-5" />{t.newReply}</button>
          </div>
        )}
        <div className={`relative rounded-[10px] border bg-surface p-2 shadow-[var(--e2)] ${dragging ? "border-2 border-dashed border-brand" : "border-border-input/60"}`}>
          {dragging && (
            <p aria-hidden className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-[10px] bg-surface/90 font-semibold text-brand">{t.dropToAttach}</p>
          )}
          {(files.length > 0 || uploading) && (
            <ul className="mb-2 flex flex-wrap gap-2 px-1">
              {files.map((f) => (
                <li key={f.id} className="flex max-w-full items-center gap-2 rounded-[8px] bg-surface-2 py-1 pl-1 pr-0">
                  {f.preview ? <img src={f.preview} alt="" className="h-11 w-11 rounded-[6px] object-cover" />
                    : <span aria-hidden className="grid h-11 w-11 place-items-center rounded-[6px] bg-surface"><Paperclip className="h-5 w-5" /></span>}
                  <span className="max-w-[9rem] truncate text-[0.875rem]">{f.name}</span>
                  <button type="button" aria-label={`${t.removeAttachment}: ${f.name}`} onClick={() => unattach(f.id)}
                    className="grid h-12 w-12 place-items-center rounded-full hover:bg-surface"><X aria-hidden className="h-4 w-4" /></button>
                </li>
              ))}
              {uploading && <li className="flex min-h-12 items-center px-2 text-[0.9375rem] text-muted">{t.uploading}</li>}
            </ul>
          )}
          {about && (
            <div className="mb-2 space-y-2 px-1">
              <p className="flex items-center gap-2 rounded-full bg-info-soft py-1 pl-3 pr-1 text-[0.9375rem] font-semibold text-info">
                <span className="min-w-0 flex-1 truncate">{t.askingAbout}: {about.label}</span>
                <button type="button" aria-label={t.removeAttachment} onClick={() => setAbout(null)}
                  className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-surface"><X aria-hidden className="h-4 w-4" /></button></p>
              {turns.length === 0 && (
                <div className="flex flex-wrap gap-2">
                  {[t.qExplainThis, t.qIsThisNormal, t.qWhatShouldIDo].map((q) => (
                    <button key={q} type="button" onClick={() => send(q)}
                      className="min-h-12 rounded-full border-[1.5px] border-border-input bg-surface px-3 font-semibold">{q}</button>))}
                </div>
              )}
            </div>
          )}
          {attachErr && <p role="alert" className="mb-1 px-2 text-[0.9375rem] font-semibold text-danger">{attachErr}</p>}
          <ActionCard a={voice.actions} className="mb-2" />
          <HeardConfirm v={voice} className="mb-2" />
          {voice.state === "listening" && (
            <div className="flex items-center gap-2 px-2 pb-1"><Waveform levels={voice.levels} className="w-28" />
              <span aria-live="polite" className="text-[0.9375rem] text-muted">{voice.interim || t.listening}</span></div>)}
          {(voice.notice || commands.notice) && <p aria-live="polite" className="px-2 pb-1 text-[0.9375rem] font-semibold text-brand">{voice.notice || commands.notice}</p>}
          {voice.error && <div className="flex justify-center pb-1"><VoiceError v={voice} /></div>}
          <div className="flex flex-wrap items-end gap-1.5 sm:flex-nowrap">
            <button type="button" onClick={voice.toggle} aria-label={voice.state === "listening" ? t.listening : t.speak}
              aria-pressed={voice.state === "listening"} className="shrink-0 rounded-full">
              <InkBlot state={voice.state} className="h-12 w-12">
                {voice.state === "listening" ? <span className="h-4 w-4 rounded-sm bg-current" /> : <MicIcon />}
              </InkBlot>
            </button>
            <button type="button" onClick={toggleHandsFree} aria-pressed={handsFree}
              aria-label={handsFree ? `${t.handsFreeOn}` : t.handsFree} title={t.handsFree}
              className={`grid h-12 w-12 shrink-0 place-items-center rounded-full ${handsFree ? "bg-brand-soft text-brand" : "bg-surface-2"}`}>
              <Headphones aria-hidden className="h-5 w-5" /></button>
            <label className={`grid h-12 w-12 shrink-0 cursor-pointer place-items-center rounded-full bg-surface-2 ${files.length >= MAX_FILES ? "opacity-50" : ""}`} title={t.attachImage}>
              <Paperclip aria-hidden className="h-5 w-5" /><span className="sr-only">{t.attachImage}</span>
              <input type="file" multiple accept="image/*,application/pdf" className="sr-only" disabled={files.length >= MAX_FILES}
                onChange={(e) => { const l = Array.from(e.target.files ?? []); e.target.value = ""; if (l.length) attach(l); }} />
            </label>
            <label htmlFor="ask-text" className="sr-only">{t.askPlaceholder}</label>
            <textarea id="ask-text" ref={box} rows={1} value={text} onChange={(e) => setText(e.target.value)} placeholder={t.askPlaceholder}
              maxLength={4000} className="input order-first min-h-12 basis-full resize-none sm:order-none sm:basis-0 sm:flex-1"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
              onPaste={(e) => { const l = Array.from(e.clipboardData.files); if (l.length) { e.preventDefault(); attach(l); } }} />
            <span aria-hidden className="flex-1 sm:hidden" />
            {busy ? (
              <button type="button" onClick={stop} aria-label={t.stopAnswer} title={t.stopAnswer}
                className="grid h-12 w-12 shrink-0 place-items-center rounded-full border-2 border-brand bg-surface text-brand">
                <Square aria-hidden className="h-4 w-4 fill-current" /></button>
            ) : (
              <button type="submit" disabled={uploading || (!text.trim() && !files.length)} aria-label={t.send}
                className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-brand text-brand-ink disabled:opacity-50">
                <SendHorizonal aria-hidden className="h-5 w-5" /></button>
            )}
          </div>
          {handsFree && <p className="px-2 pt-1 text-[0.875rem] font-semibold text-brand">● {t.handsFreeOn}{hfState === "speech" ? ` — ${t.listening}` : hfState === "transcribing" ? ` — ${t.transcribing}` : ""}
            <button type="button" onClick={stopSpeaking} className="ml-2 underline">{t.stop}</button></p>}
        </div>
      </form>
      </div>
      {/* the profile/sources panel needs room: with the chat column open it appears only on very wide screens */}
      <div className={prefs.chatList ? "hidden 2xl:block" : "hidden xl:block"}>
        <ContextPanel sources={[...turns].reverse().find((x) => x.answer?.citations?.length)?.answer?.citations} />
      </div>
    </div>
  );
}

function MicIcon() {
  return <svg aria-hidden viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4" /></svg>;
}

export default function Ask() {
  return <Suspense><AskInner /></Suspense>;
}
