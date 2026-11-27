"use client";

import { AlertTriangle, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { SERVICE_ERROR_EVENT, type ServiceErrorDetail } from "@/lib/api";

type ToastItem = ServiceErrorDetail & { id: number; key: string };

const DISMISS_MS = 7000;
const DEDUPE_MS = 4000;

/** Global floating popup notifying the user whenever any backend API or AI/speech model fails, with reason. */
export function ServiceAlert() {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const recent = useRef<Map<string, number>>(new Map());
  const timers = useRef<Map<number, number>>(new Map());

  const dismiss = (id: number) => {
    const t = timers.current.get(id);
    if (t) {
      window.clearTimeout(t);
      timers.current.delete(id);
    }
    setItems((cur) => cur.filter((x) => x.id !== id));
  };

  useEffect(() => {
    const onErr = (e: Event) => {
      const detail = (e as CustomEvent<ServiceErrorDetail>).detail;
      if (!detail?.reason) return;
      const key = `${detail.service}:${detail.reason}`;
      const now = Date.now();
      const prev = recent.current.get(key);
      if (prev && now - prev < DEDUPE_MS) return;
      recent.current.set(key, now);

      const id = ++seq.current;
      const item: ToastItem = { ...detail, id, key };
      setItems((cur) => [...cur.slice(-2), item]);

      const tid = window.setTimeout(() => dismiss(id), DISMISS_MS);
      timers.current.set(id, tid);
    };

    window.addEventListener(SERVICE_ERROR_EVENT, onErr);
    return () => {
      window.removeEventListener(SERVICE_ERROR_EVENT, onErr);
      timers.current.forEach((t) => window.clearTimeout(t));
      timers.current.clear();
    };
  }, []);

  if (!items.length) return null;

  return (
    <div aria-live="assertive" aria-atomic="true"
      className="pointer-events-none fixed top-4 end-4 z-50 flex w-[calc(100vw-2rem)] max-w-sm flex-col gap-2 print:hidden">
      {items.map((item) => (
        <div key={item.id} role="alert"
          onMouseEnter={() => {
            const t = timers.current.get(item.id);
            if (t) { window.clearTimeout(t); timers.current.delete(item.id); }
          }}
          onMouseLeave={() => {
            if (!timers.current.has(item.id)) {
              timers.current.set(item.id, window.setTimeout(() => dismiss(item.id), 3500));
            }
          }}
          className="lift pointer-events-auto flex items-start gap-3 rounded-[18px] border border-danger/35 bg-surface/95 p-3.5 shadow-[var(--rim),var(--e3)] backdrop-blur-md">
          <span aria-hidden className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-danger-soft text-danger">
            <AlertTriangle className="h-4.5 w-4.5" />
          </span>
          <div className="min-w-0 flex-1 space-y-0.5">
            <p className="text-[0.875rem] font-bold leading-snug text-text">
              {item.service} failed{item.status ? ` (${item.status})` : ""}
            </p>
            <p className="text-[0.8125rem] leading-snug text-text/90 [overflow-wrap:anywhere]">{item.reason}</p>
            {item.hint && (
              <p className="text-[0.75rem] leading-snug text-muted [overflow-wrap:anywhere]">{item.hint}</p>
            )}
          </div>
          <button type="button" onClick={() => dismiss(item.id)} aria-label="Dismiss alert"
            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-surface-2 hover:text-text">
            <X aria-hidden className="h-4 w-4" />
          </button>
        </div>
      ))}
    </div>
  );
}
