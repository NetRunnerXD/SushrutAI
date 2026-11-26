"use client";

import { api } from "./api";

export type ConversationState =
  | "IDLE"
  | "LISTENING"
  | "TRANSCRIBING"
  | "PAUSE_DETECTED"
  | "WAITING_FOR_CONTINUATION"
  | "TURN_FINALIZING"
  | "PROCESSING"
  | "GENERATING"
  | "SPEAKING"
  | "BARGE_IN"
  | "CLARIFICATION"
  | "SESSION_PAUSED"
  | "SESSION_ENDED";

export type SilenceConfig = {
  pauseDetectMs: number;        // 800-1200ms hesitation pause
  continuationGraceMs: number;  // 3000-8000ms hold turn open while thinking
  gentleAckMs: number;          // 10000-15000ms optional calm reassurance
  sessionPauseMs: number;       // 45000-60000ms long quiet pause -> SESSION_PAUSED
};

export const DEFAULT_SILENCE_CONFIG: SilenceConfig = {
  pauseDetectMs: 1000,
  continuationGraceMs: 4500,
  gentleAckMs: 12000,
  sessionPauseMs: 50000,
};

/**
 * Coordinates frontend conversation state, multi-fragment turn merging,
 * generation IDs, cancellation tokens, and server sync without fragmented UI races.
 */
export class FrontendConversationOrchestrator {
  state: ConversationState = "IDLE";
  generationSeq = 0;
  generationId = "gen_0";
  openFragments: string[] = [];
  partialText = "";
  spokenPrefix = "";
  abortController: AbortController | null = null;
  sessionId: string | null = null;

  bindSession(sid: string | null) {
    this.sessionId = sid;
  }

  async setState(next: ConversationState, reason = ""): Promise<ConversationState> {
    this.state = next;
    if (this.sessionId) {
      api(`/conversation/sessions/${this.sessionId}/state`, {
        method: "POST",
        body: JSON.stringify({ state: next, reason }),
      }).catch(() => {});
    }
    return this.state;
  }

  appendFragment(fragment: string): string {
    const clean = fragment.trim();
    if (!clean) return this.mergedText();
    const prev = this.mergedText();
    if (prev && clean.toLowerCase().startsWith(prev.toLowerCase())) {
      this.openFragments = [clean];
    } else if (!prev.toLowerCase().endsWith(clean.toLowerCase())) {
      this.openFragments.push(clean);
    }
    const merged = this.mergedText();
    if (this.sessionId) {
      api(`/conversation/sessions/${this.sessionId}/fragment`, {
        method: "POST",
        body: JSON.stringify({ text: clean }),
      }).catch(() => {});
    }
    return merged;
  }

  mergedText(): string {
    return this.openFragments.filter(Boolean).join(" ").trim();
  }

  beginGeneration(): { generationId: string; controller: AbortController } {
    this.abortController?.abort();
    this.generationSeq += 1;
    this.generationId = `gen_${this.generationSeq}_${Date.now().toString(36)}`;
    this.abortController = new AbortController();
    return { generationId: this.generationId, controller: this.abortController };
  }

  async finalizeTurn(finalFragment = ""): Promise<{ text: string; generationId: string; controller: AbortController }> {
    if (finalFragment.trim()) this.appendFragment(finalFragment);
    const text = this.mergedText() || this.partialText.trim();
    this.openFragments = [];
    this.partialText = "";
    const { generationId, controller } = this.beginGeneration();
    this.state = "TURN_FINALIZING";
    if (this.sessionId) {
      api(`/conversation/sessions/${this.sessionId}/finalize`, {
        method: "POST",
        body: JSON.stringify({ text }),
      }).catch(() => {});
    }
    this.state = "PROCESSING";
    return { text, generationId, controller };
  }

  async bargeIn(spokenSoFar = "", interruptionText = ""): Promise<string> {
    const prevGen = this.generationId;
    this.abortController?.abort();
    this.abortController = null;
    this.generationSeq += 1;
    this.generationId = `gen_${this.generationSeq}_barge`;
    if (spokenSoFar) this.spokenPrefix = spokenSoFar.slice(0, 1000);
    this.state = "BARGE_IN";
    if (interruptionText.trim()) {
      this.openFragments = [interruptionText.trim()];
    }
    if (this.sessionId) {
      api(`/conversation/sessions/${this.sessionId}/barge-in`, {
        method: "POST",
        body: JSON.stringify({
          spoken_prefix: this.spokenPrefix,
          interruption_text: interruptionText,
          cancelled_generation_id: prevGen,
        }),
      }).catch(() => {});
    }
    this.state = "LISTENING";
    return this.generationId;
  }

  async pauseSession(opts?: { abort?: boolean }) {
    // Hiding the tab pauses the voice session. It does not cancel an answer that is already being written.
    if (opts?.abort) this.abortController?.abort();
    this.state = "SESSION_PAUSED";
    if (this.sessionId) {
      await api(`/conversation/sessions/${this.sessionId}/pause`, { method: "POST" }).catch(() => {});
    }
  }

  async resumeSession(): Promise<any | null> {
    this.state = "LISTENING";
    if (!this.sessionId) return null;
    try {
      const snap = await api<any>(`/conversation/sessions/${this.sessionId}/resume`, { method: "POST" });
      if (snap?.open_fragment) {
        this.openFragments = [snap.open_fragment];
      }
      return snap;
    } catch {
      return null;
    }
  }

  isCurrent(genId: string): boolean {
    return this.generationId === genId;
  }
}
