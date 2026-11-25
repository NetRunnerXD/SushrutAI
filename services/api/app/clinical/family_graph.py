"""Family Health Knowledge Graph & Extensible Relational GraphSAGE (GNN) Architecture.

Graph Schema:
  Nodes: Patient, FamilyMember, Symptom, Disease, Condition, Medication, Allergy,
         Investigation, Doctor, Consultation, MedicalReport
  Edges: PARENT_OF, CHILD_OF, SPOUSE_OF, HAS_SYMPTOM, HAS_MEDICAL_HISTORY,
         TAKES_MEDICATION, HAS_ALLERGY, UNDERWENT_TEST, CONSULTED_DOCTOR,
         REPORTED_CONDITION

CLINICAL RULE:
  Family medical patterns (e.g., Father -> Reported Diabetes, Grandmother -> Reported
  Breast Cancer) inform risk-factor context ONLY — never treated as proof of a
  patient's own diagnosis. GNN predictions are strictly research/advisory and require
  independent clinical validation before diagnostic use.
"""
from __future__ import annotations

import math
from typing import Any

import networkx as nx
import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clinical.ml_models import compute_binary_metrics
from app.db import scope_to
from app.models import (
    Allergy,
    ClinicalSymptom,
    Condition,
    Document,
    Encounter,
    HouseholdMember,
    LabResult,
    Medication,
    User,
)
from app.security.auth import CurrentUser

NODE_TYPES = (
    "Patient",
    "FamilyMember",
    "Symptom",
    "Disease",
    "Condition",
    "Medication",
    "Allergy",
    "Investigation",
    "Doctor",
    "Consultation",
    "MedicalReport",
)

EDGE_TYPES = (
    "PARENT_OF",
    "CHILD_OF",
    "SPOUSE_OF",
    "HAS_SYMPTOM",
    "HAS_MEDICAL_HISTORY",
    "TAKES_MEDICATION",
    "HAS_ALLERGY",
    "UNDERWENT_TEST",
    "CONSULTED_DOCTOR",
    "REPORTED_CONDITION",
)


