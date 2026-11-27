"use client";

import {
  ChevronDown, ChevronUp, Cpu, GitBranch, Layers, Network, RotateCcw,
  Search, Sliders, Sparkles, Zap, ZoomIn, ZoomOut,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Card } from "@/components/ui/Card";

export interface NeuralNode {
  id: string;
  label: string;
  kind: "symptom" | "medication" | "lab" | "vital" | "condition" | "family" | "customization" | string;
  base_weight?: number;
  score: number;
  detail?: string;
}

export interface NeuralEdge {
  source: string;
  target: string;
  weight: number;
  relation: string;
  explanation: string;
}

export interface NeuralGraphData {
  enabled: boolean;
  device: string;
  gpu_boosted: boolean;
  device_name: string;
  torch_version?: string;
  latency_ms: number;
  nodes: NeuralNode[];
  edges: NeuralEdge[];
  temporal_score: number;
  cnn_motif_score: number;
  customizations: string[];
}

const KIND_STYLE: Record<string, { label: string; fill: string; stroke: string; badge: string; text: string; col: number }> = {
  symptom: { label: "Symptom", fill: "#fff1f2", stroke: "#f43f5e", badge: "bg-rose-500/10 text-rose-700 border-rose-500/30", text: "#9f1239", col: 0 },
  family: { label: "Family / Relation", fill: "#ecfdf5", stroke: "#10b981", badge: "bg-emerald-500/10 text-emerald-700 border-emerald-500/30", text: "#065f46", col: 0 },
  lab: { label: "Lab Result", fill: "#ecfeff", stroke: "#06b6d4", badge: "bg-cyan-500/10 text-cyan-700 border-cyan-500/30", text: "#155e75", col: 1 },
  vital: { label: "Vital Sign", fill: "#eff6ff", stroke: "#3b82f6", badge: "bg-blue-500/10 text-blue-700 border-blue-500/30", text: "#1e40af", col: 1 },
  condition: { label: "Condition", fill: "#fffbeb", stroke: "#f59e0b", badge: "bg-amber-500/10 text-amber-700 border-amber-500/30", text: "#92400e", col: 2 },
  medication: { label: "Medication", fill: "#f5f3ff", stroke: "#8b5cf6", badge: "bg-violet-500/10 text-violet-700 border-violet-500/30", text: "#5b21b6", col: 2 },
  customization: { label: "Response Customization", fill: "#eef2ff", stroke: "#4f46e5", badge: "bg-indigo-500/10 text-indigo-700 border-indigo-500/30", text: "#312e81", col: 3 },
};

const COL_X = [105, 305, 505, 705];
const COL_TITLES = ["Signals & Relations", "Labs & Vitals", "History & Meds", "AI Customizations"];

