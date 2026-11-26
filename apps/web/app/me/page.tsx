"use client";

import { Activity, ChevronRight, FileText, HeartPulse, IdCard, Phone, Plus, Ruler, Settings, ShieldAlert, Stethoscope, Syringe, User, Users, Venus, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useActiveProfile } from "@/components/Family";
import { LayoutToggle } from "@/components/desktop/LayoutToggle";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, PageTitle } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Sheet } from "@/components/ui/Sheet";
import { Banner } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

const COMMON_ALLERGIES = ["Penicillin", "Sulfa drugs", "Aspirin / NSAIDs", "Peanuts", "Dust", "Seafood", "Eggs", "Milk"];
const COMMON_CONDITIONS = ["Diabetes", "High blood pressure", "Asthma", "Thyroid", "Heart disease", "Kidney disease", "Arthritis"];
const STATES = [["AP", "Andhra Pradesh"], ["AS", "Assam"], ["BR", "Bihar"], ["CG", "Chhattisgarh"], ["DL", "Delhi"], ["GA", "Goa"],
  ["GJ", "Gujarat"], ["HR", "Haryana"], ["HP", "Himachal Pradesh"], ["JH", "Jharkhand"], ["KA", "Karnataka"], ["KL", "Kerala"],
  ["MP", "Madhya Pradesh"], ["MH", "Maharashtra"], ["ML", "Meghalaya"], ["MZ", "Mizoram"], ["OR", "Odisha"], ["PB", "Punjab"],
  ["RJ", "Rajasthan"], ["TN", "Tamil Nadu"], ["TG", "Telangana"], ["TR", "Tripura"], ["UP", "Uttar Pradesh"], ["UK", "Uttarakhand"],
  ["WB", "West Bengal"], ["JK", "Jammu & Kashmir"]];

type Section = "basics" | "body" | "lifestyle" | "women" | "contact";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block text-[0.9375rem] font-semibold">{label}<div className="mt-1 font-normal">{children}</div></label>;
}

function Seg({ value, options, onChange, label }: { value: any; options: [string, string][]; onChange: (v: string | null) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
      {options.map(([v, l]) => <Chip key={v} selected={value === v} onClick={() => onChange(value === v ? null : v)}>{l}</Chip>)}
    </div>
  );
}

