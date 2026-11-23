"use client";

// The API runs on the same machine as the web app. In the browser, reach it on the host the page was opened from:
// "localhost" on this PC, "192.168.x.y" on a phone over the home network. NEXT_PUBLIC_API_URL overrides it.
const API_PORT = process.env.NEXT_PUBLIC_API_PORT || "8787";
export const API = process.env.NEXT_PUBLIC_API_URL || (typeof window !== "undefined"
  ? `${window.location.protocol}//${window.location.hostname}:${API_PORT}/api/v1`
  : `http://localhost:${API_PORT}/api/v1`);

type Tokens = { access_token: string; refresh_token: string; user_id: string; is_guest: boolean };
const KEY = "sushrut.auth";

// Tokens live in sessionStorage for guests (cleared when the tab closes) and localStorage for signed-in users.
export function getTokens(): Tokens | null {
  try {
    const raw = sessionStorage.getItem(KEY) ?? localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Tokens) : null;
  } catch {
    return null;
  }
}

export function setTokens(t: Tokens | null) {
  try {
    sessionStorage.removeItem(KEY);
    localStorage.removeItem(KEY);
    if (t) (t.is_guest ? sessionStorage : localStorage).setItem(KEY, JSON.stringify(t));
    if (typeof window !== "undefined") window.dispatchEvent(new Event("sushrut:auth-changed"));
  } catch {
    /* storage unavailable: session lives in memory only */
  }
}

// ---- active family profile: whose health data the app is showing (absent = the account holder)
export type ActiveProfile = { id: string; name: string; relation: string };
const PROFILE_KEY = "sushrut.profile";

export function getProfile(): ActiveProfile | null {
  try {
    const raw = localStorage.getItem(PROFILE_KEY);
    return raw ? (JSON.parse(raw) as ActiveProfile) : null;
  } catch {
    return null;
  }
}

/** Switch profile and reload so every screen refetches that person's data. */
export function switchProfile(p: ActiveProfile | null, to = "/") {
  try {
    p ? localStorage.setItem(PROFILE_KEY, JSON.stringify(p)) : localStorage.removeItem(PROFILE_KEY);
    localStorage.removeItem("sushrut.emergencyCard");
  } catch { /* ignore */ }
  location.href = to;
}

/** Authorization + the active family profile, for every request to the API. */
export function authHeaders(): Record<string, string> {
  const t = getTokens();
  const p = getProfile();
  return { ...(t ? { Authorization: `Bearer ${t.access_token}` } : {}), ...(p ? { "X-Profile-Id": p.id } : {}) };
}

/** Download an authenticated file (exports, calendar). */
export async function downloadFile(path: string, name: string) {
  const r = await authFetch(`${API}${path}`);
  if (!r.ok) throw new ApiError(r.status, r.statusText);
  const a = document.createElement("a");
  a.href = URL.createObjectURL(await r.blob());
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export const SERVICE_ERROR_EVENT = "sushrut:service-error";
export type ServiceErrorDetail = { service: string; reason: string; hint?: string; status?: number };

export function reportServiceFailure(detail: ServiceErrorDetail) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<ServiceErrorDetail>(SERVICE_ERROR_EVENT, { detail }));
}

function serviceLabel(path: string): string {
  if (path.startsWith("/chat")) return "Chat & AI Model";
  if (path.startsWith("/tts") || path.startsWith("/stt") || path.startsWith("/voice")) return "Speech Service";
  if (path.startsWith("/clinical")) return "Clinical Context";
  if (path.startsWith("/me")) return "Profile & Settings API";
  return "Backend API";
}

// One refresh at a time: the refresh token rotates, so two parallel refreshes would spend it twice.
let refreshing: Promise<boolean> | null = null;
function refresh(): Promise<boolean> {
  refreshing ??= doRefresh().catch(() => false).finally(() => { refreshing = null; });
  return refreshing;
}

