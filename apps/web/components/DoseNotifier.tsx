"use client";

import { useEffect } from "react";
import { API, getProfile, getTokens, authFetch } from "@/lib/api";
import { useI18n, bcp47 } from "@/lib/i18n";
import { speak } from "@/lib/voice";
import { REMINDER_EVENT } from "./VoiceLoop";

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
const cancelled = new Set<string>();  // reminders cancelled by voice (this session)

export function DoseNotifier() {
  const { t, lang } = useI18n();
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
        const r = await authFetch(`${API}/me/doses`);
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
          remind(d, `${t.reminderBody} ${d.name}`);
          reg.showNotification("SushrutAI", {
            body: `${t.reminderBody} ${d.name}`, tag: `${d.medication_id}-${d.slot}`,
            data: { api: API, token: getTokens()?.access_token, profile: getProfile()?.id, medication_id: d.medication_id, slot: d.slot },
            // @ts-expect-error actions are supported by service-worker notifications
            actions: [{ action: "taken", title: t.taken }],
          }).catch(() => {});
        }, ms));
      }
    };
    // Spoken reminders (with the app open): said aloud as well as shown; "remind me later" repeats it in 15
    // minutes, "cancel the reminder" stops it for that dose today.
    let last: { d: any; text: string } | null = null;
    const remind = (d: any, text: string) => {
      if (cancelled.has(`${d.medication_id}-${d.slot}`)) return;
      last = { d, text };
      speak(`${text}. ${t.reminderVoiceHint}`, lang, bcp47(lang));
    };
    const onVoice = (e: Event) => {
      const what = (e as CustomEvent).detail;
      if (!last) return;
      const cur = last;
      if (what === "snooze") { timers.push(window.setTimeout(() => remind(cur.d, cur.text), 15 * 60 * 1000)); speak(t.reminderSnoozed, lang, bcp47(lang)); }
      if (what === "cancelReminder") { cancelled.add(`${cur.d.medication_id}-${cur.d.slot}`); speak(t.reminderCancelled, lang, bcp47(lang)); }
    };
    schedule();
    window.addEventListener("sushrut:data-changed", schedule);
    window.addEventListener(REMINDER_EVENT, onVoice);
    return () => { timers.forEach(clearTimeout); window.removeEventListener("sushrut:data-changed", schedule); window.removeEventListener(REMINDER_EVENT, onVoice); };
  }, [t, lang]);
  return null;
}