async def build_family_health_graph(db: AsyncSession, cu: CurrentUser) -> dict[str, Any]:
    """Construct an isolated Family Health Knowledge Graph for `cu.account_id`."""
    g = nx.MultiDiGraph()
    aid = cu.account_id
    acipher = cu.account_cipher

    root_id = f"patient:{aid}"
    g.add_node(root_id, node_type="Patient", label="Self (Account Holder)", member_id=aid)

    # Helper to populate clinical nodes & edges for a specific user/member
    async def populate_person(person_node: str, target_cu: CurrentUser) -> list[str]:
        await scope_to(db, target_cu.id, aid)
        person_conditions: list[str] = []

        for c in await db.scalars(select(Condition).where(Condition.user_id == target_cu.id)):
            cname = target_cu.cipher.dec(c.display_enc)
            person_conditions.append(cname)
            cid = f"condition:{cname.lower()}"
            g.add_node(cid, node_type="Condition", label=cname)
            rel_edge = "REPORTED_CONDITION" if target_cu.id != aid else "HAS_MEDICAL_HISTORY"
            g.add_edge(person_node, cid, relation=rel_edge, source_patient=target_cu.id)

        for sx in await db.scalars(select(ClinicalSymptom).where(ClinicalSymptom.user_id == target_cu.id)):
            sid = f"symptom:{sx.name.lower()}"
            g.add_node(sid, node_type="Symptom", label=sx.name)
            g.add_edge(person_node, sid, relation="HAS_SYMPTOM", status=sx.status)

        for m in await db.scalars(select(Medication).where(Medication.user_id == target_cu.id)):
            mname = target_cu.cipher.dec(m.name_enc)
            mid = f"medication:{mname.lower()}"
            g.add_node(mid, node_type="Medication", label=mname)
            g.add_edge(person_node, mid, relation="TAKES_MEDICATION", status=m.status)

        for a in await db.scalars(select(Allergy).where(Allergy.user_id == target_cu.id)):
            aname = target_cu.cipher.dec(a.substance_enc)
            al_id = f"allergy:{aname.lower()}"
            g.add_node(al_id, node_type="Allergy", label=aname)
            g.add_edge(person_node, al_id, relation="HAS_ALLERGY")

        for lr in await db.scalars(select(LabResult).where(LabResult.user_id == target_cu.id)):
            lid = f"investigation:{lr.test_name.lower()}"
            g.add_node(lid, node_type="Investigation", label=lr.test_name)
            g.add_edge(person_node, lid, relation="UNDERWENT_TEST", flag=lr.flag)

        for doc in await db.scalars(select(Document).where(Document.user_id == target_cu.id)):
            did = f"report:{doc.id}"
            g.add_node(did, node_type="MedicalReport", label=doc.kind)
            g.add_edge(person_node, did, relation="UNDERWENT_TEST")

        for enc in await db.scalars(select(Encounter).where(Encounter.user_id == target_cu.id)):
            eid = f"consultation:{enc.id}"
            g.add_node(eid, node_type="Consultation", label=enc.channel, urgency=enc.urgency)
            g.add_edge(person_node, eid, relation="CONSULTED_DOCTOR")
            if enc.specialist:
                doc_id = f"doctor:{enc.specialist.lower()}"
                g.add_node(doc_id, node_type="Doctor", label=enc.specialist)
                g.add_edge(person_node, doc_id, relation="CONSULTED_DOCTOR")

        return person_conditions

    await populate_person(root_id, cu)

    links = list(await db.scalars(
        select(HouseholdMember).where(HouseholdMember.user_id == aid).order_by(HouseholdMember.created_at)
    ))
    familial_patterns: list[dict[str, Any]] = []

    for link in links:
        mname = acipher.dec(link.name_enc)
        muser = await db.get(User, link.member_id)
        if muser is None:
            continue
        from app.security.crypto import Cipher
        mcu = CurrentUser(user=muser, account=cu.account or cu.user, cipher=Cipher(muser.dek_wrapped, muser.id))
        m_node = f"member:{link.member_id}"
        g.add_node(m_node, node_type="FamilyMember", label=mname, relation=link.relation, member_id=link.member_id)

        if link.relation == "parent":
            g.add_edge(m_node, root_id, relation="PARENT_OF")
        elif link.relation == "child":
            g.add_edge(root_id, m_node, relation="CHILD_OF")
        elif link.relation == "spouse":
            g.add_edge(root_id, m_node, relation="SPOUSE_OF")
        elif link.relation == "grandparent":
            g.add_edge(m_node, root_id, relation="PARENT_OF")
        else:
            g.add_edge(root_id, m_node, relation="HAS_MEDICAL_HISTORY")

        m_conds = await populate_person(m_node, mcu)
        for cond in m_conds:
            familial_patterns.append({
                "source_member": mname,
                "relationship": link.relation,
                "reported_condition": cond,
                "clinical_interpretation": (
                    f"{mname} ({link.relation}) reported {cond}. This is family history context only "
                    "and does NOT diagnose the speaker."
                ),
            })

    await scope_to(db, cu.id, aid)

    nodes = [{"id": nid, **attrs} for nid, attrs in g.nodes(data=True)]
    edges = [{"source": u, "target": v, **attrs} for u, v, attrs in g.edges(data=True)]

    return {
        "schema": {"node_types": list(NODE_TYPES), "edge_types": list(EDGE_TYPES)},
        "stats": {"node_count": len(nodes), "edge_count": len(edges), "family_members": len(links)},
        "nodes": nodes,
        "edges": edges,
        "familial_patterns": familial_patterns,
        "gnn_status": {
            "architecture": "RelationalGraphSAGE (2-layer message passing, pure NumPy)",
            "operational_for_diagnosis": False,
            "note": "Rule-based clinical graph analysis is active; GNN layer is advisory/research pending clinical validation.",
        },
    }


