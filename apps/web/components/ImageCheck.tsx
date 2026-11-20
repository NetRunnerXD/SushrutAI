"use client";

import { ArrowRight, Camera, Send } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Banner } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

/** What the server's quick check (services/api/app/ocr/check.py) found in an uploaded image. */
export type ImageIssue = "blurry" | "too_dark" | "too_small" | "not_health";
export type DocKind = "prescription" | "lab_report" | "scan" | "unsure";
export type Uploaded = { id: string; check?: { issue: ImageIssue | null; kind?: DocKind | null; scan_type?: string | null } | null };
export type UploadResult = { id: string; issue: ImageIssue | null; kind: DocKind | null; scanType: string | null };

/** Upload a photo or PDF. The reply says whether it looks blurry, dark, tiny or not health-related, and what kind
 *  of upload it looks like (prescription, lab report, scan). */
export async function uploadDoc(file: File, kind: string): Promise<UploadResult> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("kind", kind);
  const d = await api<Uploaded>("/documents", { method: "POST", body: fd });
  return { id: d.id, issue: d.check?.issue ?? null, kind: d.check?.kind ?? null, scanType: d.check?.scan_type ?? null };
}

/** Uploaded in the wrong place? e.g. a lab report on the Prescriptions page. Thresholds live in the API
 *  (app/ocr/check.py, measured on real images); ECG print-outs look like documents, so they never count as a scan
 *  in a document section. */
export type Section = "prescription" | "lab" | "image";
export function mismatch(section: Section, u: UploadResult): "prescription" | "lab_report" | "scan" | null {
  const scan = u.kind === "scan" && u.scanType !== "ecg";
  if (section === "prescription") return u.kind === "lab_report" ? "lab_report" : scan ? "scan" : null;
  if (section === "lab") return u.kind === "prescription" ? "prescription" : scan ? "scan" : null;
  return u.kind === "lab_report" || u.kind === "prescription" ? u.kind : null;
}

/** Where each kind of upload is read. */
export const READER_HREF: Record<"prescription" | "lab_report" | "scan", (id: string) => string> = {
  prescription: (id) => `/medicines/rx?doc=${id}`,
  lab_report: (id) => `/records?tab=labs&doc=${id}`,
  scan: (id) => `/records?tab=images&doc=${id}`,
};

/** "This looks like a lab report — Read it as a lab report / Continue here". */
export function KindMismatchBanner({ found, onSwitch, onKeep }: {
  found: "prescription" | "lab_report" | "scan"; onSwitch: () => void; onKeep: () => void;
}) {
  const { t } = useI18n();
  const copy = {
    prescription: [t.kindLooksRx, t.kindReadAsRx], lab_report: [t.kindLooksLab, t.kindReadAsLab],
    scan: [t.kindLooksScan, t.kindReadAsScan],
  }[found];
  return (
    <Banner tone="info" title={copy[0]} action={<>
      <Button icon={ArrowRight} onClick={onSwitch}>{copy[1]}</Button>
      <Button variant="secondary" onClick={onKeep}>{t.kindContinueHere}</Button>
    </>}>{t.kindHelp}</Banner>
  );
}

/** Forget an upload the person chose to retake (it is their health data; don't keep a bad copy). */
export function discardDoc(id: string) {
  api(`/documents/${id}`, { method: "DELETE" }).catch(() => { /* already gone, or already in their records */ });
}

/** "This photo looks blurry — Retake / Send anyway". Retake opens the camera (or file picker) again. */
export function ImageCheckBanner({ issue, onSend, onRetake, accept = "image/*,application/pdf" }: {
  issue: ImageIssue; onSend: () => void; onRetake: (f: File) => void; accept?: string;
}) {
  const { t } = useI18n();
  const copy: Record<ImageIssue, [string, string]> = {
    blurry: [t.imgBlurryTitle, t.imgBlurryHelp],
    too_dark: [t.imgDarkTitle, t.imgDarkHelp],
    too_small: [t.imgSmallTitle, t.imgSmallHelp],
    not_health: [t.imgNotHealthTitle, t.imgNotHealthHelp],
  };
  const [title, help] = copy[issue];
  return (
    <Banner tone="warning" title={title} action={<>
      <label className="btn btn-primary cursor-pointer">
        <Camera aria-hidden className="h-5 w-5" />{issue === "not_health" ? t.imgChooseOther : t.imgRetake}
        <input type="file" accept={accept} capture={issue === "not_health" ? undefined : "environment"} className="sr-only"
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) onRetake(f); }} />
      </label>
      <Button variant="secondary" icon={Send} onClick={onSend}>{t.imgSendAnyway}</Button>
    </>}>{help}</Banner>
  );
}
