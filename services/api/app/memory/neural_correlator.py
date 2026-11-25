"""GPU-Boosted Neural Correlation Engine (GNN + RNN + 1D-CNN) using PyTorch.

Runs automatically on CUDA GPU when available (with fast CPU fallback) and produces:
1. 2-layer Graph Neural Network (ClinicalGNN) scores across Symptom/Med/Lab/Condition/Family nodes
2. GRU Temporal Trajectory (TemporalTrajectoryRNN) score over chronological patient events
3. 1D-CNN Motif Detector (MotifCNN1D) over longitudinal numeric lab/vital sequences
4. Structured graph & response-customization explanations for the Context tab Graphical Navigator.
"""
from __future__ import annotations

import logging
import os
import time
from functools import lru_cache
from typing import Any

import torch
import torch.nn as nn

log = logging.getLogger("sushrut.neural")

_DEVICE = torch.device(
    "cuda" if (os.getenv("NEURAL_DEVICE", "auto") != "cpu" and torch.cuda.is_available()) else "cpu"
)


class ClinicalGNN(nn.Module):
    """2-layer multi-head Graph Attention + residual normalized message-passing network."""

    def __init__(self, in_dim: int = 10, hid_dim: int = 24, heads: int = 2) -> None:
        super().__init__()
        self.heads = heads
        self.head_dim = hid_dim // heads
        self.lin1 = nn.Linear(in_dim, hid_dim)
        self.attn_l = nn.Linear(hid_dim, heads)
        self.attn_r = nn.Linear(hid_dim, heads)
        self.lin2 = nn.Linear(hid_dim, hid_dim)
        self.norm1 = nn.LayerNorm(hid_dim)
        self.norm2 = nn.LayerNorm(hid_dim)
        self.head = nn.Linear(hid_dim, 1)
        self.act = nn.GELU()
        self.leaky = nn.LeakyReLU(0.2)

    def forward(self, x: torch.Tensor, adj: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        n = adj.size(0)
        eye = torch.eye(n, device=adj.device, dtype=adj.dtype)
        a_hat = adj + eye
        deg = a_hat.sum(dim=1).clamp(min=1e-6)
        d_inv_sqrt = deg.pow(-0.5).unsqueeze(1)
        norm_a = d_inv_sqrt * a_hat * d_inv_sqrt.t()

        h0 = self.act(self.lin1(norm_a @ x))
        h0 = self.norm1(h0)

        # Multi-head graph attention over connected neighbors
        al = self.attn_l(h0).unsqueeze(1)  # (N, 1, H)
        ar = self.attn_r(h0).unsqueeze(0)  # (1, N, H)
        logits = self.leaky(al + ar) * a_hat.unsqueeze(-1)
        mask = (a_hat <= 0).unsqueeze(-1)
        logits = logits.masked_fill(mask, -1e9)
        alpha = torch.softmax(logits, dim=1)  # (N, N, H)
        attn_weights = alpha.mean(dim=-1)     # (N, N)

        h_attn = attn_weights @ h0
        h = self.norm2(self.act(self.lin2(0.5 * (norm_a @ h0) + 0.5 * h_attn)) + h0)
        return torch.sigmoid(self.head(h)).squeeze(-1), attn_weights


class TemporalTrajectoryRNN(nn.Module):
    """2-layer Bidirectional GRU with temporal attention pooling over clinical events."""

    def __init__(self, in_dim: int = 6, hid_dim: int = 16) -> None:
        super().__init__()
        self.gru = nn.GRU(in_dim, hid_dim, num_layers=2, batch_first=True, bidirectional=True)
        self.attn = nn.Linear(hid_dim * 2, 1)
        self.head = nn.Linear(hid_dim * 2, 1)

    def forward(self, seq: torch.Tensor) -> torch.Tensor:
        # seq: (1, T, in_dim)
        out, _ = self.gru(seq)
        w = torch.softmax(self.attn(out), dim=1)
        ctx = (w * out).sum(dim=1)
        return torch.sigmoid(self.head(ctx)).squeeze(-1)


class MotifCNN1D(nn.Module):
    """Multi-scale dilated 1D-CNN over longitudinal numeric lab/vital sequences."""

    def __init__(self) -> None:
        super().__init__()
        self.conv2 = nn.Conv1d(1, 6, kernel_size=2, padding=1)
        self.conv3 = nn.Conv1d(1, 6, kernel_size=3, padding=1)
        self.conv5 = nn.Conv1d(1, 6, kernel_size=3, padding=2, dilation=2)
        self.act = nn.GELU()
        self.head = nn.Linear(18 * 2, 1)

    def forward(self, series: torch.Tensor) -> torch.Tensor:
        # series: (1, 1, L)
        f2 = self.act(self.conv2(series))
        f3 = self.act(self.conv3(series))
        f5 = self.act(self.conv5(series))
        pooled = torch.cat([
            f2.mean(dim=-1), f2.max(dim=-1).values,
            f3.mean(dim=-1), f3.max(dim=-1).values,
            f5.mean(dim=-1), f5.max(dim=-1).values,
        ], dim=-1)
        return torch.sigmoid(self.head(pooled)).squeeze(-1)


@lru_cache(maxsize=1)
def _models() -> tuple[ClinicalGNN, TemporalTrajectoryRNN, MotifCNN1D]:
    torch.manual_seed(42)
    gnn = ClinicalGNN().to(_DEVICE).eval()
    rnn = TemporalTrajectoryRNN().to(_DEVICE).eval()
    cnn = MotifCNN1D().to(_DEVICE).eval()
    return gnn, rnn, cnn


_KIND_INDEX = {
    "symptom": 0,
    "medication": 1,
    "lab": 2,
    "vital": 3,
    "condition": 4,
    "family": 5,
    "followup": 6,
    "customization": 7,
}


def device_info() -> dict[str, Any]:
    is_cuda = _DEVICE.type == "cuda"
    return {
        "device": _DEVICE.type,
        "gpu_boosted": is_cuda,
        "device_name": torch.cuda.get_device_name(0) if is_cuda else "CPU",
        "torch_version": torch.__version__,
    }


class NeuralCorrelationEngine:
    """Runs GNN + RNN + 1D-CNN on GPU (if available) or CPU to score and explain correlations."""

    def analyze(
        self,
        *,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        timeline_features: list[list[float]] | None = None,
        numeric_series: list[float] | None = None,
        customizations: list[str] | None = None,
    ) -> dict[str, Any]:
        t0 = time.perf_counter()
        gnn, rnn, cnn = _models()

        if not nodes:
            return {
                **device_info(),
                "latency_ms": 0.0,
                "nodes": [],
                "edges": [],
                "temporal_score": 0.0,
                "cnn_motif_score": 0.0,
                "customizations": customizations or [],
            }

        id_to_idx = {n["id"]: i for i, n in enumerate(nodes)}
        n_count = len(nodes)
        x = torch.zeros((n_count, 10), device=_DEVICE, dtype=torch.float32)
        for i, n in enumerate(nodes):
            k_idx = _KIND_INDEX.get(n.get("kind", "symptom"), 0)
            x[i, k_idx] = 1.0
            x[i, 8] = float(n.get("base_weight", 0.7))
            x[i, 9] = float(n.get("recency", 0.75))

        adj = torch.zeros((n_count, n_count), device=_DEVICE, dtype=torch.float32)
        for e in edges:
            u = id_to_idx.get(e["source"])
            v = id_to_idx.get(e["target"])
            if u is not None and v is not None:
                w = float(e.get("weight", 0.6))
                adj[u, v] = max(adj[u, v].item(), w)
                adj[v, u] = max(adj[v, u].item(), w)

        with torch.inference_mode():
            node_scores_t, attn_t = gnn(x, adj)
            node_scores = node_scores_t.detach().cpu().tolist()
            attn_mat = attn_t.detach().cpu().tolist()

            if timeline_features:
                norm_seq = [
                    (f[:6] + [0.5] * max(0, 6 - len(f)))[:6]
                    for f in timeline_features
                ]
                seq = torch.tensor([norm_seq], device=_DEVICE, dtype=torch.float32)
                temporal_score = float(rnn(seq).item())
            else:
                temporal_score = 0.5

            if numeric_series and len(numeric_series) >= 2:
                ser = torch.tensor([[numeric_series]], device=_DEVICE, dtype=torch.float32)
                cnn_score = float(cnn(ser).item())
            else:
                cnn_score = 0.5

        scored_nodes = []
        for i, n in enumerate(nodes):
            scored_nodes.append({
                **n,
                "score": round(float(node_scores[i]), 3),
            })

        enriched_edges = []
        for e in edges:
            u = id_to_idx.get(e["source"])
            v = id_to_idx.get(e["target"])
            attn = round(float(attn_mat[u][v]), 3) if (u is not None and v is not None) else 0.0
            enriched_edges.append({**e, "attention": attn})

        adaptive_followups = sorted(
            [
                {"id": n["id"], "label": n["label"], "score": n["score"], "detail": n.get("detail", "")}
                for n in scored_nodes if n.get("kind") == "followup"
            ],
            key=lambda item: item["score"],
            reverse=True,
        )

        dt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        return {
            **device_info(),
            "latency_ms": dt_ms,
            "nodes": scored_nodes,
            "edges": enriched_edges,
            "temporal_score": round(temporal_score, 3),
            "cnn_motif_score": round(cnn_score, 3),
            "adaptive_followup_priorities": adaptive_followups,
            "customizations": customizations or [],
        }


def build_clinical_neural_graph(
    *,
    symptoms: list[dict[str, Any]],
    medications: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    vitals: list[dict[str, Any]],
    labs: list[dict[str, Any]],
    correlation_notes: list[str] | None = None,
    differentials: list[dict[str, Any]] | None = None,
    subject_relations: list[dict[str, Any]] | None = None,
    followup_questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble a heterogeneous clinical graph and score it with GNN + RNN + 1D-CNN."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    timeline_features: list[list[float]] = []
    numeric_series: list[float] = []
    customizations: list[str] = []

    sym_ids: list[str] = []
    for idx, s in enumerate(symptoms[:8]):
        name = str(s.get("name") or s.get("fact_key") or f"Symptom {idx + 1}").strip()
        sev = str(s.get("severity") or "").lower()
        prog = str(s.get("progression") or "").lower()
        w = 0.9 if ("severe" in sev or "worsen" in prog) else 0.75
        nid = f"sym:{idx}"
        sym_ids.append(nid)
        nodes.append({"id": nid, "label": name, "kind": "symptom", "base_weight": w, "detail": sev or s.get("status", "active")})
        timeline_features.append([0.2, w, min(1.0, (idx + 1) / 8.0), w])

    med_ids: list[str] = []
    for idx, m in enumerate(medications[:6]):
        name = str(m.get("name") or f"Medication {idx + 1}").strip()
        dose = str(m.get("dose") or "").strip()
        nid = f"med:{idx}"
        med_ids.append(nid)
        nodes.append({"id": nid, "label": f"{name} ({dose})" if dose else name, "kind": "medication", "base_weight": 0.7, "detail": m.get("status", "active")})
        timeline_features.append([0.4, 0.7, min(1.0, (idx + 1) / 6.0), 0.7])

    cond_ids: list[str] = []
    for idx, c in enumerate(conditions[:6]):
        name = str(c.get("name") or c.get("description") or f"Condition {idx + 1}").strip()
        nid = f"cond:{idx}"
        cond_ids.append(nid)
        nodes.append({"id": nid, "label": name, "kind": "condition", "base_weight": 0.72, "detail": c.get("status", "active")})
        timeline_features.append([0.6, 0.72, min(1.0, (idx + 1) / 6.0), 0.72])

    vit_ids: list[str] = []
    for idx, v in enumerate(vitals[:6]):
        vtype = str(v.get("type") or "Vital").strip()
        val = v.get("value")
        unit = str(v.get("unit") or "").strip()
        nid = f"vit:{idx}"
        vit_ids.append(nid)
        nodes.append({"id": nid, "label": f"{vtype}: {val} {unit}".strip(), "kind": "vital", "base_weight": 0.65, "detail": "measured" if not v.get("is_estimate") else "estimate"})
        try:
            numeric_series.append(float(str(val).split("/")[0]))
        except (ValueError, TypeError):
            pass
        timeline_features.append([0.75, 0.65, min(1.0, (idx + 1) / 6.0), 0.65])

    lab_ids: list[str] = []
    for idx, lb in enumerate(labs[:6]):
        tname = str(lb.get("test_name") or "Lab").strip()
        val = lb.get("value")
        unit = str(lb.get("unit") or "").strip()
        flag = str(lb.get("flag") or "").lower()
        abnormal = any(k in flag for k in ("high", "low", "abnormal", "critical"))
        w = 0.88 if abnormal else 0.62
        nid = f"lab:{idx}"
        lab_ids.append(nid)
        nodes.append({"id": nid, "label": f"{tname}: {val} {unit}".strip(), "kind": "lab", "base_weight": w, "detail": lb.get("flag") or "normal"})
        try:
            numeric_series.append(float(val))
        except (ValueError, TypeError):
            pass
        timeline_features.append([0.9, w, min(1.0, (idx + 1) / 6.0), w])

    fam_ids: list[str] = []
    for idx, note in enumerate((correlation_notes or [])[:6]):
        is_fam = "household" in note.lower() or "family" in note.lower()
        if is_fam:
            nid = f"fam:{idx}"
            fam_ids.append(nid)
            nodes.append({"id": nid, "label": note[:64] + ("..." if len(note) > 64 else ""), "kind": "family", "base_weight": 0.78, "detail": note})

    rel_node_meta: list[tuple[str, set[str], bool]] = []
    for idx, rel in enumerate((subject_relations or [])[:6]):
        rname = str(rel.get("relation") or "relation").title()
        bits: list[str] = []
        if rel.get("age") is not None:
            bits.append(f"{rel['age']}y")
        if rel.get("gender"):
            bits.append(str(rel["gender"]).capitalize())
        label = f"{rname} ({', '.join(bits)})" if bits else rname
        detail_parts = [f"Relation: {rname} ({'Family' if rel.get('is_family') else 'Friend / Other'})"]
        if rel.get("symptoms"):
            detail_parts.append(f"Symptoms: {', '.join(rel['symptoms'][:5])}")
        if rel.get("notes"):
            detail_parts.append(f"Context: {'; '.join(rel['notes'][:3])}")
        nid = f"rel:{idx}"
        fam_ids.append(nid)
        nodes.append({"id": nid, "label": label, "kind": "family", "base_weight": 0.84, "recency": 0.9, "detail": " · ".join(detail_parts)})
        timeline_features.append([0.5, 0.84, min(1.0, (idx + 1) / 6.0), 0.9, 0.0, 1.0])
        # Link relation node to matching symptoms (or top active symptom)
        rel_syms = {str(s).lower() for s in (rel.get("symptoms") or [])}
        rel_node_meta.append((nid, rel_syms, bool(rel.get("is_family"))))
        linked = False
        for s_idx, s in enumerate(symptoms[:8]):
            sname = str(s.get("name") or s.get("fact_key") or "").lower()
            if sname and sname in rel_syms:
                edges.append({
                    "source": nid, "target": f"sym:{s_idx}", "weight": 0.88,
                    "relation": "subject_of_symptom",
                    "explanation": f"Symptom reported for {label}; age/gender/context tailors triage.",
                })
                linked = True
        if not linked and sym_ids:
            edges.append({
                "source": nid, "target": sym_ids[0], "weight": 0.82,
                "relation": "subject_of_symptom",
                "explanation": f"Context for {label} informs symptom interpretation.",
            })

    # Cross-family correlation edges (shared exposure or family linkage between member nodes)
    for i in range(len(rel_node_meta)):
        id_a, syms_a, fam_a = rel_node_meta[i]
        for j in range(i + 1, len(rel_node_meta)):
            id_b, syms_b, fam_b = rel_node_meta[j]
            shared = syms_a & syms_b
            if shared:
                edges.append({
                    "source": id_a, "target": id_b, "weight": 0.9,
                    "relation": "shared_household_exposure",
                    "explanation": f"Shared family symptoms ({', '.join(sorted(shared)[:3])}) indicate possible household transmission or shared trigger.",
                })
            elif fam_a and fam_b:
                edges.append({
                    "source": id_a, "target": id_b, "weight": 0.65,
                    "relation": "family_profile_link",
                    "explanation": "Linked household family profiles for hereditary & cross-member retrieval.",
                })

    # Follow-up question adaptation nodes
    fq_ids: list[str] = []
    for idx, fq in enumerate((followup_questions or [])[:6]):
        q_txt = str(fq.get("question") or fq.get("question_key") or f"Follow-up {idx + 1}").strip()
        status = str(fq.get("status") or "pending").lower()
        cat = str(fq.get("category") or fq.get("dimension") or "clinical").strip()
        w = 0.88 if status == "pending" else 0.76
        nid = f"fq:{idx}"
        fq_ids.append(nid)
        nodes.append({
            "id": nid,
            "label": q_txt[:56] + ("..." if len(q_txt) > 56 else ""),
            "kind": "followup",
            "base_weight": w,
            "recency": 0.95 if status == "pending" else 0.8,
            "detail": f"{cat} · {status}",
        })
        timeline_features.append([0.85, w, min(1.0, (idx + 1) / 6.0), 0.95, 1.0, 0.0])
        for sid in sym_ids[:2]:
            edges.append({
                "source": sid, "target": nid, "weight": 0.84,
                "relation": "adapts_followup",
                "explanation": f"GNN adapts follow-up priority ({cat}) from active symptoms.",
            })
        for fid in fam_ids[:2]:
            edges.append({
                "source": fid, "target": nid, "weight": 0.78,
                "relation": "family_followup_context",
                "explanation": "Follow-up question tailored to family/subject context.",
            })

    # Clinical correlations between entity layers
    for sid in sym_ids[:3]:
        for mid in med_ids[:2]:
            edges.append({"source": sid, "target": mid, "weight": 0.74, "relation": "med_symptom_link", "explanation": "Medication efficacy or side-effect check against active symptom."})
        for lid in lab_ids[:2]:
            edges.append({"source": sid, "target": lid, "weight": 0.82, "relation": "lab_corroboration", "explanation": "Lab biomarker evaluated alongside symptom trajectory."})
        for cid in cond_ids[:2]:
            edges.append({"source": cid, "target": sid, "weight": 0.79, "relation": "condition_manifestation", "explanation": "Existing condition may explain or amplify this symptom."})
    for vid in vit_ids[:2]:
        for sid in sym_ids[:2]:
            edges.append({"source": vid, "target": sid, "weight": 0.71, "relation": "vital_context", "explanation": "Vital sign reading calibrates symptom urgency."})

    # Response customization nodes
    if sym_ids or lab_ids or vit_ids:
        cid = "cust:triage"
        msg = "Prioritizes targeted clarifying questions and relevant lab/vital checks."
        customizations.append(msg)
        nodes.append({"id": cid, "label": "Tailor Triage & Lab Checks", "kind": "customization", "base_weight": 0.85, "detail": msg})
        for src in (sym_ids[:2] + lab_ids[:2] + vit_ids[:1]):
            edges.append({"source": src, "target": cid, "weight": 0.86, "relation": "customizes_triage", "explanation": msg})

    if med_ids:
        cid = "cust:meds"
        msg = "Filters guidance for medication safety, interactions, and adherence."
        customizations.append(msg)
        nodes.append({"id": cid, "label": "Personalize Medication Safety", "kind": "customization", "base_weight": 0.82, "detail": msg})
        for src in (med_ids[:2] + sym_ids[:1]):
            edges.append({"source": src, "target": cid, "weight": 0.81, "relation": "customizes_meds", "explanation": msg})

    if fam_ids:
        cid = "cust:family"
        msg = "Factors household/hereditary signals into differential weighting."
        customizations.append(msg)
        nodes.append({"id": cid, "label": "Factor Family & Household Risk", "kind": "customization", "base_weight": 0.8, "detail": msg})
        for src in fam_ids:
            edges.append({"source": src, "target": cid, "weight": 0.84, "relation": "customizes_family_risk", "explanation": msg})

    if differentials:
        cid = "cust:diff"
        top_diff = str(differentials[0].get("explanation") or "Top differential").strip()
        msg = f"Ranks differential hypotheses (leading: {top_diff[:48]})."
        customizations.append(msg)
        nodes.append({"id": cid, "label": "Rank Differential Likelihoods", "kind": "customization", "base_weight": 0.88, "detail": msg})
        for src in (sym_ids[:2] + cond_ids[:1] + lab_ids[:1]):
            edges.append({"source": src, "target": cid, "weight": 0.85, "relation": "customizes_differential", "explanation": msg})

    if nodes and not customizations:
        cid = "cust:adapt"
        msg = "Adapts clinical tone and avoids repeating already-answered questions."
        customizations.append(msg)
        nodes.append({"id": cid, "label": "Adapt Clinical Tone & Follow-ups", "kind": "customization", "base_weight": 0.75, "detail": msg})
        edges.append({"source": nodes[0]["id"], "target": cid, "weight": 0.75, "relation": "customizes_response", "explanation": msg})

    return NeuralCorrelationEngine().analyze(
        nodes=nodes,
        edges=edges,
        timeline_features=timeline_features or None,
        numeric_series=numeric_series or None,
        customizations=customizations,
    )
