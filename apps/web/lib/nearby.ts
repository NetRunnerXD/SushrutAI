import { api } from "./api";

export type Place = { name: string; kind: string; phone: string | null; distance_km: number; emergency_dept?: boolean;
                      address?: string | null };
export type Nearby = { places: Place[]; national: { name: string; phone: string }[]; lat: number; lon: number; at?: number;
                       unavailable?: boolean; phones_incomplete?: boolean };  // map down, or Google phone lookup failed

const KEY = "sushrut.nearby";

/** The phone's location (asks for permission the first time). */
export function position(timeoutMs = 10000): Promise<GeolocationPosition> {
  return new Promise((res, rej) => {
    if (!("geolocation" in navigator)) { rej(new Error("no geolocation")); return; }
    navigator.geolocation.getCurrentPosition(res, rej, { enableHighAccuracy: false, timeout: timeoutMs, maximumAge: 300000 });
  });
}

/** Nearest hospitals / ambulance services; the last list is kept on the device for when there is no signal. */
export async function findNearby(): Promise<Nearby> {
  try {
    const p = await position();
    const r = await api<Nearby>(`/emergency/nearby?lat=${p.coords.latitude}&lon=${p.coords.longitude}`);
    if (r.unavailable) return cachedNearby() ?? r;  // keep the last good list rather than an empty one
    try { localStorage.setItem(KEY, JSON.stringify({ ...r, at: Date.now() })); } catch { /* ignore */ }
    return r;
  } catch (e) {
    const cached = cachedNearby();
    if (cached) return cached;
    throw e;
  }
}

export function cachedNearby(): Nearby | null {
  try { const c = localStorage.getItem(KEY); return c ? JSON.parse(c) : null; } catch { return null; }
}

/** The first place with a direct number, else the national ambulance number. */
export function bestToCall(n: Nearby | null): { name: string; phone: string } {
  const p = n?.places.find((x) => x.phone);
  return p ? { name: p.name, phone: p.phone!.replace(/[^\d+]/g, "") } : { name: "Ambulance", phone: "108" };
}