function trunc(s: string, max = 20): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function ContextGraphNavigator({ graph, liveActive = false }: { graph?: NeuralGraphData | null; liveActive?: boolean }) {
  const [filterKind, setFilterKind] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [isolateNeighborhood, setIsolateNeighborhood] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdgeIdx, setSelectedEdgeIdx] = useState<number | null>(null);
  const [archOpen, setArchOpen] = useState(false);

  const nodes = graph?.nodes ?? [];
  const edges = graph?.edges ?? [];

  const activeNode = useMemo(() => {
    if (selectedId) {
      const found = nodes.find((n) => n.id === selectedId);
      if (found) return found;
    }
    return nodes.find((n) => n.kind === "customization") ?? nodes[0] ?? null;
  }, [nodes, selectedId]);

  const activeEdges = useMemo(() => {
    if (!activeNode) return [];
    return edges.filter((e) => e.source === activeNode.id || e.target === activeNode.id);
  }, [edges, activeNode]);

  const connectedToActive = useMemo(() => {
    const s = new Set<string>();
    if (activeNode) s.add(activeNode.id);
    activeEdges.forEach((e) => {
      s.add(e.source);
      s.add(e.target);
    });
    return s;
  }, [activeNode, activeEdges]);

  const filteredNodes = useMemo(() => {
    let base = nodes;
    if (filterKind !== "all") {
      const connectedIds = new Set<string>();
      nodes.filter((n) => n.kind === filterKind).forEach((n) => connectedIds.add(n.id));
      edges.forEach((e) => {
        if (connectedIds.has(e.source) || connectedIds.has(e.target)) {
          connectedIds.add(e.source);
          connectedIds.add(e.target);
        }
      });
      base = base.filter((n) => connectedIds.has(n.id));
    }
    const q = search.trim().toLowerCase();
    if (q) {
      const matchedIds = new Set<string>();
      base.filter((n) => n.label.toLowerCase().includes(q) || (n.detail || "").toLowerCase().includes(q))
        .forEach((n) => matchedIds.add(n.id));
      edges.forEach((e) => {
        if (matchedIds.has(e.source) || matchedIds.has(e.target)) {
          matchedIds.add(e.source);
          matchedIds.add(e.target);
        }
      });
      base = base.filter((n) => matchedIds.has(n.id));
    }
    if (isolateNeighborhood && activeNode) {
      base = base.filter((n) => connectedToActive.has(n.id));
    }
    return base;
  }, [nodes, edges, filterKind, search, isolateNeighborhood, activeNode, connectedToActive]);

  const layout = useMemo(() => {
    const buckets: NeuralNode[][] = [[], [], [], []];
    filteredNodes.forEach((n) => {
      const col = KIND_STYLE[n.kind]?.col ?? 1;
      buckets[col].push(n);
    });
    const maxRows = Math.max(1, ...buckets.map((b) => b.length));
    const svgHeight = Math.max(320, 88 + maxRows * 68);
    const pos = new Map<string, { x: number; y: number }>();

    buckets.forEach((colNodes, cIdx) => {
      const count = colNodes.length;
      if (count === 0) return;
      const span = svgHeight - 90;
      const step = span / (count + 1);
      colNodes.forEach((n, rIdx) => {
        pos.set(n.id, { x: COL_X[cIdx], y: 52 + step * (rIdx + 1) });
      });
    });

    return { pos, svgHeight };
  }, [filteredNodes]);

  const selectedEdge = selectedEdgeIdx !== null ? edges[selectedEdgeIdx] ?? null : null;
  const topScoredNodes = useMemo(
    () => [...nodes].sort((a, b) => (b.score ?? 0) - (a.score ?? 0)).slice(0, 5),
    [nodes],
  );

  if (!graph) return null;

  if (graph.enabled === false) {
    return (
      <Card className="border-dashed">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-surface-2 text-muted">
              <Network className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-[1.0625rem] font-bold">Neural Correlation Navigator is Off</h2>
              <p className="text-[0.875rem] text-muted">
                Turn on the GNN + RNN + 1D-CNN Neural Correlation Engine in Settings to map event connections and response customizations.
              </p>
            </div>
          </div>
          <Link href="/me/settings" className="btn btn-secondary min-h-10 gap-2 text-sm">
            <Sliders className="h-4 w-4" /> Open Settings
          </Link>
        </div>
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden border-brand/25 bg-gradient-to-br from-surface via-surface to-brand-soft/20 p-0">
      {/* Header + GPU/CPU & Model Telemetry + Architecture Expand Button */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/80 px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand/10 text-brand">
            <Network className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-[1.0625rem] font-bold tracking-tight">Neural Context &amp; Correlation Navigator</h2>
            <p className="text-[0.8125rem] text-muted">
              Click any node or edge to trace correlations, isolate paths, or inspect the GNN + RNN + 1D-CNN architecture.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[0.75rem] font-semibold">
          {liveActive && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-brand/40 bg-brand/10 px-2.5 py-1 text-brand">
              <span className="h-2 w-2 animate-ping rounded-full bg-brand" />
              Live Sync
            </span>
          )}
          <span
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${
              graph.gpu_boosted
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700"
                : "border-border bg-surface-2 text-text"
            }`}
            title={graph.device_name}
          >
            {graph.gpu_boosted ? <Zap className="h-3.5 w-3.5" /> : <Cpu className="h-3.5 w-3.5" />}
            {graph.gpu_boosted ? "GPU Boosted (CUDA)" : "CPU Engine"} · {graph.latency_ms} ms
          </span>
          <span className="rounded-full border border-border bg-surface-2 px-2.5 py-1">
            RNN Temporal: {(graph.temporal_score * 100).toFixed(0)}%
          </span>
          <span className="rounded-full border border-border bg-surface-2 px-2.5 py-1">
            1D-CNN Motif: {(graph.cnn_motif_score * 100).toFixed(0)}%
          </span>
          <button
            type="button"
            onClick={() => setArchOpen((v) => !v)}
            aria-expanded={archOpen}
            className="inline-flex items-center gap-1.5 rounded-full border border-brand/40 bg-brand/10 px-3 py-1 text-brand transition hover:bg-brand/15"
          >
            <Layers className="h-3.5 w-3.5" />
            Architecture
            {archOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
          <Link
            href="/me/settings"
            className="inline-flex items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-1 text-muted hover:border-brand hover:text-brand"
          >
            <Sliders className="h-3 w-3" /> Settings
          </Link>
        </div>
      </div>

      {/* Expandable Neural Architecture Explorer */}
      {archOpen && (
        <div className="border-b border-border/80 bg-surface-2/70 px-5 py-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-[0.9375rem] font-bold">Multi-Modal Neural Correlation Architecture (PyTorch)</h3>
              <p className="text-[0.8125rem] text-muted">
                Hardware: <span className="font-semibold text-text">{graph.device_name}</span> · Torch{" "}
                <span className="font-mono">{graph.torch_version || "2.x"}</span> · End-to-end latency:{" "}
                <span className="font-semibold text-text">{graph.latency_ms} ms</span>
              </p>
            </div>
            <span className="rounded-full bg-indigo-500/10 px-3 py-1 text-[0.75rem] font-semibold text-indigo-700">
              {nodes.length} Nodes · {edges.length} Weighted Edges
            </span>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            {/* Stage 1: GNN */}
            <div className="rounded-2xl border border-border bg-surface p-3.5">
              <div className="flex items-center justify-between">
                <span className="text-[0.6875rem] font-bold uppercase tracking-wider text-indigo-600">Stage 1 · Spatial Graph</span>
                <span className="rounded-md bg-indigo-500/10 px-2 py-0.5 font-mono text-[0.7rem] font-semibold text-indigo-700">ClinicalGNN</span>
              </div>
              <p className="mt-1 text-[0.875rem] font-bold">2-Layer Normalized Message Passing</p>
              <p className="mt-1 text-[0.75rem] text-muted">
                Propagates clinical weights across symptoms, relations, meds, labs &amp; conditions via{" "}
                <span className="font-mono">D⁻¹/²(A+I)D⁻¹/²</span> with ReLU &amp; residual fusion.
              </p>
              {topScoredNodes.length > 0 && (
                <div className="mt-2.5 space-y-1.5">
                  {topScoredNodes.slice(0, 3).map((n) => (
                    <button
                      key={n.id}
                      type="button"
                      onClick={() => { setSelectedId(n.id); setSelectedEdgeIdx(null); }}
                      className="block w-full text-left"
                    >
                      <div className="flex justify-between text-[0.75rem]">
                        <span className="truncate font-medium">{n.label}</span>
                        <span className="font-bold text-brand">{Math.round(n.score * 100)}%</span>
                      </div>
                      <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                        <div className="h-full rounded-full bg-indigo-500" style={{ width: `${Math.round(n.score * 100)}%` }} />
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Stage 2: RNN */}
            <div className="rounded-2xl border border-border bg-surface p-3.5">
              <div className="flex items-center justify-between">
                <span className="text-[0.6875rem] font-bold uppercase tracking-wider text-emerald-600">Stage 2 · Sequence</span>
                <span className="rounded-md bg-emerald-500/10 px-2 py-0.5 font-mono text-[0.7rem] font-semibold text-emerald-700">TemporalRNN (GRU)</span>
              </div>
              <p className="mt-1 text-[0.875rem] font-bold">Chronological Trajectory Encoder</p>
              <p className="mt-1 text-[0.75rem] text-muted">
                Encodes 4-D chronological vectors <span className="font-mono">[kind, severity, Δt, weight]</span> through a GRU hidden state to score symptom progression.
              </p>
              <div className="mt-3">
                <div className="flex justify-between text-[0.75rem] font-semibold">
                  <span>Temporal Trajectory Score</span>
                  <span className="text-emerald-700">{(graph.temporal_score * 100).toFixed(1)}%</span>
                </div>
                <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-surface-2">
                  <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${Math.round(graph.temporal_score * 100)}%` }} />
                </div>
              </div>
            </div>

            {/* Stage 3: 1D-CNN */}
            <div className="rounded-2xl border border-border bg-surface p-3.5">
              <div className="flex items-center justify-between">
                <span className="text-[0.6875rem] font-bold uppercase tracking-wider text-cyan-600">Stage 3 · Biomarkers</span>
                <span className="rounded-md bg-cyan-500/10 px-2 py-0.5 font-mono text-[0.7rem] font-semibold text-cyan-700">MotifCNN1D</span>
              </div>
              <p className="mt-1 text-[0.875rem] font-bold">1D Convolutional Trend Scanner</p>
              <p className="mt-1 text-[0.75rem] text-muted">
                Slides 1D kernels (<span className="font-mono">Conv1d → ReLU → MaxPool</span>) over numeric vital &amp; lab readings to detect rising/falling motifs.
              </p>
              <div className="mt-3">
                <div className="flex justify-between text-[0.75rem] font-semibold">
                  <span>Biomarker Motif Score</span>
                  <span className="text-cyan-700">{(graph.cnn_motif_score * 100).toFixed(1)}%</span>
                </div>
                <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-surface-2">
                  <div className="h-full rounded-full bg-cyan-500 transition-all" style={{ width: `${Math.round(graph.cnn_motif_score * 100)}%` }} />
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {nodes.length === 0 ? (
        <div className="p-6 text-center text-[0.9375rem] text-muted">
          No clinical events recorded yet. As you describe symptoms, family/friend relations, labs, vitals, or medications in chat, this navigator will map their neural correlations and response customizations.
        </div>
      ) : (
        <>
          {/* Interactive Filter, Search & Canvas Controls Bar */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 bg-surface-2/60 px-5 py-2.5">
            <div className="flex flex-wrap items-center gap-1.5">
              {[
                { id: "all", label: `All (${nodes.length})` },
                { id: "symptom", label: "Symptoms" },
                { id: "lab", label: "Labs" },
                { id: "vital", label: "Vitals" },
                { id: "condition", label: "Conditions" },
                { id: "medication", label: "Medications" },
                { id: "family", label: "Family & Relations" },
                { id: "customization", label: "Customizations" },
              ].map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setFilterKind(tab.id)}
                  className={`rounded-full px-3 py-1 text-[0.75rem] font-semibold transition ${
                    filterKind === tab.id
                      ? "bg-brand text-white shadow-sm"
                      : "bg-surface text-muted hover:bg-surface-2 hover:text-text"
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-1.5">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
                <input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Find node…"
                  aria-label="Search graph nodes"
                  className="h-8 w-36 rounded-full border border-border bg-surface pl-8 pr-3 text-[0.75rem] outline-none focus:border-brand"
                />
              </div>
              <button
                type="button"
                onClick={() => setIsolateNeighborhood((v) => !v)}
                aria-pressed={isolateNeighborhood}
                className={`rounded-full border px-2.5 py-1 text-[0.75rem] font-semibold transition ${
                  isolateNeighborhood
                    ? "border-brand bg-brand/10 text-brand"
                    : "border-border bg-surface text-muted hover:text-text"
                }`}
              >
                Isolate Path
              </button>
              <div className="inline-flex items-center rounded-full border border-border bg-surface">
                <button
                  type="button"
                  onClick={() => setZoom((z) => Math.max(0.75, +(z - 0.15).toFixed(2)))}
                  aria-label="Zoom out"
                  className="p-1.5 text-muted hover:text-text"
                >
                  <ZoomOut className="h-3.5 w-3.5" />
                </button>
                <span className="px-1 text-[0.7rem] font-semibold text-muted">{Math.round(zoom * 100)}%</span>
                <button
                  type="button"
                  onClick={() => setZoom((z) => Math.min(1.6, +(z + 0.15).toFixed(2)))}
                  aria-label="Zoom in"
                  className="p-1.5 text-muted hover:text-text"
                >
                  <ZoomIn className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => { setZoom(1); setFilterKind("all"); setSearch(""); setIsolateNeighborhood(false); setSelectedEdgeIdx(null); }}
                  aria-label="Reset view"
                  className="p-1.5 text-muted hover:text-text"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </div>

          {/* Interactive Graph + Inspector */}
          <div className="grid gap-0 lg:grid-cols-[1.65fr_1fr]">
            <div className="overflow-auto border-b border-border/70 bg-surface/70 p-3 lg:border-b-0 lg:border-e">
              <div style={{ transform: `scale(${zoom})`, transformOrigin: "top left", width: `${100 / zoom}%` }}>
                <svg
                  viewBox={`0 0 810 ${layout.svgHeight}`}
                  className="h-auto w-full min-w-[640px] select-none"
                  role="img"
                  aria-label="Neural clinical correlation graph"
                >
                  <defs>
                    <filter id="nodeGlow" x="-20%" y="-20%" width="140%" height="140%">
                      <feDropShadow dx="0" dy="2" stdDeviation="3" floodColor="#4f46e5" floodOpacity="0.22" />
                    </filter>
                  </defs>

                  {/* Column Headers */}
                  {COL_TITLES.map((title, i) => (
                    <text
                      key={title}
                      x={COL_X[i]}
                      y={24}
                      textAnchor="middle"
                      className="fill-slate-500 text-[11px] font-bold uppercase tracking-wider"
                    >
                      {title}
                    </text>
                  ))}

                  {/* Clickable Edges */}
                  {edges.map((e, idx) => {
                    const p1 = layout.pos.get(e.source);
                    const p2 = layout.pos.get(e.target);
                    if (!p1 || !p2) return null;
                    const isEdgeSelected = selectedEdgeIdx === idx;
                    const isHighlighted =
                      isEdgeSelected ||
                      (activeNode && (e.source === activeNode.id || e.target === activeNode.id));
                    const dx = (p2.x - p1.x) * 0.45;
                    const d = `M ${p1.x + 76} ${p1.y} C ${p1.x + 76 + dx} ${p1.y}, ${p2.x - 76 - dx} ${p2.y}, ${p2.x - 76} ${p2.y}`;
                    return (
                      <g key={`${e.source}-${e.target}-${idx}`} onClick={() => setSelectedEdgeIdx(idx)} className="cursor-pointer">
                        <path d={d} fill="none" stroke="transparent" strokeWidth={12} />
                        <path
                          d={d}
                          fill="none"
                          stroke={isEdgeSelected ? "#f59e0b" : isHighlighted ? "#4f46e5" : "#cbd5e1"}
                          strokeWidth={isEdgeSelected ? 3.5 : isHighlighted ? Math.max(2.2, e.weight * 3.2) : 1.4}
                          strokeOpacity={activeNode || isEdgeSelected ? (isHighlighted ? 0.95 : 0.3) : 0.65}
                          strokeDasharray={isHighlighted ? "6 4" : undefined}
                        />
                      </g>
                    );
                  })}

                  {/* Nodes */}
                  {filteredNodes.map((n) => {
                    const p = layout.pos.get(n.id);
                    if (!p) return null;
                    const style = KIND_STYLE[n.kind] ?? KIND_STYLE.symptom;
                    const isSelected = activeNode?.id === n.id;
                    const isDimmed = activeNode ? !connectedToActive.has(n.id) : false;
                    return (
                      <g
                        key={n.id}
                        transform={`translate(${p.x - 76}, ${p.y - 23})`}
                        onClick={() => { setSelectedId(n.id); setSelectedEdgeIdx(null); }}
                        className="cursor-pointer"
                        style={{ opacity: isDimmed ? 0.45 : 1, transition: "opacity 160ms ease" }}
                        filter={isSelected ? "url(#nodeGlow)" : undefined}
                      >
                        <title>{`${n.label} (${Math.round((n.score ?? 0) * 100)}%)${n.detail ? ` — ${n.detail}` : ""}`}</title>
                        <rect
                          width={152}
                          height={46}
                          rx={12}
                          fill={style.fill}
                          stroke={style.stroke}
                          strokeWidth={isSelected ? 2.5 : 1.3}
                        />
                        <text x={10} y={17} fontSize={9.5} fontWeight={700} fill={style.stroke}>
                          {style.label.toUpperCase()}
                        </text>
                        <text x={142} y={17} textAnchor="end" fontSize={9.5} fontWeight={700} fill={style.text}>
                          {Math.round((n.score ?? 0) * 100)}%
                        </text>
                        <text x={10} y={34} fontSize={11.5} fontWeight={600} fill={style.text}>
                          {trunc(n.label, 19)}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              </div>
            </div>

            {/* Right Inspector & Customizations */}
            <div className="flex flex-col justify-between gap-4 bg-surface p-4">
              {selectedEdge ? (
                <div className="space-y-2.5 rounded-2xl border border-amber-500/30 bg-amber-500/5 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="rounded-full bg-amber-500/15 px-2.5 py-0.5 text-[0.7rem] font-bold uppercase text-amber-800">
                      Edge · {selectedEdge.relation.replace(/_/g, " ")}
                    </span>
                    <button
                      type="button"
                      onClick={() => setSelectedEdgeIdx(null)}
                      className="text-[0.75rem] font-semibold text-muted hover:text-text"
                    >
                      Clear
                    </button>
                  </div>
                  <p className="text-[0.875rem] font-bold">
                    {(nodes.find((n) => n.id === selectedEdge.source)?.label ?? selectedEdge.source)} →{" "}
                    {(nodes.find((n) => n.id === selectedEdge.target)?.label ?? selectedEdge.target)}
                  </p>
                  <p className="text-[0.8125rem] text-muted">{selectedEdge.explanation}</p>
                  <p className="text-[0.75rem] font-semibold text-brand">
                    Correlation Weight: {Math.round(selectedEdge.weight * 100)}%
                  </p>
                </div>
              ) : null}

              {activeNode && (
                <div className="space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <span
                        className={`inline-block rounded-full border px-2.5 py-0.5 text-[0.6875rem] font-bold uppercase tracking-wide ${
                          (KIND_STYLE[activeNode.kind] ?? KIND_STYLE.symptom).badge
                        }`}
                      >
                        {(KIND_STYLE[activeNode.kind] ?? KIND_STYLE.symptom).label}
                      </span>
                      <h3 className="mt-1.5 text-[1rem] font-bold leading-snug">{activeNode.label}</h3>
                    </div>
                    <div className="rounded-xl bg-surface-2 px-2.5 py-1 text-right">
                      <div className="text-[0.65rem] font-bold uppercase text-muted">GNN Score</div>
                      <div className="text-[0.9375rem] font-extrabold text-brand">
                        {Math.round((activeNode.score ?? 0) * 100)}%
                      </div>
                    </div>
                  </div>

                  {activeNode.detail && (
                    <p className="rounded-xl bg-surface-2 px-3 py-2 text-[0.8125rem] text-muted">
                      {activeNode.detail}
                    </p>
                  )}

                  <div>
                    <div className="mb-1.5 flex items-center gap-1.5 text-[0.75rem] font-bold uppercase tracking-wide text-muted">
                      <GitBranch className="h-3.5 w-3.5" /> Connected Correlations ({activeEdges.length})
                    </div>
                    {activeEdges.length === 0 ? (
                      <p className="text-[0.8125rem] text-muted">No direct edges for this node yet.</p>
                    ) : (
                      <ul className="max-h-44 space-y-1.5 overflow-y-auto pr-1">
                        {activeEdges.map((e, i) => {
                          const otherId = e.source === activeNode.id ? e.target : e.source;
                          const other = nodes.find((n) => n.id === otherId);
                          return (
                            <li key={`${otherId}-${i}`}>
                              <button
                                type="button"
                                onClick={() => { setSelectedId(otherId); setSelectedEdgeIdx(null); }}
                                className="w-full rounded-xl border border-border bg-surface-2 p-2.5 text-left transition hover:border-brand"
                              >
                                <div className="flex items-center justify-between gap-2 text-[0.8125rem] font-semibold">
                                  <span className="truncate">{other?.label ?? otherId}</span>
                                  <span className="shrink-0 text-[0.75rem] text-brand">
                                    {Math.round(e.weight * 100)}%
                                  </span>
                                </div>
                                <p className="mt-0.5 text-[0.75rem] text-muted">{e.explanation}</p>
                              </button>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                </div>
              )}

              {graph.customizations.length > 0 && (
                <div className="rounded-2xl border border-indigo-500/20 bg-indigo-500/5 p-3">
                  <div className="mb-1.5 flex items-center gap-1.5 text-[0.75rem] font-bold uppercase tracking-wide text-indigo-700">
                    <Sparkles className="h-3.5 w-3.5" /> How This Customizes Responses
                  </div>
                  <ul className="space-y-1 text-[0.8125rem] text-text">
                    {graph.customizations.map((c, idx) => (
                      <li key={idx} className="flex items-start gap-1.5">
                        <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-600" />
                        <span>{c}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </Card>
  );
}
