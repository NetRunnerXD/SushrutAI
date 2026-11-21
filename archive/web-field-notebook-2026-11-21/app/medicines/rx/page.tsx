"use client";

import { Camera, CheckCircle2, Eye, EyeOff, ImageUp, Loader2, Minus, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, PageTitle } from "@/components/ui/Card";
import { DosePictogram, Steps } from "@/components/ui/Medical";
import { Banner, DisclaimerChip } from "@/components/ui/Trust";
import { API, api, authHeaders } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { AskAbout } from "@/components/AskAbout";

const STAGE: Record<string, string> = { queued: "…", loading: "Opening photo…", ocr: "Reading text…", reading: "Reading medicines…",
  normalising: "Matching medicines…", checking: "Checking safety…" };

/** Variance of a Laplacian over a downscaled grey image — low values mean a blurry photo. */
async function blurScore(file: File): Promise<number | null> {
  if (!file.type.startsWith("image/")) return null;
  const bmp = await createImageBitmap(file);
  const w = 320, h = Math.round((bmp.height / bmp.width) * 320);
  const c = new OffscreenCanvas(w, h);
  const ctx = c.getContext("2d")!;
  ctx.drawImage(bmp, 0, 0, w, h);
  const d = ctx.getImageData(0, 0, w, h).data;
  const g = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) g[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
  let sum = 0, sum2 = 0, n = 0;
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    const lap = 4 * g[i] - g[i - 1] - g[i + 1] - g[i - w] - g[i + w];
    sum += lap; sum2 += lap * lap; n++;
  }
  return sum2 / n - (sum / n) ** 2;
}

