"use client";

import { useEffect } from "react";
import { API, authHeaders, getProfile, getTokens } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

const PREF = "sushrut.doseNotifications";

export function notificationsOn(): boolean {
  try { return localStorage.getItem(PREF) === "on"; } catch { return false; }
}

/** Turn browser dose reminders on (asks permission) or off. Returns the resulting state. */
export async function setNotifications(on: boolean): Promise<boolean> {
  if (!on) { try { localStorage.setItem(PREF, "off"); } catch { /* ignore */ } window.dispatchEvent(new Event("sushrut:data-changed")); return false; }
  if (typeof Notification === "undefined") return false;
  if ((await Notification.requestPermission()) !== "granted") return false;
  try { localStorage.setItem(PREF, "on"); } catch { /* ignore */ }
  window.dispatchEvent(new Event("sushrut:data-changed"));
  return true;
}

/** Service workers need a secure context: https, or localhost on this PC. On a phone over plain http they are off. */
export function swSupported(): boolean {
  return typeof window !== "undefined" && window.isSecureContext && "serviceWorker" in navigator;
}

/** Tell the service worker to drop cached health data (after deleting data or signing out). */
export async function clearOfflineData() {
  try {  // names typed on the patient card (lib/patientCard) are personal data too
    for (const k of Object.keys(localStorage)) if (k.startsWith("sushrut.cardName.")) localStorage.removeItem(k);
  } catch { /* storage unavailable */ }
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    reg?.active?.postMessage({ type: "clear-data" });
    await Promise.all(["sushrut-data-v1", "sushrut-pages-v1"].map((k) => caches.delete(k)));
  } catch { /* nothing cached */ }
}

/** Registers the service worker and schedules today's not-yet-taken doses as notifications with a "Taken" action.
 *  Re-schedules when doses change. Works while the app is open in a tab (browsers cannot wake a closed page). */
export function DoseNotifier() {
  const { t } = useI18n();
  useEffect(() => {
    if (!swSupported()) return;
    navigator.serviceWorker.register("/sw.js").catch(() => {});
    let timers: number[] = [];
    const schedule = async () => {
      timers.forEach(clearTimeout);
      timers = [];
      if (!notificationsOn() || Notification.permission !== "granted") return;
      let items: any[] = [];
      try {
        const r = await fetch(`${API}/me/doses`, { headers: authHeaders() });
        if (r.ok) items = (await r.json()).items ?? [];
      } catch { return; }
      const reg = await navigator.serviceWorker.ready;
      const now = new Date();
      for (const d of items) {
        if (!d.time || d.status === "taken" || d.status === "skipped" || d.slot === "as_needed") continue;
        const [h, m] = d.time.split(":").map(Number);
        const ms = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m).getTime() - now.getTime();
        if (ms <= 0) continue;
        timers.push(window.setTimeout(() => {
          reg.showNotification("SushrutAI", {
            body: `${t.reminderBody} ${d.name}`, tag: `${d.medication_id}-${d.slot}`,
            data: { api: API, token: getTokens()?.access_token, profile: getProfile()?.id, medication_id: d.medication_id, slot: d.slot },
            // @ts-expect-error actions are supported by service-worker notifications
            actions: [{ action: "taken", title: t.taken }],
          }).catch(() => {});
        }, ms));
      }
    };
    schedule();
    window.addEventListener("sushrut:data-changed", schedule);
    return () => { timers.forEach(clearTimeout); window.removeEventListener("sushrut:data-changed", schedule); };
  }, [t]);
  return null;
}
