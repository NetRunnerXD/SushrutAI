"use client";

import { Home, MessageCircle, PanelLeftClose, PanelLeftOpen, Pill, FolderHeart, UserRound, WifiOff, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, ensureSession } from "@/lib/api";
import { I18nProvider, useI18n } from "@/lib/i18n";
import { PrefsProvider, usePrefs } from "@/lib/prefs";
import { EmergencyProvider, SOSButton } from "./Emergency";
import { ProfileBanner, ProfileSwitcher } from "./Family";
import { SearchButton } from "./Search";
import { LanguageButton } from "./LanguagePicker";
import { PrivacyBadge } from "./ui/Trust";
import { FloatingMic } from "./Voice";

const NAV: { href: string; key: "navHome" | "navAsk" | "navMedicines" | "navRecords" | "navMe"; icon: LucideIcon }[] = [
  { href: "/", key: "navHome", icon: Home },
  { href: "/ask", key: "navAsk", icon: MessageCircle },
  { href: "/medicines", key: "navMedicines", icon: Pill },
  { href: "/records", key: "navRecords", icon: FolderHeart },
  { href: "/me", key: "navMe", icon: UserRound },
];

function useOnline() {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const up = () => setOnline(navigator.onLine);
    up();
    window.addEventListener("online", up);
    window.addEventListener("offline", up);
    return () => { window.removeEventListener("online", up); window.removeEventListener("offline", up); };
  }, []);
  return online;
}

