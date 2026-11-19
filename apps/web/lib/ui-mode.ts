"use client";

import { useEffect, useState } from "react";

export type UiPref = "auto" | "desktop" | "mobile";
export type UiMode = "desktop" | "mobile";

/** auto: desktop from 1024 px with a fine pointer (or from 1280 px with any pointer). Spec: docs/DESKTOP_UI_SPEC.md §1. */
export function resolveMode(pref: UiPref | undefined): UiMode {
  if (pref === "desktop" || pref === "mobile") return pref;
  if (typeof window === "undefined") return "mobile";
  const w = window.innerWidth;
  return w >= 1024 && (matchMedia("(pointer: fine)").matches || w >= 1280) ? "desktop" : "mobile";
}

const SQUEEZE_WIDTH = 1280;

/** Writes html[data-ui], [data-ui-forced], [data-ui-squeeze] and, when desktop is forced on a narrow screen, renders
 *  the page at 1280 px like a browser's "desktop site" (pinch-zoom stays allowed). */
export function applyMode(pref: UiPref | undefined) {
  const r = document.documentElement;
  const mode = resolveMode(pref);
  r.setAttribute("data-ui", mode);
  pref && pref !== "auto" ? r.setAttribute("data-ui-forced", pref) : r.removeAttribute("data-ui-forced");
  const meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
  const narrow = (window.screen?.width ?? window.innerWidth) < 1024;
  const squeeze = mode === "desktop" && pref === "desktop" && narrow;
  squeeze ? r.setAttribute("data-ui-squeeze", "") : r.removeAttribute("data-ui-squeeze");
  if (meta) {
    if (!meta.dataset.original) meta.dataset.original = meta.content;
    meta.content = squeeze ? `width=${SQUEEZE_WIDTH}, viewport-fit=cover` : meta.dataset.original;
  }
  window.dispatchEvent(new Event("sushrut:ui-mode"));
}

function current(): UiMode {
  if (typeof document === "undefined") return "mobile";
  return (document.documentElement.getAttribute("data-ui") as UiMode) || "mobile";
}

/** The layout in use. Follows the html attribute (set before paint by prefs-boot, updated by applyMode).
 *  null until mounted: the server cannot know it, so callers render a neutral frame first (no hydration mismatch). */
export function useUiMode(): UiMode | null {
  const [m, setM] = useState<UiMode | null>(null);
  useEffect(() => {
    const on = () => setM(current());
    on();
    window.addEventListener("sushrut:ui-mode", on);
    return () => window.removeEventListener("sushrut:ui-mode", on);
  }, []);
  return m;
}

/** In auto mode, re-evaluate on resize (debounced) and pointer changes. */
export function useAutoMode(pref: UiPref | undefined) {
  useEffect(() => {
    if (pref && pref !== "auto") return;
    let id = 0;
    const on = () => { clearTimeout(id); id = window.setTimeout(() => { if (resolveMode(pref) !== current()) applyMode(pref); }, 200); };
    const mq = matchMedia("(pointer: fine)");
    window.addEventListener("resize", on);
    mq.addEventListener?.("change", on);
    return () => { window.removeEventListener("resize", on); mq.removeEventListener?.("change", on); clearTimeout(id); };
  }, [pref]);
}
