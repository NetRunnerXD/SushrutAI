"use client";

import { BellRing, CalendarPlus, Download, Gauge, HelpCircle, Languages, Lock, MonitorSmartphone, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { LanguageGrid } from "@/components/LanguagePicker";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader, PageTitle } from "@/components/ui/Card";
import { Chip } from "@/components/ui/Medical";
import { Banner } from "@/components/ui/Trust";
import { api, downloadFile, setTokens } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { usePrefs } from "@/lib/prefs";

type Reminder = { medication_id: string; name: string; slot: string; time: string | null; timing: string | null };

function Toggle({ label, help, checked, onChange, disabled }: { label: string; help?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="flex min-h-14 cursor-pointer items-start justify-between gap-4 py-2">
      <span><span className="block font-semibold">{label}</span>{help && <span className="block text-[0.9375rem] text-muted">{help}</span>}</span>
      <input type="checkbox" role="switch" aria-checked={checked} checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)}
        className="mt-1 h-7 w-12 shrink-0 cursor-pointer appearance-none rounded-full bg-border-input transition-colors before:block before:h-6 before:w-6
          before:translate-x-0.5 before:rounded-full before:bg-white before:transition-transform checked:bg-brand checked:before:translate-x-[22px]" />
    </label>
  );
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return <div role="radiogroup" aria-label={label}><p className="mb-1.5 font-semibold">{label}</p><div className="flex flex-wrap gap-2">{children}</div></div>;
}


const CONSENT_LABEL: Record<string, string> = {
  history_storage: "Save my chats, prescriptions and reports", cloud_speech: "Use India-hosted voice services when needed",
  cloud_llm_deidentified: "Send de-identified hard questions to a cloud AI", model_improvement: "Help improve prescription reading",
};

