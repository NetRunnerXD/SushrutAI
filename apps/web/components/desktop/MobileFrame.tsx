"use client";

import { Monitor } from "lucide-react";
import { useEffect, useRef } from "react";
import { useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";

/** Mobile layout forced on a wide screen (docs/DESKTOP_UI_SPEC.md §1.4): the phone app runs at phone width in a
 *  centred 440px frame over the ambient field. It is the same app (same origin, same session), so every breakpoint,
 *  the bottom bar, sheets and the floating mic behave exactly as on a phone. The address bar follows the frame. */
export function MobileFrame() {
  const { t } = useI18n();
  const { setPrefs } = usePrefs();
  const ref = useRef<HTMLIFrameElement>(null);
  useEffect(() => {
    const on = (e: MessageEvent) => {
      if (e.origin !== location.origin || e.data?.type !== "sushrut:path") return;
      if (e.data.path !== location.pathname + location.search) history.replaceState(null, "", e.data.path);
    };
    window.addEventListener("message", on);
    return () => window.removeEventListener("message", on);
  }, []);
  return (
    <>
      <div aria-hidden className="d-backdrop" />
      <div className="flex min-h-dvh items-center justify-center gap-6 p-4">
        <iframe ref={ref} title="SushrutAI" src={location.pathname + location.search}
          className="h-[calc(100dvh-2rem)] max-h-[960px] w-[440px] rounded-[24px] border border-[var(--d-frame-line)] bg-[var(--bg)] shadow-[var(--e3)]" />
        <button type="button" onClick={() => setPrefs({ ui: "auto" })}
          className="d-press absolute bottom-6 end-6 inline-flex h-11 items-center gap-2 rounded-full border border-[var(--d-tile-line)] bg-[var(--d-pop)] px-4 font-semibold text-text shadow-[var(--e2)]">
          <Monitor aria-hidden className="h-4 w-4" />{t.useDesktopLayout}</button>
      </div>
    </>
  );
}

/** Inside the frame: report the current path to the window around it. */
export function useReportPathToFrame(path: string) {
  useEffect(() => {
    if (typeof window === "undefined" || window.self === window.top) return;
    window.parent.postMessage({ type: "sushrut:path", path: location.pathname + location.search }, location.origin);
  }, [path]);
}
