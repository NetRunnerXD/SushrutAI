"use client";

import { Monitor, Smartphone } from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";
import { useUiMode } from "@/lib/ui-mode";

/** One-tap switch between the desktop and mobile layouts (a forced choice; Settings → Display can set Automatic). */
export function LayoutToggle() {
  const { t } = useI18n();
  const { setPrefs } = usePrefs();
  const mode = useUiMode();
  const toDesktop = mode === "mobile";
  const Icon = toDesktop ? Monitor : Smartphone;
  return (
    <button type="button" onClick={() => setPrefs({ ui: toDesktop ? "desktop" : "mobile" })} title="Ctrl+Shift+D"
      className="btn btn-outline"><Icon aria-hidden className="h-5 w-5" />{toDesktop ? t.useDesktopLayout : t.useMobileLayout}</button>
  );
}
