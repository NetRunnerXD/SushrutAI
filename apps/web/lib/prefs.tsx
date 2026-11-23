"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/** Display preferences: stored per device (localStorage), applied as data-* attributes on <html>. */
export type Prefs = {
  text: "md" | "lg" | "xl"; theme: "auto" | "light" | "dark"; contrast: "normal" | "high"; motion: "auto" | "reduce";
  rate: "slow" | "normal" | "fast";   // read-aloud speed
  simple: boolean;                    // Simple mode: big-button Home, everything read aloud
  home: { order: string[]; hidden: string[] }; // Home card layout ("Customise Home")
  sidebar: "full" | "rail";           // desktop sidebar: labels, or icons only
  chatList: boolean;                  // Ask page: chat history column shown on wide screens
  ui: "auto" | "desktop" | "mobile";
  deskRail: "icons" | "pinned";       // desktop rail: icons (expands on hover) or the labelled menu pinned open // layout: automatic by screen, or forced (docs/DESKTOP_UI_SPEC.md §1)
  profileStrip: boolean;              // Ask page: "who this is for" strip expanded (collapsed by default)
  sections: Record<string, boolean>;  // Answer sections the person opened/closed (overrides the default)
  chatUi: "v1" | "v2";                // Chat response layout: v1 (classic) or v2 (modular cards)
};
const DEFAULT: Prefs = { text: "md", theme: "auto", contrast: "normal", motion: "auto", rate: "normal", simple: false,
                         home: { order: [], hidden: [] }, sidebar: "full", chatList: true,
                         profileStrip: false, sections: {}, ui: "auto", deskRail: "icons", chatUi: "v2" };
import { PREFS_KEY as KEY } from "./prefs-boot";
import { applyMode, useAutoMode } from "./ui-mode";

function apply(p: Prefs) {
  const r = document.documentElement;
  p.text === "md" ? r.removeAttribute("data-text") : r.setAttribute("data-text", p.text);
  p.theme === "auto" ? r.removeAttribute("data-theme") : r.setAttribute("data-theme", p.theme);
  p.contrast === "high" ? r.setAttribute("data-contrast", "high") : r.removeAttribute("data-contrast");
  p.motion === "reduce" ? r.setAttribute("data-motion", "reduce") : r.removeAttribute("data-motion");
  p.sidebar === "rail" ? r.setAttribute("data-sidebar", "rail") : r.removeAttribute("data-sidebar");
  if ((r.getAttribute("data-ui-forced") ?? "auto") !== p.ui || !r.hasAttribute("data-ui")) applyMode(p.ui);
}

const Ctx = createContext<{ prefs: Prefs; setPrefs: (p: Partial<Prefs>) => void }>({ prefs: DEFAULT, setPrefs: () => {} });

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, set] = useState<Prefs>(DEFAULT);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    try { set({ ...DEFAULT, ...JSON.parse(localStorage.getItem(KEY) || "{}") }); } catch { /* ignore */ }
    setLoaded(true);
  }, []);
  useEffect(() => {
    if (!loaded) return;
    apply(prefs);
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch { /* ignore */ }
  }, [prefs, loaded]);
  // Functional update so quick successive changes don't overwrite each other.
  const setPrefs = (patch: Partial<Prefs>) => set((cur) => ({ ...cur, ...patch }));
  useAutoMode(prefs.ui);
  // Ctrl+Shift+D: switch between the desktop and mobile layouts (a forced choice; Settings can set it back to automatic)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && (e.key === "D" || e.key === "d")) {
        e.preventDefault();
        set((cur) => ({ ...cur, ui: document.documentElement.getAttribute("data-ui") === "desktop" ? "mobile" : "desktop" }));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return <Ctx.Provider value={{ prefs, setPrefs }}>{children}</Ctx.Provider>;
}

export const usePrefs = () => useContext(Ctx);