export default function SettingsPage() {
  const { t } = useI18n();
  const { prefs, setPrefs } = usePrefs();
  const [s, setS] = useState<any>(null);
  const [rems, setRems] = useState<Reminder[]>([]);
  const [consents, setConsents] = useState<string[]>([]);
  const [notify, setNotify] = useState(false);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    api("/me/settings").then(setS);
    api<Reminder[]>("/me/reminders").then(setRems).catch(() => {});
    api<any>("/consents").then((c) => setConsents(c.active));
    return () => timers.current.forEach(clearTimeout);
  }, []);

  const update = async (patch: any) => setS(await api("/me/settings", { method: "PUT", body: JSON.stringify(patch) }));
  async function toggleConsent(purpose: string, on: boolean) {
    const r = on ? await api<any>("/consents", { method: "POST", body: JSON.stringify({ purposes: [purpose] }) })
                 : await api<any>(`/consents/${purpose}`, { method: "DELETE" });
    setConsents(r.active);
  }
  async function enableNotify() {
    if (typeof Notification === "undefined" || (await Notification.requestPermission()) !== "granted") return;
    timers.current.forEach(clearTimeout);
    const now = new Date();
    timers.current = rems.filter((r) => r.time).map((r) => {
      const [h, m] = r.time!.split(":").map(Number);
      const ms = new Date(now.getFullYear(), now.getMonth(), now.getDate(), h, m).getTime() - now.getTime();
      return ms > 0 ? window.setTimeout(() => new Notification("SushrutAI", { body: `${r.name}`, tag: r.medication_id + r.slot }), ms) : 0;
    }).filter(Boolean);
    setNotify(true);
  }
  async function erase() {
    if (!confirm(t.deleteConfirm)) return;
    await api("/me", { method: "DELETE" });
    setTokens(null);
    try { localStorage.clear(); } catch { /* ignore */ }
    location.href = "/welcome";
  }

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <PageTitle>{t.settingsTitle}</PageTitle>

      <Card>
        <CardHeader icon={Languages} title={t.langVoice} />
        <LanguageGrid />
        {s && <Toggle label="Read answers aloud automatically" help="When you ask by voice, the first sentence of the answer is spoken."
                      checked={s.voice_autoplay} onChange={(v) => update({ voice_autoplay: v })} />}
        <div className="pt-2"><Group label={t.speechSpeed}>
          {([["slow", t.speedSlow], ["normal", t.speedNormal], ["fast", t.speedFast]] as const).map(([v, l]) => (
            <Chip key={v} selected={prefs.rate === v} onClick={() => setPrefs({ rate: v })}>{l}</Chip>
          ))}
        </Group></div>
      </Card>

      <Card className="space-y-4">
        <CardHeader icon={MonitorSmartphone} title={t.display} />
        <Toggle label={t.simpleMode} help={t.simpleModeHelp} checked={prefs.simple} onChange={(v) => setPrefs({ simple: v })} />
        <Group label={t.textSize}>
          {([["md", "A"], ["lg", "A+"], ["xl", "A++"]] as const).map(([v, l]) => (
            <Chip key={v} selected={prefs.text === v} onClick={() => setPrefs({ text: v })} className={v === "md" ? "" : v === "lg" ? "text-[1.15rem]" : "text-[1.3rem]"}>{l}</Chip>
          ))}
        </Group>
        <Group label={t.theme}>
          {([["auto", t.themeAuto], ["light", t.themeLight], ["dark", t.themeDark]] as const).map(([v, l]) => (
            <Chip key={v} selected={prefs.theme === v} onClick={() => setPrefs({ theme: v })}>{l}</Chip>
          ))}
        </Group>
        <Toggle label={t.highContrast} help="Black text and borders for low vision." checked={prefs.contrast === "high"} onChange={(v) => setPrefs({ contrast: v ? "high" : "normal" })} />
        <Toggle label={t.reduceMotion} help="Turns off pulsing and sliding animations." checked={prefs.motion === "reduce"} onChange={(v) => setPrefs({ motion: v ? "reduce" : "auto" })} />
      </Card>

      <Card id="reminders" className="space-y-3">
        <CardHeader icon={BellRing} title={t.reminders} />
        {rems.length === 0 ? <p className="text-muted">{t.noReminders}</p> : (
          <ul className="divide-y divide-border">{rems.map((r) => (
            <li key={r.medication_id + r.slot} className="flex justify-between py-2"><span>{r.name}</span><span className="font-mono font-semibold">{r.time ?? "SOS"}</span></li>))}</ul>
        )}
        {s && (
          <div className="grid gap-2 sm:grid-cols-3">
            {(["morning", "afternoon", "night"] as const).map((slot) => (
              <label key={slot} className="font-semibold">{t[slot]}
                <input type="time" className="input mt-1 font-mono" value={s.reminder_times?.[slot] ?? ""} onChange={(e) => update({ reminder_times: { [slot]: e.target.value } })} /></label>
            ))}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button icon={CalendarPlus} disabled={!rems.length} onClick={() => downloadFile("/me/reminders.ics", "sushrutai-reminders.ics")}>{t.remindersCalendar}</Button>
          <Button variant="secondary" icon={BellRing} disabled={!rems.length || notify} onClick={enableNotify}>{notify ? "✓" : t.remindersNotify}</Button>
        </div>
      </Card>

      {s && (
        <Card>
          <CardHeader icon={Gauge} title={t.answerSpeed} sub={t.answerSpeedHelp} />
          <div role="radiogroup" aria-label={t.answerSpeed} className="grid gap-2 sm:grid-cols-3">
            {([["balanced", t.modelBalanced, t.modelBalancedHelp], ["fast", t.modelFast, t.modelFastHelp],
               ["cloud", t.modelCloud, t.modelCloudHelp]] as const)
              .filter(([k]) => (s.model_options ?? ["balanced"]).includes(k))
              .map(([k, label, help]) => {
                const cloudBlocked = k === "cloud" && (s.offline_mode || !consents.includes("cloud_llm_deidentified"));
                const on = (s.model_pref ?? "balanced") === k;
                return (
                  <button key={k} type="button" role="radio" aria-checked={on} disabled={cloudBlocked}
                    onClick={() => update({ model_pref: k })}
                    className={`flex min-h-24 flex-col items-start gap-1 rounded-[6px] border-2 p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60
                      ${on ? "border-brand bg-accent-soft" : "border-border bg-surface hover:border-border-input"}`}>
                    <span className="serif text-[1.125rem] font-bold">{label}</span>
                    <span className="text-[0.875rem] leading-snug text-muted">{cloudBlocked ? t.modelCloudNeeds : help}</span>
                  </button>
                );
              })}
          </div>
        </Card>
      )}

      <Card className="space-y-1">
        <CardHeader icon={Lock} title={t.privacy} />
        {s && <Toggle label={t.offlineMode} help={t.offlineModeHelp} checked={s.offline_mode} disabled={s.offline_forced_by_server} onChange={(v) => update({ offline_mode: v })} />}
        {Object.entries(CONSENT_LABEL).map(([k, l]) => <Toggle key={k} label={l} checked={consents.includes(k)} onChange={(v) => toggleConsent(k, v)} />)}
        <div className="flex flex-wrap gap-2 pt-3">
          <Button variant="secondary" icon={Download} onClick={() => downloadFile("/me/export", "sushrutai-my-data.json")}>{t.exportData}</Button>
          <Button variant="danger" icon={Trash2} onClick={erase}>{t.deleteData}</Button>
        </div>
      </Card>

      <Card className="space-y-3">
        <CardHeader icon={HelpCircle} title={t.help} />
        <Banner tone="info" title={t.aboutAi}>
          <ul className="list-disc space-y-1 pl-5">
            <li>The assistant runs on small AI models on this device. It can be wrong — it is not a doctor.</li>
            <li>It reads about 9 in 10 printed lab-report values correctly, and about half of handwritten prescriptions — always check.</li>
            <li>Emergency words (like chest pain or difficulty breathing) are detected by fixed rules, before the AI answers.</li>
            <li>For X-rays it only describes what it sees; a radiologist must review every image.</li>
          </ul>
        </Banner>
        <p className="font-semibold">Ambulance 108 · Emergency 112 · Tele-MANAS 14416 · eSanjeevani (free teleconsultation)</p>
      </Card>
    </div>
  );
}