class RelationalGraphSAGE:
    """Extensible 2-layer Relational GraphSAGE / R-GCN encoder over heterogeneous patient-family graphs."""

    def __init__(self, in_dim: int = 8, hidden_dim: int = 8, seed: int = 42) -> None:
        rng = np.random.default_rng(seed)
        self.W_self = rng.normal(0.0, 0.35, size=(in_dim, hidden_dim))
        self.W_fam = rng.normal(0.0, 0.35, size=(in_dim, hidden_dim))
        self.W_clin = rng.normal(0.0, 0.35, size=(in_dim, hidden_dim))
        self.head_w = np.zeros(hidden_dim, dtype=np.float64)
        self.head_b = 0.0

    @staticmethod
    def _relu(x: np.ndarray) -> np.ndarray:
        return np.maximum(0.0, x)

    @staticmethod
    def _sigmoid(z: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(z, -25.0, 25.0)))

    def encode(self, x_self: np.ndarray, x_fam_mean: np.ndarray, x_clin_mean: np.ndarray) -> np.ndarray:
        h = self._relu(x_self @ self.W_self + x_fam_mean @ self.W_fam + x_clin_mean @ self.W_clin)
        return h

    def fit(self, x_self: np.ndarray, x_fam: np.ndarray, x_clin: np.ndarray, y: np.ndarray, epochs: int = 250, lr: float = 0.2) -> None:
        H = self.encode(x_self, x_fam, x_clin)
        n = len(y)
        for _ in range(epochs):
            p = self._sigmoid(H @ self.head_w + self.head_b)
            err = p - y
            self.head_w -= lr * ((H.T @ err) / n + 0.01 * self.head_w)
            self.head_b -= lr * float(np.mean(err))

    def predict_proba(self, x_self: np.ndarray, x_fam: np.ndarray, x_clin: np.ndarray) -> np.ndarray:
        H = self.encode(x_self, x_fam, x_clin)
        return self._sigmoid(H @ self.head_w + self.head_b)


def evaluate_gnn_vs_rule_baseline(n_graphs: int = 600, seed: int = 7) -> dict[str, Any]:
    """Compare RelationalGraphSAGE against a rule-based family-risk baseline on synthetic graph embeddings."""
    rng = np.random.default_rng(seed)
    # 8-dim node features: [age_norm, symptom_load, bp_flag, sugar_flag, cancer_hx, cardio_hx, chronic_count, abnormal_lab]
    x_self = rng.random((n_graphs, 8))
    x_fam = rng.random((n_graphs, 8)) * 0.8
    x_clin = rng.random((n_graphs, 8)) * 0.9

    logits = (
        -2.2
        + 1.8 * x_self[:, 1]
        + 1.6 * x_self[:, 2]
        + 1.5 * x_self[:, 7]
        + 1.4 * x_fam[:, 4]
        + 1.2 * x_fam[:, 5]
        + 1.3 * x_clin[:, 3]
    )
    probs = 1.0 / (1.0 + np.exp(-logits))
    y = (rng.random(n_graphs) < probs).astype(int)

    split = int(0.7 * n_graphs)
    gnn = RelationalGraphSAGE(in_dim=8, hidden_dim=8, seed=seed)
    gnn.fit(x_self[:split], x_fam[:split], x_clin[:split], y[:split])

    gnn_probs = gnn.predict_proba(x_self[split:], x_fam[split:], x_clin[split:])
    rule_probs = np.clip(0.35 * x_self[split:, 1] + 0.35 * x_fam[split:, 4] + 0.30 * x_clin[split:, 3], 0.0, 1.0)

    return {
        "validation_graphs": n_graphs - split,
        "clinical_deployment_ready": False,
        "validation_note": "Synthetic benchmark only; requires real clinical cohort validation before diagnostic deployment.",
        "models": [
            compute_binary_metrics(y[split:], gnn_probs, "RelationalGraphSAGE_2Layer").as_dict(),
            compute_binary_metrics(y[split:], rule_probs, "Rule_Based_Graph_Baseline").as_dict(),
        ],
    }
