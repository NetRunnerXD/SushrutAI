"use client";

import { Download, IdCard, Share2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { spiceOf, useActiveProfile } from "@/components/Family";
import { Button } from "@/components/ui/Button";
import { Card, Skeleton } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Banner } from "@/components/ui/Trust";
import { api, getProfile } from "@/lib/api";
import { bcp47, fill, useI18n } from "@/lib/i18n";
import { cardFonts, cardText, drawPatientCard, type CardData, type CardLabels, type CardStyle } from "@/lib/patientCard";

const NAME_KEY = (id: string | null) => `sushrut.cardName.${id ?? "me"}`;

/** Resolve "var(--sp-2)" to its hex value: the canvas can't read CSS variables. */
function cssColor(v: string): string {
  const m = /var\((--[\w-]+)\)/.exec(v);
  return (m && getComputedStyle(document.documentElement).getPropertyValue(m[1]).trim()) || "#C98A0A";
}

function build(s: any, ec: any, name: string, t: any, lang: string): CardData {
  const p = s.patient;
  const prof = getProfile();
  const date = (d: string | Date) => new Date(d).toLocaleDateString(bcp47(lang as any), { day: "numeric", month: "short", year: "numeric" });
  const slots = (f: any) => {
    if (!f) return "";
    if (f.per_day == null) return f.label ?? t.asNeeded;
    return [f.morning && t.morning, f.afternoon && t.afternoon, f.night && t.night].filter(Boolean).join(", ") || f.label || "";
  };
  const vit: CardData["vitals"] = [];
  const byType: Record<string, any> = Object.fromEntries(s.vitals.map((v: any) => [v.type, v]));
  if (byType.bp_sys && byType.bp_dia)
    vit.push({ label: t.cardBp, value: `${byType.bp_sys.value}/${byType.bp_dia.value} mmHg`, date: date(byType.bp_sys.at) });
  for (const v of s.vitals) {
    if (v.type === "bp_sys" || v.type === "bp_dia") continue;
    vit.push({ label: v.label, value: `${v.value} ${v.unit ?? ""}`.trim(), date: date(v.at) });
  }
  if (p.bmi) vit.push({ label: "BMI", value: `${p.bmi}`, date: p.bmi_category ?? "" });
  return {
    name: name.trim() || p.name || t.visitPatient,
    line: [p.age != null && fill(t.yearsOld, { n: p.age }), p.sex && (t[p.sex] ?? p.sex),
           prof?.relation && (t.rel?.[prof.relation] ?? prof.relation)].filter(Boolean).join(" · "),
    bloodGroup: p.blood_group ?? null,
    allergies: s.allergies,
    conditions: s.conditions,
    medicines: s.medicines.map((m: any) => ({
      name: [m.name, m.dose].filter(Boolean).join(" "),
      detail: [slots(m.frequency), m.timing && (t.timing?.[m.timing] ?? m.timing.replace(/_/g, " "))].filter(Boolean).join(" · "),
    })),
    contact: ec?.emergency_contact ?? null,
    pregnancy: p.pregnancy_status === "pregnant" ? t.cardPregnant : p.pregnancy_status === "possibly_pregnant" ? t.cardMaybePregnant : null,
    vitals: vit,
    labs: s.abnormal_labs.map((l: any) => ({
      test: l.test, value: [l.value, l.unit].filter(Boolean).join(" "), flag: l.flag === "H" ? "H" : "L",
      detail: [l.range && fill(t.cardNormal, { range: l.range }), l.at && date(l.at)].filter(Boolean).join(" · "),
    })),
    date: date(s.generated_at ?? new Date()),
    spice: cssColor(spiceOf(prof?.id ?? null)),
  };
}

