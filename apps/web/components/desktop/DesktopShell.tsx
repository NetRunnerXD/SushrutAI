"use client";

import { Pin, PinOff, Settings, WifiOff, X, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Avatar, ProfileBanner, useActiveProfile, useFamily } from "@/components/Family";
import { SOSButton } from "@/components/Emergency";
import { LanguageButton } from "@/components/LanguagePicker";
import { SearchButton } from "@/components/Search";
import { PrivacyBadge } from "@/components/ui/Trust";
import { FloatingMic } from "@/components/Voice";
import { switchProfile } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";

export type NavItem = { href: string; label: string; icon: LucideIcon };

/** Desktop shell (docs/DESKTOP_UI_SPEC.md §3): an ambient field, one glass frame, a floating pill icon rail that expands
 *  to a labelled menu (hover-intent or pinned with "["), and a top bar with search, voice, language, family and SOS. */
export function DesktopShell({ nav, active, online, localOnly, logo, children }: {
  nav: NavItem[]; active: (href: string) => boolean; online: boolean; localOnly: boolean; logo: ReactNode; children: ReactNode;
}) {
  const { t } = useI18n();
  const { prefs, setPrefs } = usePrefs();
  const path = usePathname();
  const pinned = prefs.deskRail === "pinned";
  const [hover, setHover] = useState(false);
  const timer = useRef<number | null>(null);
  const [squeezeNote, setSqueezeNote] = useState(false);
  useEffect(() => { setSqueezeNote(document.documentElement.hasAttribute("data-ui-squeeze")); }, []);
  const expanded = pinned || hover;
  const enter = () => { if (!pinned) timer.current = window.setTimeout(() => setHover(true), 400); };
  const leave = () => { if (timer.current) clearTimeout(timer.current); setHover(false); };
  // "[" pins / unpins the labelled menu (not while typing)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (e.key !== "[" || e.ctrlKey || e.metaKey || e.altKey) return;
      if (el?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el?.tagName)) return;
      setPrefs({ deskRail: document.documentElement.dataset.deskRail === "pinned" ? "icons" : "pinned" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPrefs]);
  useEffect(() => { document.documentElement.dataset.deskRail = prefs.deskRail; }, [prefs.deskRail]);

  return (
    <>
      <div aria-hidden className="d-backdrop" />
      <a href="#main" className="sr-only z-50 rounded-[14px] bg-brand px-4 py-3 font-semibold text-brand-ink focus:not-sr-only focus:fixed focus:start-3 focus:top-3">
        {t.skipToContent}</a>
      <div className="d-frame relative mx-4 my-4 flex min-h-[calc(100dvh-2rem)] min-[1600px]:mx-5 min-[1600px]:my-5">
        {/* rail column: 108px for the floating pill; 280px when the labelled menu is pinned (it then pushes the content) */}
        <div className={`relative shrink-0 transition-[width] duration-[280ms] ease-[var(--ease-out)] ${pinned ? "w-[280px] max-[1279px]:w-[108px]" : "w-[108px]"}`}>
          <aside aria-label="Main" onMouseEnter={enter} onMouseLeave={leave} onFocus={() => setHover(true)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setHover(false); }}
            className={`sticky top-4 z-30 ms-4 flex h-[calc(100dvh-4rem)] max-h-[880px] flex-col rounded-[38px] border border-[var(--d-tile-line)] py-4 transition-[width,background-color,box-shadow] duration-[280ms] ease-[var(--ease-out)]
              ${expanded ? "w-[248px] bg-[var(--d-pop)] shadow-[var(--e3)]" : "w-[76px] bg-[var(--d-tile)]"}`}>
            <Link href="/" aria-label="SushrutAI home" className="mx-auto mb-5 flex items-center gap-3 self-stretch px-3.5">
              {logo}
              {expanded && <span className="d-rise display truncate text-[1.25rem] font-extrabold">SushrutAI</span>}
            </Link>
            <nav aria-label="Main" className="flex-1">
              <ul className="space-y-1.5 px-3.5">
                {nav.map((n) => {
                  const on = active(n.href);
                  return (
                    <li key={n.href} className="relative">
                      {on && <span aria-hidden className="d-notch" />}
                      <Link href={n.href} aria-current={on ? "page" : undefined} aria-label={n.label} title={expanded ? undefined : n.label}
                        className={`d-press flex h-12 items-center gap-3 rounded-full font-semibold transition-colors duration-[120ms]
                          ${expanded ? "px-0.5 pe-4" : "justify-center"} ${on ? "text-text" : "text-muted hover:bg-[var(--d-tile-hover)] hover:text-text"}`}>
                        <span className={`grid h-12 w-12 shrink-0 place-items-center rounded-full ${on ? "bg-brand text-brand-ink shadow-[0_6px_18px_-6px_var(--d-accent-500)]" : ""}`}>
                          <n.icon aria-hidden className="h-[22px] w-[22px]" strokeWidth={on ? 2.3 : 2} />
                        </span>
                        {expanded && <span className="d-rise truncate">{n.label}</span>}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </nav>
            <div className="space-y-1.5 px-3.5">
              {/* always mounted: it fades in after the rail has opened (no wrapping while the width animates) and
                  fades out first when it closes */}
              <div aria-hidden={!expanded} className={`overflow-hidden whitespace-nowrap px-1 transition-[opacity,max-height,transform] ease-[var(--ease-out)]
                ${expanded ? "max-h-24 translate-y-0 pb-2 opacity-100 duration-[240ms] delay-[140ms]" : "pointer-events-none max-h-0 translate-y-1 opacity-0 duration-[120ms]"}`}>
                <div className="space-y-2">
                  <PrivacyBadge local={localOnly} />
                  <p className="font-mono text-[0.8125rem] leading-relaxed text-muted">108 · 112 · Tele-MANAS 14416</p>
                </div>
              </div>
              <button type="button" onClick={() => setPrefs({ deskRail: pinned ? "icons" : "pinned" })} aria-pressed={pinned}
                aria-label={pinned ? t.collapseSidebar : t.expandSidebar} title={`${pinned ? t.collapseSidebar : t.expandSidebar} ( [ )`}
                className={`flex h-12 w-full items-center gap-3 rounded-full text-muted hover:bg-[var(--d-tile-hover)] hover:text-text ${expanded ? "px-0.5" : "justify-center"}`}>
                <span className="grid h-12 w-12 shrink-0 place-items-center">{pinned ? <PinOff aria-hidden className="h-5 w-5" /> : <Pin aria-hidden className="h-5 w-5" />}</span>
                {expanded && <span className="truncate text-[0.9375rem] font-semibold">{pinned ? t.collapseSidebar : t.expandSidebar}</span>}
              </button>
              <Link href="/me/settings" aria-label={t.settingsTitle} title={expanded ? undefined : t.settingsTitle}
                className={`flex h-12 items-center gap-3 rounded-full text-muted hover:bg-[var(--d-tile-hover)] hover:text-text ${expanded ? "px-0.5" : "justify-center"}`}>
                <span className="grid h-12 w-12 shrink-0 place-items-center"><Settings aria-hidden className="h-[22px] w-[22px]" /></span>
                {expanded && <span className="truncate font-semibold">{t.settingsTitle}</span>}
              </Link>
              <RailAvatar expanded={expanded} />
            </div>
          </aside>
        </div>

        <div className="flex min-w-0 flex-1 flex-col pe-4">
          <header className="sticky top-4 z-20 mb-2 flex h-[72px] items-center gap-3 rounded-[22px] bg-[var(--d-frame)] px-4 backdrop-blur-xl print:hidden">
            <div className="flex min-w-0 flex-1 justify-center"><SearchButton field /></div>
            <FloatingMic inline />
            <LanguageButton />
            <FamilyAvatars />
            <SOSButton />
          </header>
          <ProfileBanner />
          {squeezeNote && (
            <p role="status" className="mx-4 mb-2 flex items-center gap-2 rounded-[14px] border border-[var(--d-tile-line)] bg-info-soft px-4 py-2 text-[0.9375rem] font-semibold">
              <span className="flex-1">{t.squeezeNote}</span>
              <button type="button" onClick={() => setPrefs({ ui: "mobile" })} className="underline">{t.useMobileLayout}</button>
              <button type="button" aria-label={t.close} onClick={() => setSqueezeNote(false)} className="grid h-9 w-9 place-items-center rounded-full"><X aria-hidden className="h-4 w-4" /></button>
            </p>
          )}
          {!online && (
            <p role="status" className="mx-4 mb-2 flex items-center justify-center gap-2 rounded-[14px] bg-warn-soft px-4 py-1.5 font-semibold text-warn">
              <WifiOff aria-hidden className="h-4 w-4" />{t.youreOffline}</p>
          )}
          <main id="main" tabIndex={-1} className="d-page mx-auto w-full max-w-[1680px] flex-1 px-4 pb-12 pt-2 min-[1280px]:px-6 print:p-0">
            <div key={path} className="d-rise">{children}</div>
          </main>
        </div>
      </div>
    </>
  );
}

/** The household as avatars in the top bar: the open record is ringed; one click switches. */
function FamilyAvatars() {
  const { members } = useFamily();
  const who = useActiveProfile();
  const { t } = useI18n();
  if (!members?.length) return null;
  return (
    <div role="group" aria-label={t.whoseHealth} className="flex items-center -space-x-1.5">
      {[null, ...members].slice(0, 5).map((m) => {
        const current = m ? who?.id === m.id : !who;
        return (
          <button key={m?.id ?? "me"} type="button" aria-pressed={current} title={m ? m.name : t.myself}
            onClick={() => !current && switchProfile(m && { id: m.id, name: m.name, relation: m.relation }, location.pathname)}
            className={`rounded-full transition-transform duration-[120ms] hover:z-10 hover:-translate-y-0.5 ${current ? "z-10 ring-2 ring-[var(--d-accent-500)] ring-offset-2 ring-offset-[var(--bg)]" : ""}`}>
            <Avatar name={m?.name} id={m?.id} size={36} />
          </button>
        );
      })}
    </div>
  );
}

function RailAvatar({ expanded }: { expanded: boolean }) {
  const who = useActiveProfile();
  const { t } = useI18n();
  return (
    <Link href="/me" aria-label={who ? who.name : t.myself} className={`flex h-14 items-center gap-3 rounded-full hover:bg-[var(--d-tile-hover)] ${expanded ? "px-1" : "justify-center"}`}>
      <Avatar name={who?.name} id={who?.id} size={44} />
      {expanded && <span className="min-w-0 truncate font-semibold">{who ? who.name : t.myself}</span>}
    </Link>
  );
}