function RxInner() {
  const { t } = useI18n();
  const params = useSearchParams();
  const startedDoc = useRef<string | null>(null);
  const router = useRouter();
  const [rx, setRx] = useState<any>(null);
  const [img, setImg] = useState<string | null>(null);
  const [stage, setStage] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [blurWarn, setBlurWarn] = useState<File | null>(null);
  const [ticks, setTicks] = useState<Record<number, boolean>>({});
  const [showImg, setShowImg] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [saving, setSaving] = useState(false);

  async function open(r: any) {
    setRx(r);
    setTicks({});
    if (r.document_id) {
      const res = await fetch(`${API}/documents/${r.document_id}/content`, { headers: authHeaders() });
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

  async function pick(f: File, force = false) {
    setErr(null);
    if (!force) {
      const score = await blurScore(f).catch(() => null);
      if (score != null && score < 60) { setBlurWarn(f); return; }
    }
    setBlurWarn(null);
    setStage("queued");
    try {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("kind", "prescription");
      const doc = await api<{ id: string }>("/documents", { method: "POST", body: fd });
      await parseDoc(doc.id);
    } catch (e: any) { setErr(e.message); setStage(null); }
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
    <div className="mx-auto max-w-5xl space-y-4 pb-24">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PageTitle>{t.rxTitle}</PageTitle>
        {rx?.id && <AskAbout refId={`rx:${rx.id}`} label={`${t.rxTitle} ${new Date(rx.created_at ?? Date.now()).toLocaleDateString()}`} />}
      </div>
      <Steps steps={[t.rxStepPhoto, t.rxStepReading, t.rxStepReview, t.rxStepDone]} current={stepIdx} />

      {!rx && !stage && (
        <Card className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-[6px] bg-brand p-5 text-center text-brand-ink">
              <Camera aria-hidden className="h-10 w-10" /><span className="text-[1.125rem] font-semibold">{t.rxUpload}</span>
              <input type="file" accept="image/*" capture="environment" className="sr-only" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
            </label>
            <label className="flex min-h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-[6px] border-2 border-dashed border-border-input p-5 text-center">
              <ImageUp aria-hidden className="h-10 w-10 text-brand" /><span className="text-[1.125rem] font-semibold">{t.rxGallery}</span>
              <span className="text-[0.875rem] text-muted">JPG · PNG · PDF</span>
              <input type="file" accept="image/*,application/pdf" className="sr-only" onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
            </label>
          </div>
          <p className="text-muted">{t.rxTips}</p>
        </Card>
      )}

      {blurWarn && (
        <Banner tone="warning" title="This photo looks blurry"
          action={<>
            <label className="btn btn-primary cursor-pointer">Retake<input type="file" accept="image/*" capture="environment" className="sr-only"
              onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} /></label>
            <Button variant="secondary" onClick={() => pick(blurWarn, true)}>Use it anyway</Button></>}>
          {t.rxTips}
        </Banner>
      )}

      {stage && (
        <Card className="flex items-center gap-3" role="status">
          <Loader2 aria-hidden className="h-6 w-6 animate-spin text-brand" />
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
                  className="mb-2 inline-flex min-h-12 items-center gap-2 rounded-[8px] bg-surface-2 px-4 font-semibold lg:hidden">
                  {showImg ? <EyeOff aria-hidden className="h-5 w-5" /> : <Eye aria-hidden className="h-5 w-5" />}{showImg ? t.hidePrescription : t.showPrescription}
                </button>
                <div className={`${showImg ? "block" : "hidden"} lg:block`}>
                  <div className="max-h-[70dvh] overflow-auto rounded-[6px] border border-border bg-surface">
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
                  <li key={i} className={`rounded-[6px] border-2 bg-surface p-4 ${review && !ticks[i] ? "border-warn bg-warn-soft" : "border-border"}`}>
                    {review && <p className="mb-2 font-semibold text-warn">⚠ {t.rxReview}</p>}
                    <div className="grid gap-2 sm:grid-cols-2">
                      <label className="text-[0.875rem] text-muted">Name
                        <input className="input mt-1 text-[1.0625rem] font-semibold text-text" value={m.name} disabled={rx.status === "confirmed"} onChange={(e) => edit(i, "name", e.target.value)} /></label>
                      <label className="text-[0.875rem] text-muted">Strength
                        <input className="input mt-1 text-text" value={m.strength ?? ""} disabled={rx.status === "confirmed"} onChange={(e) => edit(i, "strength", e.target.value)} /></label>
                    </div>
                    {m.candidates?.[0] && m.product_id && (
                      <p className="mt-2 text-[0.875rem]">Matched: <b>{m.candidates[0].name}</b>{m.candidates[0].is_janaushadhi && <span className="ml-1 rounded bg-accent px-1.5 font-semibold text-accent-ink">{t.janAushadhi}</span>}</p>
                    )}
                    <div className="mt-3"><DosePictogram frequency={m.frequency} timing={m.timing} /></div>
                    {m.duration_days && <p className="mt-2 text-[0.9375rem]">For <b>{m.duration_days} days</b></p>}
                    {review && (
                      <>
                        <p className="mt-2 text-[0.875rem] text-muted">{t.rxReadLine} “{m.raw}”</p>
                        <label className="mt-3 flex min-h-12 cursor-pointer items-center gap-3 rounded-[8px] bg-surface px-3 font-semibold">
                          <input type="checkbox" className="h-6 w-6 accent-[var(--brand)]" checked={!!ticks[i]}
                                 onChange={(e) => setTicks((x) => ({ ...x, [i]: e.target.checked }))} />{t.rxChecked}
                        </label>
                      </>
                    )}
                  </li>
                );
              })}
              {rx.tests?.length > 0 && <li className="rounded-[6px] bg-surface-2 p-3"><b>{t.tests}:</b> {rx.tests.join(", ")}</li>}
            </ol>
          </div>
          <DisclaimerChip />

          <div className="fixed inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] z-20 border-t border-border bg-surface p-3 shadow-[var(--e2)] md:bottom-0 md:left-[var(--sbw)]">
            <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-3">
              {rx.status === "confirmed" ? (
                <>
                  <p className="flex flex-1 items-center gap-2 font-semibold text-ok"><CheckCircle2 aria-hidden className="h-6 w-6" />{t.rxConfirmed}</p>
                  <Link href="/me/settings#reminders" className="btn btn-outline">{t.remindersCalendar}</Link>
                </>
              ) : (
                <>
                  {!allTicked && <p className="flex-1 text-[0.9375rem] text-warn">{t.rxNeedsTicks}</p>}
                  <Button size="lg" className="ml-auto" loading={saving} disabled={!allTicked} onClick={confirm}>{t.rxConfirm}</Button>
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
