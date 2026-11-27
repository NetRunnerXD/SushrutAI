"use client";

import { ClipboardList, Home, MessageCircle, PanelLeftClose, PanelLeftOpen, Pill, FolderHeart, UserRound, WifiOff, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, ensureSession } from "@/lib/api";
import { I18nProvider, useI18n } from "@/lib/i18n";
import { PrefsProvider, usePrefs } from "@/lib/prefs";
import { EmergencyProvider, SOSButton } from "./Emergency";
import { FamilyTiers, ProfileBanner, ProfileSwitcher } from "./Family";
import { SearchButton } from "./Search";
import { LanguageButton } from "./LanguagePicker";
import { PrivacyBadge } from "./ui/Trust";
import { FloatingMic } from "./Voice";
import { LiveTalkProvider } from "./LiveTalkProvider";
import { VoiceLoopProvider } from "./VoiceLoop";
import { DoseNotifier } from "./DoseNotifier";
import { ServiceAlert } from "./ServiceAlert";
import { DesktopShell } from "./desktop/DesktopShell";
import { MobileFrame, useReportPathToFrame } from "./desktop/MobileFrame";
import { useUiMode } from "@/lib/ui-mode";

const NAV: { href: string; key: "navHome" | "navAsk" | "navContext" | "navMedicines" | "navRecords" | "navMe"; icon: LucideIcon }[] = [
  { href: "/", key: "navHome", icon: Home },
  { href: "/ask", key: "navAsk", icon: MessageCircle },
  { href: "/context", key: "navContext", icon: ClipboardList },
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
  const mode = useUiMode();
  useReportPathToFrame(path);
  // Mobile forced on a wide screen: the phone app in a centred frame (not inside a frame already)
  const [framed, setFramed] = useState(false);
  useEffect(() => {
    const on = () => setFramed(mode === "mobile" && prefs.ui === "mobile" && window.self === window.top && window.innerWidth >= 640);
    on();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, [mode, prefs.ui]);
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

  const active = (href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(href + "/"));
  const content = error ? (
    <div role="alert" className="rounded-[16px] border-[1.5px] border-danger bg-danger-soft p-4 text-danger">
      Cannot reach the SushrutAI server ({error}). Start it with: python main.py
    </div>
  ) : ready ? children : (
    <div className="space-y-3" aria-busy="true"><div className="skeleton h-28" /><div className="skeleton h-40" /></div>
  );
  if (mode === null) return <div className="min-h-dvh bg-bg" aria-busy="true" />;
  if (framed) return <MobileFrame />;
  if (mode === "desktop") {
    return (
      <>
        <DesktopShell nav={nav.map((n) => ({ href: n.href, label: t[n.key], icon: n.icon }))} active={active} online={online}
          localOnly={localOnly} logo={<LogoMark className="h-12 w-12" />}>{content}</DesktopShell>
        <DoseNotifier />
      </>
    );
  }
  return (
    <div className="app-grid min-h-dvh print:block">
      <a href="#main" className="sr-only z-50 rounded-[14px] bg-brand px-4 py-3 font-semibold text-brand-ink focus:not-sr-only focus:fixed focus:start-3 focus:top-3">
        {t.skipToContent}
      </a>

      {/* Sidebar (tablet: icon rail; desktop: full or rail, user's choice): the gunmetal handle of the carrier */}
      <aside className="sidebar relative hidden print:!hidden bg-rail text-rail-text md:sticky md:top-0 md:flex md:h-dvh md:flex-col md:py-5">
        <div className="sb-head mb-8 flex items-center gap-2">
          <Link href="/" className="flex min-w-0 flex-1 items-center gap-3 rounded-[14px]" aria-label="SushrutAI home">
            <LogoMark className="h-12 w-12" />
            <span className="sb-label min-w-0"><span className="display block text-[1.375rem] font-extrabold leading-tight">SushrutAI</span>
              <span className="block text-[0.8125rem] leading-snug text-rail-muted">{t.appTagline}</span></span>
          </Link>
        </div>
        <nav aria-label="Main">
          <ul className="space-y-1">
            {nav.map((n) => {
              const on = active(n.href);
              return (
                <li key={n.href}>
                  <Link href={n.href} aria-current={on ? "page" : undefined} title={t[n.key]}
                    className={`sb-link group flex min-h-14 items-center gap-3 rounded-[16px] text-[1.0625rem] font-semibold
                      ${on ? "text-rail-text" : "text-rail-muted hover:bg-rail-2 hover:text-rail-text"}`}>
                    <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-full transition-colors
                      ${on ? "bg-rail-on text-rail-on-ink shadow-[inset_0_-2px_0_rgb(0_0_0/18%)]" : "bg-rail-2 shadow-[inset_0_2px_4px_rgb(0_0_0/30%)]"}`}>
                      <n.icon aria-hidden className="h-[1.375rem] w-[1.375rem]" strokeWidth={on ? 2.4 : 2} />
                    </span>
                    <span className={`sb-label ${on ? "font-bold" : ""}`}>{t[n.key]}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="mt-auto space-y-3">
          <div className="sb-label space-y-2 px-1">
            <PrivacyBadge local={localOnly} />
            <p className="font-mono text-[0.875rem] leading-relaxed text-rail-muted">108 · 112<br />Tele-MANAS 14416</p>
          </div>
          <button type="button" onClick={toggleRail} aria-expanded={!rail} aria-label={rail ? t.expandSidebar : t.collapseSidebar}
            title={`${rail ? t.expandSidebar : t.collapseSidebar} ( [ )`}
            className="sb-toggle hidden min-h-12 w-full items-center gap-2 rounded-[14px] px-2 font-semibold text-rail-muted hover:bg-rail-2 hover:text-rail-text lg:flex">
            {rail ? <PanelLeftOpen aria-hidden className="h-6 w-6 shrink-0" /> : <PanelLeftClose aria-hidden className="h-6 w-6 shrink-0" />}
            <span className="sb-label text-[0.9375rem]">{t.collapseSidebar}</span>
          </button>
        </div>
      </aside>

      <div className="flex min-h-dvh min-w-0 flex-col">
        {/* Header: gunmetal, with the family tiers hanging from it */}
        <header className="sticky top-0 z-20 print:hidden bg-rail text-rail-text">
          <div className="mx-auto flex max-w-[1200px] items-center gap-2 px-3 py-2 sm:px-4 lg:px-8">
            <Link href="/" className="flex min-h-12 min-w-12 shrink-0 items-center gap-2 rounded-[14px] md:hidden" aria-label="SushrutAI home">
              <LogoMark className="h-10 w-10" />
              <span className="display truncate text-[1.25rem] font-extrabold max-[479px]:sr-only">SushrutAI</span>
            </Link>
            <SearchButton />
            <div className="flex-1" />
            <ProfileSwitcher />
            <LanguageButton />
            <SOSButton />
          </div>
          <div className="mx-auto max-w-[1200px]"><FamilyTiers /></div>
          <ProfileBanner />
          {!online && (
            <p role="status" className="flex items-center justify-center gap-2 bg-warn-soft px-4 py-1.5 text-[0.9375rem] font-semibold text-warn">
              <WifiOff aria-hidden className="h-4 w-4" />{t.youreOffline}
            </p>
          )}
        </header>

        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-[1200px] flex-1 px-4 pb-[calc(9rem+env(safe-area-inset-bottom))] pt-5 md:px-6 md:pb-12 lg:px-8 lg:pt-8 print:p-0">
          {content}
        </main>
      </div>

      {/* Mobile bottom tab bar: the current place is a filled jamun katori */}
      <nav aria-label="Main" className="bottom-nav fixed inset-x-0 bottom-0 z-20 print:hidden border-t border-border bg-surface pb-safe shadow-[0_-8px_24px_-16px_rgb(24_32_40/40%)] md:hidden">
        <ul className="mx-auto flex max-w-xl">
          {nav.map((n) => {
            const on = active(n.href);
            return (
              <li key={n.href} className="min-w-0 flex-1">
                <Link href={n.href} aria-current={on ? "page" : undefined}
                  className={`flex min-h-16 flex-col items-center justify-center gap-0.5 px-0 pb-1 pt-1 text-[0.78rem]
                    ${on ? "font-bold text-brand" : "font-semibold text-muted"}`}>
                  <span className={`grid h-9 w-9 place-items-center rounded-full ${on ? "bg-brand text-brand-ink" : ""}`}>
                    <n.icon aria-hidden className="h-[1.375rem] w-[1.375rem]" strokeWidth={on ? 2.4 : 2} />
                  </span>
                  <span className="max-w-full text-center leading-tight [overflow-wrap:anywhere]">{t[n.key]}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>
      <div className="print:hidden"><FloatingMic /></div>
      <DoseNotifier />
      <ServiceAlert />
    </div>
  );
}

/** The SushrutAI mark (the app logo: a health cross with leaves and a molecule). Sized by className; the 96px
 *  rendition covers every place it appears (largest 56px, sharp on 2x screens). */
export function LogoMark({ className = "" }: { className?: string }) {
  return <img src="/icons/logo-96.png" alt="" aria-hidden width={96} height={96} className={`shrink-0 object-contain ${className}`} />;
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <PrefsProvider>
      <I18nProvider>
        <EmergencyProvider>
          <VoiceLoopProvider><LiveTalkProvider><Inner>{children}</Inner></LiveTalkProvider></VoiceLoopProvider>
        </EmergencyProvider>
      </I18nProvider>
    </PrefsProvider>
  );
}
