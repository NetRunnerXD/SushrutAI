"use client";

import { api } from "./api";

export type OfflineMutation = {
  client_op_id: string;
  kind: "vital" | "symptom_note" | "followup_ack";
  member_id?: string | null;
  client_timestamp: string;
  payload: Record<string, unknown>;
};

export type SyncStatusSnapshot = {
  online: boolean;
  unsyncedCount: number;
  lastSyncedAt: string | null;
  lastConflictCount: number;
  state: "synced" | "unsynced_pending" | "conflict_resolved";
};

const QUEUE_KEY = "sushrut.offlineQueue.v1";
const META_KEY = "sushrut.offlineSyncMeta.v1";

function readQueue(): OfflineMutation[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]");
  } catch {
    return [];
  }
}

function writeQueue(q: OfflineMutation[]) {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
  } catch { /* ignore quota errors */ }
}

export function getOfflineSyncStatus(): SyncStatusSnapshot {
  const q = readQueue();
  const online = typeof navigator !== "undefined" ? navigator.onLine : true;
  let meta = { lastSyncedAt: null as string | null, lastConflictCount: 0 };
  if (typeof window !== "undefined") {
    try {
      meta = { ...meta, ...JSON.parse(localStorage.getItem(META_KEY) || "{}") };
    } catch { /* ignore */ }
  }
  return {
    online,
    unsyncedCount: q.length,
    lastSyncedAt: meta.lastSyncedAt,
    lastConflictCount: meta.lastConflictCount,
    state: q.length > 0 ? "unsynced_pending" : meta.lastConflictCount > 0 ? "conflict_resolved" : "synced",
  };
}

export function queueOfflineMutation(m: Omit<OfflineMutation, "client_op_id" | "client_timestamp">): SyncStatusSnapshot {
  const q = readQueue();
  q.push({
    ...m,
    client_op_id: `op_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    client_timestamp: new Date().toISOString(),
  });
  writeQueue(q);
  return getOfflineSyncStatus();
}

export async function flushOfflineQueue(): Promise<SyncStatusSnapshot> {
  const q = readQueue();
  if (!q.length) return getOfflineSyncStatus();
  if (typeof navigator !== "undefined" && !navigator.onLine) {
    return getOfflineSyncStatus();
  }
  const resp = await api<{ synced_count: number; conflict_count: number }>("/offline/sync", {
    method: "POST",
    body: JSON.stringify({ mutations: q }),
  });
  writeQueue([]);
  if (typeof window !== "undefined") {
    try {
      localStorage.setItem(
        META_KEY,
        JSON.stringify({ lastSyncedAt: new Date().toISOString(), lastConflictCount: resp.conflict_count ?? 0 })
      );
    } catch { /* ignore */ }
  }
  return getOfflineSyncStatus();
}
