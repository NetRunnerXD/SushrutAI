"use client";

import { MapPin, Pill } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useDataChanged } from "@/components/Home";
import { Banner } from "@/components/ui/Trust";
import { api } from "@/lib/api";
import { fill, useI18n } from "@/lib/i18n";

export type Refill = { medication_id: string; name: string; days_left: number;
                       cheapest: { id: string; name: string; per_unit_inr: number; is_janaushadhi: boolean } | null };

export function useRefills() {
  const [items, setItems] = useState<Refill[]>([]);
  const load = useCallback(() => { api<Refill[]>("/me/refills").then(setItems).catch(() => setItems([])); }, []);
  useEffect(load, [load]);
  useDataChanged(load);
  return items;
}

export const KENDRA_MAP = "https://www.google.com/maps/search/Jan+Aushadhi+Kendra+near+me";

/** Medicines running out within 3 days, each with the cheapest same-medicine option and the nearest Kendra. */
export function Refills() {
  const { t } = useI18n();
  const items = useRefills();
  if (!items.length) return null;
  return (
    <div className="space-y-3">
      {items.map((r) => (
        <Banner key={r.medication_id} tone="warning" title={fill(t.refillSoon, { name: r.name, n: r.days_left })}>
          {r.cheapest ? (
            <p>{fill(t.refillCheapest, { name: r.cheapest.name, price: `₹${r.cheapest.per_unit_inr.toFixed(2)}` })}
              {r.cheapest.is_janaushadhi ? ` · ${t.janAushadhi}` : ""}</p>
          ) : <p>{t.refillAsk}</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            {r.cheapest && <Link href={`/medicines/${r.cheapest.id}`} className="btn btn-outline"><Pill aria-hidden className="h-5 w-5" />{t.refillSee}</Link>}
            <a href={KENDRA_MAP} target="_blank" rel="noreferrer" className="btn btn-outline"><MapPin aria-hidden className="h-5 w-5" />{t.findKendra}</a>
          </div>
        </Banner>
      ))}
    </div>
  );
}