async function doRefresh(): Promise<boolean> {
  const t = getTokens();
  if (!t) return false;
  const r = await fetch(`${API}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: t.refresh_token }),
  });
  if (!r.ok) return false;
  const j = await r.json();
  setTokens({ ...t, access_token: j.access_token, refresh_token: j.refresh_token });
  return true;
}

/** fetch() for a raw response (images, files, streams) with the session headers; renews an expired token once. */
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const open = () => fetch(url, { ...init, headers: { ...(init.headers as Record<string, string> | undefined), ...authHeaders() } });
  const r = await open();
  return r.status === 401 && (await refresh()) ? open() : r;
}

export async function api<T = unknown>(path: string, init: RequestInit = {}, retry = true, silent = false): Promise<T> {
  const headers = new Headers(init.headers);
  for (const [k, v] of Object.entries(authHeaders())) headers.set(k, v);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let r: Response;
  try {
    r = await fetch(`${API}${path}`, { ...init, headers });
  } catch (e: any) {
    if (!silent && e?.name !== "AbortError") {
      reportServiceFailure({
        service: serviceLabel(path),
        reason: e?.message || "Network connection failed",
        hint: "Check that the SushrutAI backend server is running.",
      });
    }
    throw e;
  }
  if (r.status === 401 && retry && (await refresh())) return api<T>(path, init, false, silent);
  if (r.status === 403 && retry && getProfile()) {
    // the family profile was removed (or belongs to an old guest session): fall back to the account holder
    const detail = await r.clone().json().catch(() => ({}));
    if (detail?.detail === "Not a profile you manage") {
      try { localStorage.removeItem(PROFILE_KEY); } catch { /* ignore */ }
      return api<T>(path, init, false, silent);
    }
  }
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    // Do not popup on 501 TTS fallback (expected when device speech synthesis handles playback)
    if (!silent && !(path.startsWith("/tts") && r.status === 501)) {
      reportServiceFailure({ service: serviceLabel(path), reason: msg || `HTTP ${r.status}`, status: r.status });
    }
    throw new ApiError(r.status, msg);
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

let pending: Promise<Tokens> | null = null;

export function ensureSession(): Promise<Tokens> {
  pending ??= createOrReuseSession().finally(() => { pending = null; });
  return pending;
}

async function createOrReuseSession(): Promise<Tokens> {
  const t = getTokens();
  if (t) {
    try {
      await api("/me/profile", {}, true, true);
      return getTokens()!;
    } catch {
      setTokens(null);
    }
  }
  const g = await api<Tokens>("/auth/guest", { method: "POST" });
  setTokens(g);
  return g;
}

/** POST that returns a Server-Sent-Events stream; calls onEvent for each event. */
/** POST and read a server-sent event stream. `signal` aborts it (Stop): the server then stops the model too. */
export async function postSSE(path: string, body: unknown, onEvent: (event: string, data: any) => void,
                              signal?: AbortSignal) {
  let r: Response;
  try {
    r = await authFetch(`${API}${path}`, {  // renews a token that expired mid-chat
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  } catch (e: any) {
    if (e?.name !== "AbortError") {
      reportServiceFailure({
        service: serviceLabel(path),
        reason: e?.message || "Connection failed while streaming",
        hint: "Check that the SushrutAI backend server is running.",
      });
    }
    throw e;
  }
  if (!r.ok || !r.body) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail; } catch {}
    reportServiceFailure({ service: serviceLabel(path), reason: msg || `HTTP ${r.status}`, status: r.status });
    throw new ApiError(r.status, msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const ev = /^event: (.+)$/m.exec(block)?.[1];
      const data = /^data: (.+)$/m.exec(block)?.[1];
      if (ev && data) {
        const parsed = JSON.parse(data);
        if (ev === "error") {
          reportServiceFailure({
            service: parsed?.busy ? "AI Model (Busy)" : "AI Model",
            reason: parsed?.message || "Model failed to generate a response",
            hint: parsed?.hint,
          });
        }
        onEvent(ev, parsed);
      }
    }
  }
}
