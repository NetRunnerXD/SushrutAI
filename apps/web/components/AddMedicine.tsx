"use client";

import { Plus } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Banner } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

type Warning = { kind: "allergy" | "interaction"; level: "high" | "medium"; allergy?: string; with?: string; severity?: string; generics: string[] };
const SLOTS = ["morning", "afternoon", "night"] as const;
const TIMINGS = ["before_food", "after_food", "any"] as const;

/** Add a medicine by hand. The server checks it against allergies and current medicines; warnings show at once. */
export function AddMedicine({ onAdded }: { onAdded: () => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [slots, setSlots] = useState<Set<string>>(new Set(["morning"]));
  const [timing, setTiming] = useState<string>("after_food");
  const [days, setDays] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<{ name: string; items: Warning[] } | null>(null);

  const toggle = (s: string) => setSlots((cur) => { const n = new Set(cur); n.has(s) ? n.delete(s) : n.add(s); return n; });

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true); setErr(null);
    const today = new Date();
    const n = parseInt(days, 10);
    const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const end = n > 0 ? new Date(today.getFullYear(), today.getMonth(), today.getDate() + n - 1) : null;
    const frequency = slots.size
      ? { morning: +slots.has("morning"), afternoon: +slots.has("afternoon"), night: +slots.has("night"), per_day: slots.size }
      : { per_day: null, label: "only when needed" };
    try {
      const r = await api<{ warnings: Warning[] }>("/me/medications", { method: "POST", body: JSON.stringify({
        name: name.trim(), frequency, timing: timing === "any" ? null : timing, start_date: iso(today), end_date: end ? iso(end) : null }) });
      setWarnings({ name: name.trim(), items: r.warnings ?? [] });
      setName(""); setDays(""); setOpen(false);
      window.dispatchEvent(new Event("sushrut:data-changed"));
      onAdded();
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <div className="space-y-3">
      {warnings && warnings.items.map((w, i) => (
        <Banner key={i} tone={w.level === "high" ? "danger" : "warning"}
          title={w.kind === "allergy" ? fill(t.addMedAllergy, { name: warnings.name, allergy: w.allergy ?? "" })
                                       : fill(t.addMedInteraction, { name: warnings.name, other: w.with ?? "", severity: w.severity ?? "" })}>
          <p>{t.addMedTalk}</p>
          <p className="text-[0.875rem] text-muted">{w.generics.join(" + ")}{w.kind === "interaction" ? " · DDInter" : ""}</p>
        </Banner>
      ))}
      {warnings && warnings.items.length === 0 && <Banner tone="success" title={fill(t.addMedOk, { name: warnings.name })} />}

      {!open ? (
        <Button variant="secondary" icon={Plus} onClick={() => { setOpen(true); setWarnings(null); }}>{t.addMedicine}</Button>
      ) : (
        <Card>
          <CardHeader title={t.addMedicine} sub={t.addMedHelp} />
          <form onSubmit={save} className="space-y-4">
            <label className="block font-semibold">{t.addMedName}
              <input className="input mt-1" required maxLength={120} value={name} onChange={(e) => setName(e.target.value)}
                     placeholder="Telma 40, Paracetamol 650…" autoComplete="off" /></label>
            <div role="group" aria-label={t.addMedWhen}>
              <p className="mb-1.5 font-semibold">{t.addMedWhen}</p>
              <div className="flex flex-wrap gap-2">
                {SLOTS.map((s) => <Chip key={s} selected={slots.has(s)} onClick={() => toggle(s)}>{(t as any)[s]}</Chip>)}
              </div>
              {slots.size === 0 && <p className="mt-1 text-[0.875rem] text-muted">{t.addMedAsNeeded}</p>}
            </div>
            <div role="group" aria-label={t.addMedFood}>
              <p className="mb-1.5 font-semibold">{t.addMedFood}</p>
              <div className="flex flex-wrap gap-2">
                {TIMINGS.map((x) => <Chip key={x} selected={timing === x} onClick={() => setTiming(x)}>{(t as any)[`food_${x}`]}</Chip>)}
              </div>
            </div>
            <label className="block font-semibold">{t.addMedDays}
              <input className="input mt-1 w-32 font-mono" inputMode="numeric" value={days}
                     onChange={(e) => setDays(e.target.value.replace(/\D/g, "").slice(0, 3))} placeholder="5" /></label>
            {err && <Banner tone="warning">{err}</Banner>}
            <div className="flex flex-wrap gap-2">
              <Button type="submit" loading={busy} disabled={!name.trim()}>{t.save}</Button>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>{t.cancel}</Button>
            </div>
          </form>
        </Card>
      )}
    </div>
  );
}
