"use client";

import { Bell, Download, Network, Search, ShieldCheck, Sparkles, Trash2, Users } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { api, downloadFile, switchProfile } from "@/lib/api";
import { flushOfflineQueue, getOfflineSyncStatus } from "@/lib/offlineSync";

export function FamilyNetworkPanel() {
  const [graph, setGraph] = useState<any>(null);
  const [members, setMembers] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api<any[]>("/family").then(setMembers).catch(() => {});
    api<any>("/family/graph").then(setGraph).catch((e) => setErr(e?.message || "Unable to load family graph"));
  }, []);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader icon={Users} title="Family Member Profiles & Switching" level={2} />
        <p className="mb-3 text-[0.9375rem] text-muted">
          Switch profiles to view or record health data for a specific family member. Each member&apos;s data is encrypted separately.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => switchProfile(null, "/records?tab=family")}>
            Self (Account Holder)
          </Button>
          {members.map((m) => (
            <Button
              key={m.id}
              variant="secondary"
              onClick={() => switchProfile({ id: m.id, name: m.name, relation: m.relation }, "/records?tab=family")}
            >
              {m.name} ({m.relation}{m.age != null ? `, ${m.age}y` : ""})
            </Button>
          ))}
          <Link href="/me/family" className="btn btn-outline">Manage Family & Permissions</Link>
        </div>
      </Card>

      <Card>
        <CardHeader icon={Network} title="Family Health Knowledge Graph" level={2} />
        {err && <p className="text-[0.875rem] text-muted">{err}</p>}
        {graph && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-3 text-[0.875rem]">
              <span className="rounded-full bg-surface-2 px-3 py-1 font-semibold">Nodes: {graph.stats?.node_count ?? 0}</span>
              <span className="rounded-full bg-surface-2 px-3 py-1 font-semibold">Edges: {graph.stats?.edge_count ?? 0}</span>
              <span className="rounded-full bg-surface-2 px-3 py-1 font-semibold">Members: {graph.stats?.family_members ?? 0}</span>
            </div>
            {(graph.familial_patterns || []).length > 0 ? (
              <ul className="space-y-1.5 text-[0.9375rem]">
                {graph.familial_patterns.map((p: any, i: number) => (
                  <li key={i} className="rounded-[12px] bg-surface-2 p-2.5">
                    <span className="font-semibold">{p.source_member} ({p.relationship})</span> → {p.reported_condition}
                    <span className="block text-[0.8125rem] text-muted">{p.clinical_interpretation}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[0.9375rem] text-muted">No family medical patterns recorded yet.</p>
            )}
            <p className="text-[0.8125rem] text-muted">{graph.gnn_status?.architecture}: {graph.gnn_status?.note}</p>
          </div>
        )}
      </Card>
    </div>
  );
}

export function InsightsAndSummariesPanel() {
  const [summaries, setSummaries] = useState<any[]>([]);
  const [query, setQuery] = useState("");
  const [plan, setPlan] = useState<any>(null);
  const [consents, setConsents] = useState<string[]>([]);
  const [syncState, setSyncState] = useState(() => getOfflineSyncStatus());

  const loadSummaries = (q = "") => {
    api<{ summaries: any[] }>(`/memory/summaries?q=${encodeURIComponent(q)}`)
      .then((r) => setSummaries(r.summaries || []))
      .catch(() => setSummaries([]));
  };

  useEffect(() => {
    loadSummaries();
    api<any>("/followup/plan").then(setPlan).catch(() => {});
    api<{ active: string[] }>("/consents").then((r) => setConsents(r.active || [])).catch(() => {});
  }, []);

  const deleteSummary = async (encounterId: string) => {
    await api(`/memory/summaries/${encounterId}`, { method: "DELETE" });
    loadSummaries(query);
  };

  const syncNow = async () => {
    try {
      const next = await flushOfflineQueue();
      setSyncState(next);
    } catch { /* ignore */ }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader icon={Download} title="Doctor-Friendly PDF Report & Sync Status" level={2} />
        <div className="flex flex-wrap items-center gap-3">
          <Button icon={Download} onClick={() => downloadFile("/records/report.pdf", "sushrutai-medical-report.pdf")}>
            Download Doctor PDF Report
          </Button>
          <span className="rounded-full bg-surface-2 px-3 py-1.5 text-[0.875rem] font-semibold">
            Sync: {syncState.state === "unsynced_pending" ? `${syncState.unsyncedCount} unsynced offline items` : "All records synced"}
          </span>
          {syncState.unsyncedCount > 0 && (
            <Button variant="secondary" onClick={syncNow}>Sync Now</Button>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader icon={Bell} title="AI Health Insights & Proactive Follow-Ups" level={2} />
        {(plan?.items || []).length > 0 ? (
          <ul className="space-y-2">
            {plan.items.map((it: any) => (
              <li key={it.id} className="rounded-[12px] bg-surface-2 p-3 text-[0.9375rem]">
                <span className="font-semibold">{it.title}</span>
                <span className="ml-2 text-[0.75rem] uppercase text-muted">[{it.priority}]</span>
                <p className="text-muted">{it.message}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[0.9375rem] text-muted">No pending follow-ups or overdue reminders right now.</p>
        )}
      </Card>

      <Card>
        <CardHeader icon={Sparkles} title="Automatic Conversation Summaries" level={2} />
        <div className="mb-3 flex gap-2">
          <input
            className="input flex-1"
            placeholder="Search conversations by symptom, specialist, or complaint..."
            value={query}
            onChange={(e) => { setQuery(e.target.value); loadSummaries(e.target.value); }}
          />
          <Button variant="secondary" icon={Search} onClick={() => loadSummaries(query)}>Search</Button>
        </div>
        {summaries.length === 0 ? (
          <p className="text-[0.9375rem] text-muted">No saved conversation summaries found.</p>
        ) : (
          <ul className="space-y-2.5">
            {summaries.map((s) => (
              <li key={s.encounter_id} className="rounded-[12px] border border-border bg-surface-2 p-3 text-[0.9375rem]">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold">{s.main_complaint}</p>
                    <p className="text-[0.8125rem] text-muted">
                      {s.date_and_time} · Channel: {s.channel} · Specialist: {s.suggested_specialist || "Pending"}
                    </p>
                  </div>
                  <button
                    type="button"
                    aria-label="Delete summary"
                    onClick={() => deleteSummary(s.encounter_id)}
                    className="rounded-full p-2 text-muted hover:bg-danger-soft hover:text-danger"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
                {(s.symptoms || []).length > 0 && (
                  <p className="mt-1 text-[0.875rem]">Symptoms: {s.symptoms.join(", ")}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader icon={ShieldCheck} title="Privacy & Active Consents" level={2} />
        <p className="text-[0.9375rem] text-muted">
          Active consents: {consents.length ? consents.join(", ") : "None"}.{" "}
          <Link href="/me/settings" className="underline">Manage privacy, data export & erasure</Link>.
        </p>
      </Card>
    </div>
  );
}