function Inner({ children }: { children: ReactNode }) {
  const { t, chosen } = useI18n();
  const path = usePathname();
  const router = useRouter();
  const online = useOnline();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localOnly, setLocalOnly] = useState(true);
  const isWelcome = path.startsWith("/welcome");
  const { prefs, setPrefs } = usePrefs();
  const rail = prefs.sidebar === "rail";
  // On-screen keyboard: when it takes a big share of the screen, hide the bottom bar and floating mic (CSS on data-kb)
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const on = () => document.documentElement.toggleAttribute("data-kb", vv.height < window.innerHeight * 0.72 || window.innerHeight - vv.height > 160);
    vv.addEventListener("resize", on);
    return () => { vv.removeEventListener("resize", on); document.documentElement.removeAttribute("data-kb"); };
  }, []);
  const toggleRail = () => setPrefs({ sidebar: rail ? "full" : "rail" });
  // "[" toggles the desktop sidebar (not while typing)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.key !== "[" || e.ctrlKey || e.metaKey || e.altKey) return;
      if (el?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el?.tagName)) return;
      setPrefs({ sidebar: document.documentElement.dataset.sidebar === "rail" ? "full" : "rail" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPrefs]);
  // Simple mode: fewer destinations (SOS stays in the header on every screen)
  const nav = prefs.simple ? NAV.filter((n) => ["/", "/ask", "/medicines"].includes(n.href)) : NAV;

  useEffect(() => {
    (async () => {
      try {
        await ensureSession();
        const c = await api<{ active: string[] }>("/consents");
        const needsOnboarding = !c.active.includes("core_service");
        if (needsOnboarding && !isWelcome) { router.replace("/welcome"); return; }
        setReady(true);
        api<any>("/me/settings").then((s) => setLocalOnly(s.offline_mode || !c.active.includes("cloud_llm_deidentified"))).catch(() => {});
      } catch (e: any) {
        setError(e.message);
        setReady(true);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isWelcome]);

  useEffect(() => {
    if (!chosen && !isWelcome) router.replace("/welcome");
  }, [chosen, isWelcome, router]);

  if (isWelcome) return <main id="main" className="min-h-dvh">{children}</main>;

  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  return (
    <div className="app-grid min-h-dvh print:block">
      <a href="#main" className="sr-only z-50 rounded-[10px] bg-brand px-4 py-3 font-semibold text-brand-ink focus:not-sr-only focus:fixed focus:left-3 focus:top-3">
        {t.skipToContent}
      </a>

      {/* Desktop sidebar: the notebook's cover board, stitched to the page */}
      {/* Sidebar (tablet: icon rail; desktop: full or rail, user's choice). The notebook's cover board, stitched to the page. */}
      <aside className="sidebar relative hidden print:!hidden bg-surface-2 md:sticky md:top-0 md:flex md:h-dvh md:flex-col md:py-5">
        <span aria-hidden className="stitch absolute inset-y-3 right-2" />
        <div className="sb-head mb-8 flex items-center gap-2">
          <Link href="/" className="flex min-w-0 flex-1 items-center gap-3" aria-label="SushrutAI home">
            <LogoMark className="h-12 w-12" />
            <span className="sb-label min-w-0"><span className="serif block text-[1.375rem] font-extrabold leading-tight">SushrutAI</span>
              <span className="block text-[0.8125rem] leading-snug text-muted">{t.appTagline}</span></span>
          </Link>
        </div>
        <nav aria-label="Main">
          <ul className="sb-list space-y-1.5">
            {nav.map((n) => (
              <li key={n.href}>
                <Link href={n.href} aria-current={active(n.href) ? "page" : undefined} title={t[n.key]}
                  className={`sb-link group flex min-h-12 items-center gap-3 rounded-[8px] text-[1.0625rem] font-semibold
                    ${active(n.href) ? "text-text" : "text-muted hover:text-text"}`}>
                  <span key={active(n.href) ? "on" : "off"} className={`sb-icon grid h-10 w-10 shrink-0 place-items-center ${active(n.href) ? "sb-on" : ""}`}>
                    <n.icon aria-hidden className={`h-6 w-6 ${active(n.href) ? "text-brand" : ""}`} strokeWidth={active(n.href) ? 2.4 : 2} />
                  </span>
                  <span key={active(n.href) ? "lon" : "loff"} className={`sb-label ${active(n.href) ? "highlight px-1" : "px-1"}`}>{t[n.key]}</span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
        <div className="sb-foot mt-auto space-y-2">
          <div className="sb-label space-y-2">
            <PrivacyBadge local={localOnly} />
            <p className="px-1 font-mono text-[0.875rem] leading-relaxed text-muted">108 · 112<br />Tele-MANAS 14416</p>
          </div>
          <button type="button" onClick={toggleRail} aria-expanded={!rail} aria-label={rail ? t.expandSidebar : t.collapseSidebar}
            title={`${rail ? t.expandSidebar : t.collapseSidebar} ( [ )`}
            className="sb-toggle hidden min-h-12 items-center gap-2 rounded-[8px] px-2 font-semibold text-muted hover:bg-surface hover:text-text lg:flex">
            {rail ? <PanelLeftOpen aria-hidden className="h-6 w-6 shrink-0" /> : <PanelLeftClose aria-hidden className="h-6 w-6 shrink-0" />}
            <span className="sb-label text-[0.9375rem]">{t.collapseSidebar}</span>
          </button>
        </div>
      </aside>

      <div className="flex min-h-dvh min-w-0 flex-col">
        {/* Header */}
        <header className="sticky top-0 z-20 print:hidden border-b border-border bg-bg/92 backdrop-blur-sm">
          <div className="mx-auto flex max-w-[1200px] items-center gap-2 px-3 py-2 sm:px-4 lg:px-8">
            <Link href="/" className="flex min-w-0 items-center gap-2 max-[389px]:hidden md:hidden" aria-label="SushrutAI home">
              <LogoMark className="h-10 w-10" />
              <span className="serif truncate text-[1.125rem] font-extrabold max-[479px]:sr-only">SushrutAI</span>
            </Link>
            <SearchButton />
            <div className="flex-1" />
            <ProfileSwitcher />
            <LanguageButton />
            <SOSButton />
          </div>
          <ProfileBanner />
          {!online && (
            <p role="status" className="flex items-center justify-center gap-2 bg-warn-soft px-4 py-1.5 text-[0.9375rem] font-semibold text-warn">
              <WifiOff aria-hidden className="h-4 w-4" />{t.youreOffline}
            </p>
          )}
        </header>

        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1200px] flex-1 px-4 pb-[calc(9rem+env(safe-area-inset-bottom))] pt-5 md:px-6 md:pb-12 lg:px-8 lg:pt-8 print:p-0">
          {error ? (
            <div role="alert" className="rounded-[3px_8px_8px_3px] border-l-[5px] border-danger bg-danger-soft p-4 text-danger">
              Cannot reach the SushrutAI server ({error}). Start it with: uvicorn app.main:app --port 8787
            </div>
          ) : ready ? children : (
            <div className="space-y-3" aria-busy="true"><div className="skeleton h-28" /><div className="skeleton h-40" /></div>
          )}
        </main>
      </div>

      {/* Mobile bottom tab bar: notebook tabs, the current one marked with a highlighter */}
      <nav aria-label="Main" className="bottom-nav fixed inset-x-0 bottom-0 z-20 print:hidden border-t border-border bg-surface/96 pb-safe backdrop-blur-sm md:hidden">
        <ul className="mx-auto flex max-w-xl">
          {nav.map((n) => {
            const on = active(n.href);
            return (
              <li key={n.href} className="min-w-0 flex-1">
                <Link href={n.href} aria-current={on ? "page" : undefined}
                  className={`flex min-h-16 flex-col items-center justify-center gap-0.5 px-0 pb-1 text-[0.78rem] font-semibold tracking-[-0.01em]
                    ${on ? "text-text" : "text-muted"}`}>
                  <span key={on ? "on" : "off"} className={`grid h-8 w-14 place-items-center ${on ? "highlight" : ""}`}>
                    <n.icon aria-hidden className={`h-6 w-6 ${on ? "text-brand" : ""}`} strokeWidth={on ? 2.4 : 2} />
                  </span>
                  <span className="max-w-full text-center leading-tight [overflow-wrap:anywhere]">{t[n.key]}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="print:hidden"><FloatingMic /></div>
    </div>
  );
}

/** The SushrutAI mark: "सु" in Martel, set in a blot of ink with a turmeric dot. */
export function LogoMark({ className = "" }: { className?: string }) {
  return (
    <span aria-hidden className={`relative grid shrink-0 place-items-center ${className}`}>
      <span className="absolute inset-0 rounded-[47%_53%_44%_56%/54%_41%_59%_46%] bg-brand" />
      <span className="absolute right-0 top-0.5 h-[22%] w-[22%] rounded-full bg-accent" />
      <span className="serif relative text-[1.25em] font-extrabold leading-none text-brand-ink" style={{ lineHeight: 1 }}>सु</span>
    </span>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <PrefsProvider>
      <I18nProvider>
        <EmergencyProvider>
          <Inner>{children}</Inner>
        </EmergencyProvider>
      </I18nProvider>
    </PrefsProvider>
  );
}
