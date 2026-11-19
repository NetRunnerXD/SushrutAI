"use client";

import { Ambulance, HeartHandshake, Hospital, Phone, Share2, ShieldAlert, Siren } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Sheet } from "./ui/Sheet";

type Card = { age: number | null; sex: string | null; blood_group: string | null; emergency_contact: string | null;
              allergies: string[]; conditions: string[]; medicines: string[] };
const CACHE = "sushrut.emergencyCard";

const Ctx = createContext<{ openSOS: () => void }>({ openSOS: () => {} });
export const useSOS = () => useContext(Ctx);

function phoneOf(contact: string | null): string | null {
  const m = contact?.match(/(\+?\d[\d\s-]{8,}\d)/);
  return m ? m[1].replace(/[\s-]/g, "") : null;
}

/** Always-available emergency sheet. The responder card is cached locally so it works offline. */
export function EmergencyProvider({ children }: { children: ReactNode }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [card, setCard] = useState<Card | null>(null);
  const openSOS = useCallback(() => setOpen(true), []);

  useEffect(() => {
    if (!open) return;
    try { const c = localStorage.getItem(CACHE); if (c) setCard(JSON.parse(c)); } catch { /* ignore */ }
    api<Card>("/me/emergency-card").then((c) => {
      setCard(c);
      try { localStorage.setItem(CACHE, JSON.stringify(c)); } catch { /* ignore */ }
    }).catch(() => { /* offline: cached card stays */ });
  }, [open]);

  const contactPhone = phoneOf(card?.emergency_contact ?? null);
  const cardText = card ? [
    `${t.emergencyCard}`, card.blood_group ? `${t.bloodGroup}: ${card.blood_group}` : "",
    `${t.allergiesLabel}: ${card.allergies.join(", ") || t.none}`, `${t.conditionsLabel}: ${card.conditions.join(", ") || t.none}`,
    `${t.medicinesLabel}: ${card.medicines.join(", ") || t.none}`].filter(Boolean).join("\n") : "";

  const big = "flex min-h-16 w-full items-center gap-3 rounded-[16px] px-4 text-left text-[1.125rem] font-bold";
  return (
    <Ctx.Provider value={{ openSOS }}>
      {children}
      <Sheet open={open} onClose={() => setOpen(false)} title={<span className="flex items-center gap-2"><Siren aria-hidden className="h-6 w-6" />{t.sosTitle}</span>} tone="emergency">
        <div className="space-y-3">
          <a href="tel:108" className={`${big} bg-emergency text-white`}><Ambulance aria-hidden className="h-7 w-7" />{t.call108}</a>
          <a href="tel:112" className={`${big} bg-emergency text-white`}><Phone aria-hidden className="h-7 w-7" />{t.call112}</a>
          {contactPhone && (
            <a href={`tel:${contactPhone}`} className={`${big} border-2 border-danger bg-surface text-danger`}>
              <Phone aria-hidden className="h-6 w-6" />{t.callContact}
            </a>
          )}
          <a href="tel:14416" className={`${big} bg-info-soft text-info`}><HeartHandshake aria-hidden className="h-6 w-6" />{t.teleManas}</a>
          <a href="https://www.google.com/maps/search/hospital+near+me" target="_blank" rel="noreferrer"
             className={`${big} bg-surface-2 text-text`}><Hospital aria-hidden className="h-6 w-6" />{t.nearestHospital}</a>

          <section aria-labelledby="ecard" className="rounded-[16px] border-2 border-danger p-4">
            <h3 id="ecard" className="display mb-2 flex items-center gap-2 text-[1.125rem] font-bold text-danger"><ShieldAlert aria-hidden className="h-5 w-5" />{t.emergencyCard}</h3>
            {card ? (
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[1rem]">
                {card.age != null && (<><dt className="text-muted">Age</dt><dd>{card.age}{card.sex ? ` · ${card.sex}` : ""}</dd></>)}
                <dt className="text-muted">{t.bloodGroup}</dt><dd className="font-semibold">{card.blood_group ?? "—"}</dd>
                <dt className="text-muted">{t.allergiesLabel}</dt><dd className="font-semibold text-danger">{card.allergies.join(", ") || t.none}</dd>
                <dt className="text-muted">{t.conditionsLabel}</dt><dd>{card.conditions.join(", ") || t.none}</dd>
                <dt className="text-muted">{t.medicinesLabel}</dt><dd>{card.medicines.join(", ") || t.none}</dd>
              </dl>
            ) : <p className="text-muted">…</p>}
            <p className="mt-2 text-[0.875rem] text-muted">{t.showToResponder}</p>
            {card && typeof navigator !== "undefined" && "share" in navigator && (
              <button onClick={() => navigator.share({ title: t.emergencyCard, text: cardText }).catch(() => {})}
                      className="mt-2 inline-flex min-h-12 items-center gap-2 rounded-[14px] bg-surface-2 px-4 font-semibold">
                <Share2 aria-hidden className="h-5 w-5" />{t.share}</button>
            )}
          </section>
        </div>
      </Sheet>
    </Ctx.Provider>
  );
}

export function SOSButton() {
  const { t } = useI18n();
  const { openSOS } = useSOS();
  return (
    <button onClick={openSOS} aria-label={t.sosOpen}
      className="inline-flex min-h-12 items-center gap-1.5 shrink-0 rounded-full bg-emergency px-3 font-bold tracking-wide text-white shadow-[inset_0_1px_0_rgb(255_255_255/25%),0_0_0_2px_rgb(255_255_255/14%)] hover:brightness-110 sm:px-4">
      <Siren aria-hidden className="h-5 w-5" />{t.sos}
    </button>
  );
}