export default function Me() {
  const { t } = useI18n();
  const who = useActiveProfile();
  const [p, setP] = useState<any>(null);
  const [draft, setDraft] = useState<any>({});
  const [allergies, setAllergies] = useState<any[]>([]);
  const [conds, setConds] = useState<any[]>([]);
  const [edit, setEdit] = useState<Section | null>(null);
  const [addA, setAddA] = useState("");
  const [addC, setAddC] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    setP(await api("/me/profile"));
    setAllergies(await api("/me/allergies"));
    setConds(await api("/me/conditions"));
  };
  useEffect(() => { load(); }, []);
  if (!p) return <div className="skeleton h-40" />;

  const open = (s: Section) => { setDraft({ ...p }); setEdit(s); };
  async function save() {
    const keys = ["dob", "sex", "blood_group", "height_cm", "state_code", "diet", "smoking_status", "tobacco_chewing", "alcohol_units_wk",
      "activity_level", "pregnancy_status", "lactating", "emergency_contact", "occupation", "sleep", "hydration", "healthcare_access", "literacy_level"];
    const body: any = {};
    for (const k of keys) if (draft[k] !== undefined && draft[k] !== "" && draft[k] !== p[k]) body[k] = draft[k];
    if (body.height_cm) body.height_cm = Number(body.height_cm);
    if (body.alcohol_units_wk) body.alcohol_units_wk = Number(body.alcohol_units_wk);
    try {
      setP(await api("/me/profile", { method: "PUT", body: JSON.stringify(body) }));
      if (draft._weight) await api("/me/vitals", { method: "POST", body: JSON.stringify([{ type: "weight", value: Number(draft._weight) }] ) });
      setEdit(null); setMsg(t.saved); load();
    } catch (e: any) { setMsg(e.message); }
  }
  const set = (k: string, v: any) => setDraft((d: any) => ({ ...d, [k]: v }));
  async function addAllergy(s: string) { if (!s.trim()) return; await api("/me/allergies", { method: "POST", body: JSON.stringify({ substance: s.trim() }) }); setAddA(""); load(); }
  async function addCond(s: string) { if (!s.trim()) return; await api("/me/conditions", { method: "POST", body: JSON.stringify({ display: s.trim() }) }); setAddC(""); load(); }

  const filled = (ks: string[]) => ks.filter((k) => p[k] != null && p[k] !== "").length;
  const sections: { key: Section; icon: any; title: string; keys: string[]; summary: string; show?: boolean }[] = [
    { key: "basics", icon: User, title: t.basics, keys: ["dob", "sex", "state_code", "healthcare_access", "literacy_level"],
      summary: [p.dob && `${new Date().getFullYear() - Number(p.dob.slice(0, 4))} yrs`, p.sex, STATES.find((s) => s[0] === p.state_code)?.[1], p.healthcare_access].filter(Boolean).join(" · ") },
    { key: "body", icon: Ruler, title: t.body, keys: ["blood_group", "height_cm"],
      summary: [p.blood_group && `${t.bloodGroup} ${p.blood_group}`, p.height_cm && `${p.height_cm} cm`, p.bmi && `BMI ${p.bmi.value} (${p.bmi.category})`].filter(Boolean).join(" · ") },
    { key: "lifestyle", icon: Activity, title: t.lifestyle, keys: ["diet", "smoking_status", "activity_level", "occupation", "sleep", "hydration"],
      summary: [p.occupation, p.diet, p.smoking_status && `smoking: ${p.smoking_status}`, p.activity_level].filter(Boolean).join(" · ") },
    { key: "women", icon: Venus, title: t.womensHealth, keys: ["pregnancy_status"], show: p.sex === "female", summary: p.pregnancy_status?.replace("_", " ") ?? "" },
    { key: "contact", icon: Phone, title: t.emergencyContact, keys: ["emergency_contact"], summary: p.emergency_contact ?? "" },
  ];

  return (
    <div className="mx-auto max-w-3xl space-y-4 desk:max-w-[1200px]">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <PageTitle sub={p.is_guest && !who ? t.guestNote : undefined}>{who ? who.name : t.profileTitle}</PageTitle>
        <div className="flex flex-wrap gap-2">
          <LayoutToggle />
          <Link href="/me/settings" className="btn btn-outline"><Settings aria-hidden className="h-5 w-5" />{t.settingsTitle}</Link>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 desk:grid-cols-4">
        {[{ href: "/me/visit", icon: FileText, title: t.visitTitle, sub: t.visitHelp },
          { href: "/me/family", icon: Users, title: t.family, sub: t.familyHelp },
          { href: "/me/care", icon: Syringe, title: t.careTitle, sub: t.careHelp },
          { href: "/me/card", icon: IdCard, title: t.cardTitle, sub: t.cardSub }].map((x) => (
          <Link key={x.href} href={x.href} className="flex min-h-20 items-center gap-3 rounded-[16px] border border-border bg-surface p-4 shadow-[var(--e1)] hover:bg-surface-2">
            <span aria-hidden className="grid h-11 w-11 shrink-0 place-items-center rounded-full well text-text"><x.icon className="h-6 w-6" /></span>
            <span className="min-w-0 flex-1"><span className="block font-semibold">{x.title}</span>
              <span className="block text-[0.875rem] text-muted">{x.sub}</span></span>
            <ChevronRight aria-hidden className="h-5 w-5 text-muted" />
          </Link>))}
      </div>
      {msg && <Banner tone={msg === t.saved ? "success" : "warning"}>{msg}</Banner>}

      <ul className="space-y-2 desk:grid desk:grid-cols-2 desk:gap-3 desk:space-y-0">
        {sections.filter((s) => s.show !== false).map((s) => (
          <li key={s.key}>
            <button onClick={() => open(s.key)} className="flex min-h-16 w-full items-center gap-3 rounded-[16px] border border-border bg-surface p-4 text-start hover:border-border-input">
              <span aria-hidden className="grid h-10 w-10 shrink-0 place-items-center rounded-full well text-text"><s.icon className="h-5 w-5" /></span>
              <span className="min-w-0 flex-1">
                <span className="block font-semibold">{s.title}</span>
                <span className="block truncate text-[0.9375rem] text-muted">{s.summary || fill(t.filled, { n: filled(s.keys), total: s.keys.length })}</span>
              </span>
              <span className="text-[0.8125rem] text-muted">{fill(t.filled, { n: filled(s.keys), total: s.keys.length })}</span>
              <ChevronRight aria-hidden className="h-5 w-5 text-muted" />
            </button>
          </li>
        ))}
      </ul>

      <Card>
        <CardHeader icon={ShieldAlert} title={t.allergiesLabel} />
        <ul className="mb-3 flex flex-wrap gap-2">
          {allergies.map((a) => (
            <li key={a.id} className="inline-flex min-h-12 items-center gap-1 rounded-full bg-danger-soft ps-3 font-semibold text-danger">{a.substance}
              <button aria-label={`Remove ${a.substance}`} onClick={async () => { await api(`/me/allergies/${a.id}`, { method: "DELETE" }); load(); }}
                className="grid h-12 w-12 place-items-center"><X aria-hidden className="h-4 w-4" /></button></li>
          ))}
        </ul>
        <div className="no-scrollbar -mx-4 mb-2 flex gap-2 overflow-x-auto px-4">
          {COMMON_ALLERGIES.filter((x) => !allergies.some((a) => a.substance === x)).map((x) => <Chip key={x} icon={Plus} onClick={() => addAllergy(x)}>{x}</Chip>)}
        </div>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); addAllergy(addA); }}>
          <label className="sr-only" htmlFor="addA">{t.allergiesLabel}</label>
          <input id="addA" className="input" value={addA} onChange={(e) => setAddA(e.target.value)} placeholder="Other allergy…" />
          <Button type="submit" variant="secondary" icon={Plus} aria-label="Add allergy" />
        </form>
      </Card>

      <Card>
        <CardHeader icon={Stethoscope} title={t.conditionsLabel} />
        <ul className="mb-3 flex flex-wrap gap-2">
          {conds.map((c) => (
            <li key={c.id} className="inline-flex min-h-12 items-center gap-1 rounded-full bg-surface-2 ps-3 font-semibold">{c.display}
              <button aria-label={`Remove ${c.display}`} onClick={async () => { await api(`/me/conditions/${c.id}`, { method: "DELETE" }); load(); }}
                className="grid h-12 w-12 place-items-center"><X aria-hidden className="h-4 w-4" /></button></li>
          ))}
        </ul>
        <div className="no-scrollbar -mx-4 mb-2 flex gap-2 overflow-x-auto px-4">
          {COMMON_CONDITIONS.filter((x) => !conds.some((c) => c.display === x)).map((x) => <Chip key={x} icon={Plus} onClick={() => addCond(x)}>{x}</Chip>)}
        </div>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); addCond(addC); }}>
          <label className="sr-only" htmlFor="addC">{t.conditionsLabel}</label>
          <input id="addC" className="input" value={addC} onChange={(e) => setAddC(e.target.value)} placeholder="Other condition…" />
          <Button type="submit" variant="secondary" icon={Plus} aria-label="Add condition" />
        </form>
      </Card>

      <Card className="border-2 border-danger">
        <CardHeader icon={HeartPulse} title={t.emergencyCard} sub={t.showToResponder} />
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt className="text-muted">{t.bloodGroup}</dt><dd className="font-semibold">{p.blood_group ?? "—"}</dd>
          <dt className="text-muted">{t.allergiesLabel}</dt><dd className="font-semibold text-danger">{allergies.map((a) => a.substance).join(", ") || t.none}</dd>
          <dt className="text-muted">{t.conditionsLabel}</dt><dd>{conds.map((c) => c.display).join(", ") || t.none}</dd>
          <dt className="text-muted">{t.emergencyContact}</dt><dd>{p.emergency_contact ?? "—"}</dd>
        </dl>
        <Link href="/me/card" className="btn btn-outline mt-3"><IdCard aria-hidden className="h-5 w-5" />{t.cardMake}</Link>
      </Card>

      <Sheet open={!!edit} onClose={() => setEdit(null)} title={sections.find((s) => s.key === edit)?.title ?? ""}>
        <div className="space-y-4">
          {edit === "basics" && (<>
            <Field label="Date of birth"><input type="date" className="input" value={draft.dob ?? ""} onChange={(e) => set("dob", e.target.value)} autoComplete="bday" /></Field>
            <Field label="Sex"><Seg label="Sex" value={draft.sex} onChange={(v) => set("sex", v)} options={[["female", "Female"], ["male", "Male"], ["intersex", "Intersex"]]} /></Field>
            <Field label="State"><select className="input" value={draft.state_code ?? ""} onChange={(e) => set("state_code", e.target.value || null)}>
              <option value="">—</option>{STATES.map(([c, n]) => <option key={c} value={c}>{n}</option>)}</select></Field>
            <Field label={t.healthcareAccessLabel}><input className="input" value={draft.healthcare_access ?? ""} onChange={(e) => set("healthcare_access", e.target.value)} placeholder="e.g. PHC 5 km away, district hospital 20 km" /></Field>
            <Field label={t.readingPrefLabel}><Seg label={t.readingPrefLabel} value={draft.literacy_level} onChange={(v) => set("literacy_level", v)} options={[["limited", "Very simple words"], ["non_reader", "Voice / very simple"], ["fluent", "Standard detail"]]} /></Field>
          </>)}
          {edit === "body" && (<>
            <Field label={t.bloodGroup}><Seg label={t.bloodGroup} value={draft.blood_group} onChange={(v) => set("blood_group", v)}
              options={["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"].map((b) => [b, b])} /></Field>
            <Field label="Height (cm)"><input className="input font-mono" inputMode="numeric" value={draft.height_cm ?? ""} onChange={(e) => set("height_cm", e.target.value.replace(/\D/g, ""))} /></Field>
            <Field label="Weight (kg)"><input className="input font-mono" inputMode="decimal" value={draft._weight ?? ""} onChange={(e) => set("_weight", e.target.value.replace(/[^\d.]/g, ""))} /></Field>
            <p className="text-[0.875rem] text-muted">BMI uses Asian-Indian cut-offs: 23 and above is overweight, 25 and above is obese.</p>
          </>)}
          {edit === "lifestyle" && (<>
            <Field label="Diet"><Seg label="Diet" value={draft.diet} onChange={(v) => set("diet", v)} options={[["veg", "Vegetarian"], ["non_veg", "Non-veg"], ["eggetarian", "Eggetarian"], ["vegan", "Vegan"], ["jain", "Jain"]]} /></Field>
            <Field label="Smoking"><Seg label="Smoking" value={draft.smoking_status} onChange={(v) => set("smoking_status", v)} options={[["never", "Never"], ["former", "Used to"], ["current", "Yes"]]} /></Field>
            <Field label="Chew tobacco (gutka / khaini)?"><Seg label="Tobacco" value={draft.tobacco_chewing === true ? "y" : draft.tobacco_chewing === false ? "n" : null}
              onChange={(v) => set("tobacco_chewing", v === "y" ? true : v === "n" ? false : null)} options={[["n", t.no], ["y", t.yes]]} /></Field>
            <Field label="Physical activity"><Seg label="Activity" value={draft.activity_level} onChange={(v) => set("activity_level", v)} options={[["sedentary", "Mostly sitting"], ["light", "Light"], ["moderate", "Moderate"], ["active", "Active"]]} /></Field>
            <Field label={t.occupationLabel}><input className="input" value={draft.occupation ?? ""} onChange={(e) => set("occupation", e.target.value)} placeholder="e.g. Farming, shopkeeper, teacher" /></Field>
            <Field label={t.sleepLabel}><input className="input" value={draft.sleep ?? ""} onChange={(e) => set("sleep", e.target.value)} placeholder="e.g. 6 hours, waking up often" /></Field>
            <Field label={t.hydrationLabel}><input className="input" value={draft.hydration ?? ""} onChange={(e) => set("hydration", e.target.value)} placeholder="e.g. Drinking less water, poor appetite" /></Field>
          </>)}
          {edit === "women" && (
            <Field label="Pregnancy"><Seg label="Pregnancy" value={draft.pregnancy_status} onChange={(v) => set("pregnancy_status", v)}
              options={[["not_pregnant", "Not pregnant"], ["pregnant", "Pregnant"], ["possibly_pregnant", "Maybe"]]} /></Field>
          )}
          {edit === "contact" && (
            <Field label={t.emergencyContact}><input className="input" value={draft.emergency_contact ?? ""} onChange={(e) => set("emergency_contact", e.target.value)}
              placeholder="Name and phone, e.g. Asha 98765 43210" autoComplete="tel" /></Field>
          )}
          <Button size="lg" className="w-full" onClick={save}>{t.save}</Button>
        </div>
      </Sheet>
    </div>
  );
}
