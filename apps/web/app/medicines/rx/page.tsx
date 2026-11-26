"use client";

import { Storylines } from "@/components/answer/Storylines";
import { Camera, CheckCircle2, Eye, EyeOff, ImageUp, Loader2, Minus, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, PageTitle } from "@/components/ui/Card";
import { DosePictogram, Steps } from "@/components/ui/Medical";
import { Banner, DisclaimerChip } from "@/components/ui/Trust";
import { API, api, authFetch } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { AskAbout } from "@/components/AskAbout";
import { discardDoc, ImageCheckBanner, KindMismatchBanner, mismatch, READER_HREF, uploadDoc, type ImageIssue, type UploadResult } from "@/components/ImageCheck";

const STAGE: Record<string, string> = { queued: "…", loading: "Opening photo…", ocr: "Reading text…", reading: "Reading medicines…",
  normalising: "Matching medicines…", checking: "Checking safety…" };

function RxInner() {
  const { t } = useI18n();
  const params = useSearchParams();
  const startedDoc = useRef<string | null>(null);
  const router = useRouter();
  const [rx, setRx] = useState<any>(null);
  const [img, setImg] = useState<string | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [check, setCheck] = useState<{ u: UploadResult; issue: ImageIssue } | null>(null);  // blurry / not health: ask first
  const [wrong, setWrong] = useState<{ u: UploadResult; found: "lab_report" | "scan" | "prescription" } | null>(null);  // a lab report or X-ray here
  const [ticks, setTicks] = useState<Record<number, boolean>>({});
  const [showImg, setShowImg] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [saving, setSaving] = useState(false);

  async function open(r: any) {
    setRx(r);
    setTicks({});
    if (r.document_id) {
      const res = await authFetch(`${API}/documents/${r.document_id}/content`);
      if (res.ok) setImg(URL.createObjectURL(await res.blob()));
    }
  }

  useEffect(() => {
    const id = params.get("id");
    if (id) api(`/prescriptions/${id}`).then(open).catch((e) => setErr(e.message));
    const doc = params.get("doc"); // a photo already uploaded elsewhere (e.g. attached in chat)
    if (doc && !id && startedDoc.current !== doc) { startedDoc.current = doc; parseDoc(doc); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  async function parseDoc(documentId: string) {
    setErr(null);
    setStage("queued");
    try {
      const { job_id } = await api<{ job_id: string }>("/prescriptions/parse", { method: "POST", body: JSON.stringify({ document_id: documentId }) });
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        const j = await api<any>(`/jobs/${job_id}`);
        setStage(j.progress.at(-1));
        if (j.status === "done") { router.replace(`/medicines/rx?id=${j.result_id}`); break; }
        if (j.status === "error") throw new Error(j.error);
      }
    } catch (e: any) { setErr(e.message); } finally { setStage(null); }
  }

  async function pick(f: File) {
    setErr(null);
    setCheck(null);
    setWrong(null);
    setStage("queued");
    try {
      // the server checks the photo first (blur, darkness, size, health-related?) before the big model reads it
      const doc = await uploadDoc(f, "prescription");
      if (doc.issue) { setCheck({ u: doc, issue: doc.issue }); setStage(null); return; }
      await proceed(doc);
    } catch (e: any) { setErr(e.message); setStage(null); }
  }

  /** A lab report or an X-ray uploaded here: offer its own reader instead of reading it as a prescription. */
  async function proceed(u: UploadResult) {
    const found = mismatch("prescription", u);
    if (found) { setWrong({ u, found }); setStage(null); return; }
    await parseDoc(u.id);
  }

  function edit(i: number, k: string, v: string) {
    setRx((r: any) => ({ ...r, medications: r.medications.map((m: any, j: number) => (j === i ? { ...m, [k]: v } : m)) }));
  }

  const needs = (rx?.medications ?? []).map((m: any, i: number) => (m.needs_review ? i : -1)).filter((i: number) => i >= 0);
  const allTicked = needs.every((i: number) => ticks[i]);

  async function confirm() {
    setSaving(true);
    try {
      const meds = rx.medications.map((m: any, i: number) => (ticks[i] ? { ...m, needs_review: false, user_corrected: true } : m));
      await api(`/prescriptions/${rx.id}`, { method: "PATCH", body: JSON.stringify({ medications: meds }) });
      setRx(await api(`/prescriptions/${rx.id}/confirm`, { method: "POST" }));
    } catch (e: any) { setErr(e.message); } finally { setSaving(false); }
  }

  const stepIdx = rx?.status === "confirmed" ? 3 : rx ? 2 : stage ? 1 : 0;
  return (
    <div className="mx-auto max-w-5xl space-y-4 pb-24 desk:max-w-[1400px]">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PageTitle>{t.rxTitle}</PageTitle>
        {rx?.id && <AskAbout refId={`rx:${rx.id}`} label={`${t.rxTitle} ${new Date(rx.created_at ?? Date.now()).toLocaleDateString()}`} />}
      </div>
      <Steps steps={[t.rxStepPhoto, t.rxStepReading, t.rxStepReview, t.rxStepDone]} current={stepIdx} />

      {!rx && !stage && (
        <Card className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-[20px] bg-brand p-5 text-center text-brand-ink shadow-[inset_0_1px_0_rgb(255_255_255/18%),var(--e2)] hover:bg-brand-strong">
              <Camera aria-hidden className="h-10 w-10" /><span className="text-[1.125rem] font-semibold">{t.rxUpload}</span>
              <input type="file" accept="image/*" capture="environment" className="sr-only" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
            </label>
            <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-[20px] border-[1.5px] border-border-input bg-surface p-5 text-center shadow-[var(--rim)] hover:bg-surface-2">
              <ImageUp aria-hidden className="h-10 w-10 text-muted" /><span className="text-[1.125rem] font-semibold">{t.rxGallery}</span>
              <span className="text-[0.875rem] text-muted">JPG · PNG · PDF</span>
              <input type="file" accept="image/*,application/pdf" className="sr-only" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
            </label>
          </div>
          <p className="text-muted">{t.rxTips}</p>
        </Card>
      )}

      {check && !stage && (
        <ImageCheckBanner issue={check.issue}
          onSend={() => { const u = check.u; setCheck(null); proceed(u); }}
          onRetake={(f) => { discardDoc(check.u.id); pick(f); }} />
      )}
      {wrong && !stage && (
        <KindMismatchBanner found={wrong.found}
          onSwitch={() => router.push(READER_HREF[wrong.found](wrong.u.id))}
          onKeep={() => { const id = wrong.u.id; setWrong(null); parseDoc(id); }} />
      )}

      {stage && (
        <Card className="flex items-center gap-3" role="status">
          <Loader2 aria-hidden className="h-6 w-6 animate-spin text-muted" />
          <div><p className="font-semibold">{t.rxReading}</p><p className="text-muted">{STAGE[stage] ?? stage}</p></div>
        </Card>
      )}
      {err && <Banner tone="danger">{err}</Banner>}

      {rx && (
        <>
          <Banner tone="warning">{rx.notice}</Banner>
          {rx.alerts?.map((a: any, i: number) => <Banner key={i} tone={a.level === "high" ? "danger" : "warning"}>{a.text}</Banner>)}

          <div className="grid gap-4 lg:grid-cols-2">
            {img && (
              <div className="lg:sticky lg:top-20 lg:self-start">
                <button onClick={() => setShowImg((s) => !s)} aria-expanded={showImg}
                  className="mb-2 inline-flex min-h-12 items-center gap-2 rounded-[12px] bg-surface-2 px-4 font-semibold lg:hidden">
                  {showImg ? <EyeOff aria-hidden className="h-5 w-5" /> : <Eye aria-hidden className="h-5 w-5" />}{showImg ? t.hidePrescription : t.showPrescription}
                </button>
                <div className={`${showImg ? "block" : "hidden"} lg:block`}>
                  <div className="max-h-[70dvh] overflow-auto rounded-[16px] border border-border bg-surface">
                    <img src={img} alt="Your prescription" style={{ width: `${zoom * 100}%` }} className="max-w-none" />
                  </div>
                  <div className="mt-2 flex gap-2">
                    <button onClick={() => setZoom((z) => Math.max(1, z - 0.25))} aria-label="Zoom out" className="grid h-12 w-12 place-items-center rounded-full bg-surface-2"><Minus aria-hidden className="h-5 w-5" /></button>
                    <button onClick={() => setZoom((z) => Math.min(3, z + 0.25))} aria-label="Zoom in" className="grid h-12 w-12 place-items-center rounded-full bg-surface-2"><Plus aria-hidden className="h-5 w-5" /></button>
                  </div>
                </div>
              </div>
            )}

            <ol className="space-y-3">
              {rx.medications.map((m: any, i: number) => {
                const review = m.needs_review && rx.status !== "confirmed";
                return (
                  <li key={i} className={`rounded-[16px] border-2 bg-surface p-4 ${review && !ticks[i] ? "border-warn bg-warn-soft" : "border-border"}`}>
                    {review && <p className="mb-2 font-semibold text-warn">⚠ {t.rxReview}</p>}
                    <div className="grid gap-2 sm:grid-cols-2">
                      <label className="text-[0.875rem] text-muted">Name
                        <input className="input mt-1 text-[1.0625rem] font-semibold text-text" value={m.name} disabled={rx.status === "confirmed"} onChange={(e) => edit(i, "name", e.target.value)} /></label>
                      <label className="text-[0.875rem] text-muted">Strength
                        <input className="input mt-1 text-text" value={m.strength ?? ""} disabled={rx.status === "confirmed"} onChange={(e) => edit(i, "strength", e.target.value)} /></label>
                    </div>
                    {m.candidates?.[0] && m.product_id && (
                      <p className="mt-2 text-[0.875rem]">Matched: <b>{m.candidates[0].name}</b>{m.candidates[0].is_janaushadhi && <span className="ms-1 rounded bg-accent px-1.5 font-semibold text-accent-ink">{t.janAushadhi}</span>}</p>
                    )}
                    <div className="mt-3"><DosePictogram frequency={m.frequency} timing={m.timing} /></div>
                    {m.duration_days && <p className="mt-2 text-[0.9375rem]">For <b>{m.duration_days} days</b></p>}
                    {review && (
                      <>
                        <p className="mt-2 text-[0.875rem] text-muted">{t.rxReadLine} “{m.raw}”</p>
                        <label className="mt-3 flex min-h-12 cursor-pointer items-center gap-3 rounded-[12px] bg-surface px-3 font-semibold">
                          <input type="checkbox" className="h-6 w-6 accent-[var(--brand)]" checked={!!ticks[i]}
                                 onChange={(e) => setTicks((x) => ({ ...x, [i]: e.target.checked }))} />{t.rxChecked}
                        </label>
                      </>
                    )}
                  </li>
                );
              })}
              {rx.tests?.length > 0 && <li className="rounded-[16px] bg-surface-2 p-3"><b>{t.tests}:</b> {rx.tests.join(", ")}</li>}
            </ol>
          </div>
          {rx.status === "confirmed" && <RxStories names={rx.medications.map((m: any) => [m.name, m.strength].filter(Boolean).join(" "))}
            printed={rx.medications.map((m: any) => m.composition ?? "")} />}
          <DisclaimerChip />

          <div className="fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-20 border-t border-border bg-surface p-3 shadow-[var(--e2)] md:bottom-0 md:start-[var(--sbw)]">
            <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3">
              {rx.status === "confirmed" ? (
                <>
                  <p className="flex flex-1 items-center gap-2 font-semibold text-ok"><CheckCircle2 aria-hidden className="h-6 w-6" />{t.rxConfirmed}</p>
                  <Link href="/me/settings#reminders" className="btn btn-outline">{t.remindersCalendar}</Link>
                </>
              ) : (
                <>
                  {!allTicked && <p className="flex-1 text-[0.9375rem] text-warn">{t.rxNeedsTicks}</p>}
                  <Button size="lg" className="ms-auto" loading={saving} disabled={!allTicked} onClick={confirm}>{t.rxConfirm}</Button>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export default function Rx() {
  return <Suspense><RxInner /></Suspense>;
}

/** After saving: each medicine's salt, a cheaper same-salt option and how it went before, as short stories. */
function RxStories({ names, printed }: { names: string[]; printed: string[] }) {
  const { lang } = useI18n();
  const [items, setItems] = useState<any[] | null>(null);
  const key = names.join("|");
  useEffect(() => {
    if (!names.length) return;
    api<any[]>("/medicines/storyline", { method: "POST", body: JSON.stringify({ names: names.slice(0, 8), printed, lang }) })
      .then(setItems).catch(() => setItems([]));
  }, [key, lang]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (items === null) return <div className="skeleton h-24" />;
  return <Storylines items={items} />;
}