export default function PatientCardPage() {
  const { t, lang } = useI18n();
  const active = useActiveProfile();
  const [style, setStyle] = useState<CardStyle>("family");
  const [name, setName] = useState("");
  const [src, setSrc] = useState<{ s: any; ec: any } | null>(null);
  const [img, setImg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [canShare, setCanShare] = useState(false);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const data = useRef<CardData | null>(null);

  useEffect(() => {
    if (new URLSearchParams(location.search).get("style") === "doctor") setStyle("doctor");
    const prof = getProfile();
    try { setName(localStorage.getItem(NAME_KEY(prof?.id ?? null)) ?? prof?.name ?? ""); } catch { setName(prof?.name ?? ""); }
    Promise.all([api<any>("/me/visit-summary?days=365"), api<any>("/me/emergency-card").catch(() => null)])
      .then(([s, ec]) => setSrc({ s, ec })).catch((e) => setErr(e.message));
    try {
      const probe = new File([new Blob(["x"], { type: "image/png" })], "x.png", { type: "image/png" });
      setCanShare(!!navigator.canShare?.({ files: [probe] }));
    } catch { setCanShare(false); }
  }, []);

  const labels = useCallback((): CardLabels => ({
    title: t.cardTitle, titleDoctor: t.cardTitleDoctor, bloodGroup: t.bloodGroup, bloodUnknown: t.cardBloodUnknown,
    allergies: t.visitAllergies, noAllergies: t.cardNoAllergies, conditions: t.visitConditions, medicines: t.visitMeds,
    contact: t.emergencyContact, emergencyCall: t.cardEmergencyCall, readings: t.visitVitals, labs: t.visitLabs,
    high: t.cardHigh, low: t.cardLow, none: t.visitNoneRecorded, more: t.cardMore, madeBy: t.cardMadeBy,
    note: t.cardNote, scan: t.cardScan,
  }), [t]);

  // draw (debounced while typing the name)
  useEffect(() => {
    if (!src) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      const d = build(src.s, src.ec, name, t, lang);
      const L = labels();
      const fonts = await cardFonts(cardText(d, L, "doctor"));
      if (cancelled) return;
      const c = canvas.current ?? (canvas.current = document.createElement("canvas"));
      drawPatientCard(c, d, style, L, fonts);
      data.current = d;
      c.toBlob((b) => {
        if (!b || cancelled) return;
        setImg((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(b); });
      }, "image/png");
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [src, name, style, t, lang, labels]);

  const onName = (v: string) => {
    setName(v);
    try { localStorage.setItem(NAME_KEY(active?.id ?? null), v); } catch { /* ignore */ }
  };

  const fileName = (ext: string) =>
    `sushrutai-card-${(data.current?.name || "patient").replace(/[^\p{L}\p{N}]+/gu, "-").toLowerCase()}${style === "doctor" ? "-doctor" : ""}.${ext}`;

  const blob = (type: string) => new Promise<Blob | null>((res) => {
    if (!canvas.current) return res(null);
    canvas.current.toBlob(res, type, 0.92);  // returns nothing; the callback resolves
  });

  const save = async (type: "image/png" | "image/jpeg") => {
    const b = await blob(type);
    if (!b) return;
    const url = URL.createObjectURL(b);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName(type === "image/png" ? "png" : "jpg");
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  };

  const share = async () => {
    const b = await blob("image/png");
    if (!b || !data.current) return;
    const file = new File([b], fileName("png"), { type: "image/png" });
    await navigator.share({ files: [file], title: t.cardTitle, text: cardText(data.current, labels(), style) }).catch(() => {});
  };

  return (
    <div className="mx-auto max-w-3xl space-y-4 desk:max-w-[1100px]">
      <div className="space-y-2">
        <h1 className="flex items-center gap-2"><IdCard aria-hidden className="h-7 w-7 text-muted" />{t.cardTitle}</h1>
        <p className="text-muted">{t.cardHelp}</p>
      </div>
      {err && <Banner tone="warning">{err}</Banner>}

      <Card className="space-y-4">
        <div role="group" aria-label={t.cardStyle} className="flex flex-wrap gap-2">
          {([["family", t.cardForFamily], ["doctor", t.cardForDoctor]] as const).map(([k, l]) => (
            <Chip key={k} selected={style === k} onClick={() => setStyle(k)}>{l}</Chip>))}
        </div>
        <div className="space-y-1">
          <label htmlFor="cardName" className="font-semibold">{t.cardName}</label>
          <input id="cardName" className="input" maxLength={60} value={name} onChange={(e) => onName(e.target.value)}
            placeholder={t.cardNamePh} autoComplete="name" />
        </div>
        <div className="flex flex-wrap gap-2">
          {canShare && <Button icon={Share2} disabled={!img} onClick={share}>{t.cardShare}</Button>}
          <Button variant={canShare ? "secondary" : "primary"} icon={Download} disabled={!img} onClick={() => save("image/png")}>{t.cardSavePng}</Button>
          <Button variant="secondary" icon={Download} disabled={!img} onClick={() => save("image/jpeg")}>{t.cardSaveJpg}</Button>
        </div>
        {!canShare && <p className="text-[0.875rem] text-muted">{t.cardShareTip}</p>}
      </Card>

      {img ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={img} alt={fill(t.cardPreviewAlt, { name: data.current?.name ?? "" })}
          className="mx-auto block w-full max-w-[540px] rounded-[20px] shadow-[var(--e2)]" />
      ) : <Skeleton className="mx-auto h-[720px] max-w-[540px]" />}
    </div>
  );
}
