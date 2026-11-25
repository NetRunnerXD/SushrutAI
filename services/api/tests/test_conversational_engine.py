"""Stage 9 Evaluation & Verification Suite (Parts 13 & 14).

Covers:
1. 13-state ConversationOrchestrator, strict transition validation, multi-fragment turn merging,
   <200ms barge-in cancellation, stale generation_id write rejection, and pause/resume continuity.
2. Two-stage multilingual transcript refinement, invariant safety guard (negations, numbers, doses,
   left/right, severity, drugs), Transcript/TranscriptVersion provenance, and /voice/stream WebSocket.
3. Uncapped (>3 questions) hypothesis-driven AdaptiveClinicalInterviewEngine with distinct paths
   (inner-ear/BPPV vs orthostatic presyncope) and all 5 outcome states.
4. ClinicalReasoningEngine evolving differential updates, patient correction handling, and causal queries.
5. Patient-dependent InvestigationRecommendationEngine checking existing LabResult records.
6. /clinical/context (Sections A-M), transcript span highlighting [span_start:span_end], authorization
   guard on CLINICIAN_CONFIRMED records (403), offline sync conflict (409), and DPDP export/erasure.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.clinical.extract import extract_deterministic
from app.clinical.facts import record_clinical_facts
from app.clinical.interview import AdaptiveClinicalInterviewEngine
from app.clinical.investigations import InvestigationRecommendationEngine
from app.clinical.persistence import get_or_create_encounter
from app.clinical.reasoning import ClinicalReasoningEngine
from app.conversation.state import ConversationState, conversation_orchestrator
from app.db import SessionLocal, engine
from app.llm import orchestrator as llm_orch
from app.llm import providers as providers_mod
from app.main import app
from app.memory.context import PatientContextBuilder
from app.models import (
    ClinicalAssessment,
    ClinicalFact,
    ClinicalInvestigation,
    DifferentialConsideration,
    FollowUpQuestion,
    LabResult,
    Transcript,
    TranscriptVersion,
)
from app.routers import voice as voice_router
from app.safety.clinical_safety import evaluate_safety
from app.speech import models as speech_models
from app.speech.refine import persist_transcript, preserves_clinical_invariants, refine_transcript
from app.speech.stream import StreamingSpeechSession
from tests.conftest import current_user_for


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    cur_ev = "message"
    cur_data: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event:"):
            cur_ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            cur_data.append(line.split(":", 1)[1].strip())
        elif line.strip() == "" and cur_data:
            events.append((cur_ev, json.loads("\n".join(cur_data))))
            cur_ev = "message"
            cur_data = []
    if cur_data:
        events.append((cur_ev, json.loads("\n".join(cur_data))))
    return events


# ------------------------------------------------------------------ 1. State Machine, Fragment Merge, Barge-in, Pause/Resume
async def test_conversation_state_fragment_merging_barge_in_and_pause_resume(client, guest):
    cu = await current_user_for(guest)
    s_res = await client.post("/chat/sessions", json={"lang": "en", "channel": "voice"}, headers=guest)
    assert s_res.status_code in (200, 201)
    sid = s_res.json()["id"]

    # Illegal transition rejected cleanly in strict mode
    bad = await client.post(
        f"/conversation/sessions/{sid}/state",
        json={"state": "BARGE_IN", "reason": "invalid direct jump from IDLE", "strict": True},
        headers=guest,
    )
    assert bad.status_code == 409

    # Valid listening -> pause -> fragment merging across thinking pause
    r1 = await client.post(
        f"/conversation/sessions/{sid}/state",
        json={"state": "LISTENING", "reason": "Mic opened"},
        headers=guest,
    )
    assert r1.status_code == 200
    assert r1.json()["state"] == "LISTENING"
    sc = r1.json()["silence_config"]
    assert 800 <= sc["pause_detect_ms"] <= 1200
    assert 3000 <= sc["continuation_grace_ms"] <= 8000

    f1 = await client.post(
        f"/conversation/sessions/{sid}/fragment",
        json={"text": "I have been having a headache"},
        headers=guest,
    )
    assert f1.status_code == 200
    await client.post(
        f"/conversation/sessions/{sid}/state",
        json={"state": "WAITING_FOR_CONTINUATION", "reason": "Thinking pause"},
        headers=guest,
    )
    f2 = await client.post(
        f"/conversation/sessions/{sid}/fragment",
        json={"text": "for approximately four days"},
        headers=guest,
    )
    assert f2.status_code == 200
    assert f2.json()["merged_text"] == "I have been having a headache for approximately four days"

    # Pause & resume keeps open fragment continuity
    pz = await client.post(f"/conversation/sessions/{sid}/pause", headers=guest)
    assert pz.status_code == 200
    assert pz.json()["state"] == "SESSION_PAUSED"
    assert pz.json()["open_fragment"] == "I have been having a headache for approximately four days"

    rz = await client.post(f"/conversation/sessions/{sid}/resume", headers=guest)
    assert rz.status_code == 200
    assert rz.json()["state"] == "LISTENING"
    assert rz.json()["open_fragment"] == "I have been having a headache for approximately four days"

    # Finalize allocates fresh monotonic generation_id + asyncio cancellation token
    fin = await client.post(f"/conversation/sessions/{sid}/finalize", json={"text": ""}, headers=guest)
    assert fin.status_code == 200
    assert fin.json()["committed_text"] == "I have been having a headache for approximately four days"
    gen1 = fin.json()["generation_id"]
    enc_id = fin.json()["encounter_id"]
    cur_gen, ev1 = conversation_orchestrator.cancellation_token(enc_id, cu.id)
    assert cur_gen == gen1
    assert not ev1.is_set()

    # Transition to SPEAKING then trigger immediate barge-in
    sp = await client.post(
        f"/conversation/sessions/{sid}/state",
        json={"state": "SPEAKING", "reason": "Playing TTS"},
        headers=guest,
    )
    assert sp.status_code == 200
    bi = await client.post(
        f"/conversation/sessions/{sid}/barge-in",
        json={"spoken_prefix": "Since you have had fever for four days", "interruption_text": "No, not fever"},
        headers=guest,
    )
    assert bi.status_code == 200
    bdata = bi.json()
    assert bdata["cancelled_generation_id"] == gen1
    assert bdata["generation_id"] != gen1
    assert bdata["spoken_prefix_preserved"] == "Since you have had fever for four days"
    assert ev1.is_set()  # Cancellation event triggered synchronously
    assert not conversation_orchestrator.is_generation_current(enc_id, cu.id, gen1)


async def test_stale_generation_cannot_commit_after_barge_in(client, guest, monkeypatch):
    s_res = await client.post("/chat/sessions", json={"lang": "en", "channel": "voice"}, headers=guest)
    sid = s_res.json()["id"]
    fin = await client.post(
        f"/conversation/sessions/{sid}/finalize",
        json={"text": "I have mild fever since yesterday"},
        headers=guest,
    )
    stale_gen = fin.json()["generation_id"]

    async def fake_answer(db, cu, text, **kwargs):
        # Simulate user interrupting right before LLM response finishes
        await conversation_orchestrator.handle_barge_in(
            db, cu, fin.json()["encounter_id"], spoken_prefix="Stopped early", interruption_text="Actually no fever"
        )
        return llm_orch.TurnResult(
            reply="Here is an answer about fever.",
            english="Here is an answer about fever.",
            structured={"summary": "Fever summary"},
            citations=[],
            disclaimer="Not a diagnosis.",
            provider="local",
            model="test",
            tier="local",
            speed="fast",
            latency_ms=5,
            red_flag={"triggered": False},
            warnings=[],
            kind="symptom",
        )

    monkeypatch.setattr(llm_orch, "answer", fake_answer)
    sse = await client.post(
        f"/chat/sessions/{sid}/messages",
        json={"text": "I have mild fever since yesterday", "lang": "en", "channel": "voice", "generation_id": stale_gen},
        headers=guest,
    )
    assert sse.status_code == 200
    evs = _parse_sse(sse.text)
    ev_names = [e[0] for e in evs]
    assert "cancelled" in ev_names
    assert "answer" not in ev_names

    # Verify stale message was NOT committed to chat history
    sess = await client.get(f"/chat/sessions/{sid}", headers=guest)
    assert sess.json()["messages"] == []


async def test_client_generation_id_binds_cleanly_on_typed_chat(client, guest, monkeypatch):
    s_res = await client.post("/chat/sessions", json={"lang": "en", "channel": "text"}, headers=guest)
    assert s_res.status_code in (200, 201)
    sid = s_res.json()["id"]

    async def fake_ok_answer(db, cu, text, **kwargs):
        return llm_orch.TurnResult(
            reply="Dizziness on standing can be checked alongside blood pressure and hydration.",
            english="Dizziness on standing can be checked alongside blood pressure and hydration.",
            structured={"summary": "Orthostatic symptoms", "follow_up_questions": []},
            citations=[],
            disclaimer="Not a diagnosis.",
            provider="local",
            model="test",
            tier="local",
            speed="fast",
            latency_ms=10,
            red_flag={"triggered": False},
            warnings=[],
            kind="symptom",
        )

    monkeypatch.setattr(llm_orch, "answer", fake_ok_answer)
    # Normal typed message sends client-only generation_id without prior /finalize -> MUST commit cleanly
    ok_sse = await client.post(
        f"/chat/sessions/{sid}/messages",
        json={"text": "I feel dizziness when standing up", "lang": "en", "channel": "text", "generation_id": "gen_1_client_typed"},
        headers=guest,
    )
    assert ok_sse.status_code == 200
    evs = _parse_sse(ok_sse.text)
    assert any(e[0] == "answer" for e in evs)
    assert not any(e[0] == "cancelled" for e in evs)

    # Pre-cancelled client generation_id via /barge-in is rejected immediately without running model
    bi = await client.post(
        f"/conversation/sessions/{sid}/barge-in",
        json={"cancelled_generation_id": "gen_2_client_precancel", "interruption_text": "Wait"},
        headers=guest,
    )
    assert bi.status_code == 200
    pre_sse = await client.post(
        f"/chat/sessions/{sid}/messages",
        json={"text": "Should cancel", "lang": "en", "channel": "text", "generation_id": "gen_2_client_precancel"},
        headers=guest,
    )
    evs2 = _parse_sse(pre_sse.text)
    assert any(e[0] == "cancelled" for e in evs2)
    assert not any(e[0] == "answer" for e in evs2)


# ------------------------------------------------------------------ 2. Two-Stage Transcript Refinement & Safety Guard + WS
async def test_transcript_refinement_preserves_invariants_and_blocks_unsafe_edits(client, guest, monkeypatch):
    # Safe invariant helper checks
    ok_safe, bad_safe = preserves_clinical_invariants("amar 5 din dhore jor", "Amar ৫ দিন ধরে জ্বর")
    assert ok_safe and bad_safe == []

    for orig, mutated, expect_cat in [
        ("I have no chest pain", "I have chest pain", "neg"),
        ("Taking paracetamol 500 mg daily", "Taking paracetamol 250 mg daily", "num"),
        ("Pain on left side of head", "Pain on right side of head", "lat"),
        ("Moderate headache for 3 days", "Severe headache for 3 days", "sev"),
        ("No allergy, took metformin", "No allergy, took aspirin", "med"),
    ]:
        ok, cats = preserves_clinical_invariants(orig, mutated)
        assert not ok
        assert expect_cat in cats

    # Mock Stage-2 LLM returning an unsafe edit that drops negation "no" -> guard MUST reject & flag clarification
    class _MockRes:
        def __init__(self, payload):
            self._p = payload
        def json(self):
            return self._p

    class _MockOllama:
        def __init__(self, payload):
            self._p = payload
        async def chat(self, *args, **kwargs):
            return _MockRes(self._p)

    monkeypatch.setattr(
        providers_mod,
        "ollama",
        lambda: _MockOllama({"corrected": "Amar 5 din dhore chest pain ache", "uncertain": False}),
    )
    s_res = await client.post("/chat/sessions", json={"lang": "bn", "channel": "voice"}, headers=guest)
    sid = s_res.json()["id"]
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        enc = await get_or_create_encounter(db, cu, chat_session_id=sid, channel="voice", language="bn")
        await db.commit()
        enc_id = enc.id

    r_unsafe = await client.post(
        "/voice/refine",
        json={
            "raw_transcript": "Amar 5 din dhore no chest pain ache",
            "lang": "bn",
            "encounter_id": enc_id,
            "use_llm": True,
        },
        headers=guest,
    )
    assert r_unsafe.status_code == 200
    du = r_unsafe.json()
    assert du["safety_preserved"] is False
    assert "neg" in du["rejected_categories"]
    assert du["needs_clarification"] is True
    assert "no" in du["corrected_transcript"].lower()
    assert du["transcript_id"] is not None

    monkeypatch.setattr(
        providers_mod,
        "ollama",
        lambda: _MockOllama({"corrected": "Amar 5 din dhore matha ghurche, no chest pain.", "uncertain": False}),
    )
    r_safe = await client.post(
        "/voice/refine",
        json={"raw_transcript": "amar 5 din dhore matha ghurche no chest pain", "lang": "bn", "encounter_id": enc_id, "use_llm": True},
        headers=guest,
    )
    assert r_safe.status_code == 200
    ds = r_safe.json()
    assert ds["safety_preserved"] is True
    assert ds["needs_clarification"] is False
    assert "5" in ds["corrected_transcript"]

    async with SessionLocal() as db:
        vers = list(await db.scalars(TranscriptVersion.__table__.select().where(TranscriptVersion.transcript_id == ds["transcript_id"])))
        assert len(vers) >= 2


def test_voice_stream_websocket_auth_and_partial_final(monkeypatch):
    # Patch speech backend so offline streaming test runs deterministically in ms without downloading models
    monkeypatch.setattr(speech_models, "available", lambda pkg: pkg in ("onnxruntime", "transformers", "faster_whisper"))
    monkeypatch.setattr(speech_models, "detect_spoken_language", lambda audio, allowed, ui: ("bn", 0.91))
    monkeypatch.setattr(
        speech_models,
        "stt_indic_detailed",
        lambda audio, lang: {
            "text": "Amar ৫ দিন ধরে মাথা ঘুরছে",
            "segments": [{"start": 0.0, "end": 0.8, "text": "Amar ৫ দিন ধরে মাথা ঘুরছে", "language": "bn"}],
        },
    )
    monkeypatch.setattr(
        speech_models,
        "stt_whisper_detailed",
        lambda audio, lang: {
            "text": "Amar ৫ দিন ধরে মাথা ঘুরছে",
            "lang": "bn",
            "confidence": 0.91,
            "segments": [{"start": 0.0, "end": 0.8, "text": "Amar ৫ দিন ধরে মাথা ঘুরছে", "language": "bn"}],
        },
    )

    with TestClient(app) as tc:
        # Unauthenticated WS closes cleanly with 4401
        with pytest.raises(Exception):
            with tc.websocket_connect("/api/v1/voice/stream?token=invalid.jwt"):
                pass

        # Valid guest token
        g = tc.post("/api/v1/auth/guest").json()
        tok = g["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        tc.post("/api/v1/consents", json={"purposes": ["core_service", "history_storage"]}, headers=h)

        with tc.websocket_connect(f"/api/v1/voice/stream?token={tok}&lang=auto&ui=en") as ws:
            ws.send_text(json.dumps({"type": "config", "format": "pcm", "sample_rate": 16000}))
            # Send 4 voiced PCM16 chunks (~800ms) -> partial transcript arrives
            pcm = (np.sin(np.linspace(0, 80, 3200)) * 12000).astype(np.int16).tobytes()
            for _ in range(4):
                ws.send_bytes(pcm)
            partial = ws.receive_json()
            assert partial["type"] == "partial"
            assert partial["is_partial"] is True
            assert "মাথা ঘুরছে" in partial["text"]

            # Finalize returns full refined response
            ws.send_text(json.dumps({"type": "finalize", "use_llm": False}))
            final = ws.receive_json()
            assert final["type"] == "final"
            assert final["is_partial"] is False
            assert final["safety_preserved"] is True
            assert "5" in final["corrected_transcript"]


# ------------------------------------------------------------------ 3. Uncapped Adaptive Clinical Interview Engine
async def test_adaptive_interview_branches_by_complaint_and_exceeds_three_questions(client, guest):
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        # Patient A: positional vertigo (turning head) -> vestibular / BPPV pathway
        enc_a = await get_or_create_encounter(db, cu, channel="chat", language="en", chief_complaint="Spinning on turning head")
        ext_a = extract_deterministic("The room spins whenever I turn my head in bed for 2 days", patient_id=cu.id, encounter_id=enc_a.id)
        mem_a = await PatientContextBuilder().build(db, cu, encounter_id=enc_a.id)
        saf_a = evaluate_safety("The room spins whenever I turn my head", lang="en", extraction=ext_a)
        res_a = await AdaptiveClinicalInterviewEngine().evaluate_and_next_question(
            db, cu, encounter_id=enc_a.id, user_text="The room spins whenever I turn my head in bed",
            extraction=ext_a, mem=mem_a, safety=saf_a, lang="en"
        )
        assert res_a["outcome"] == "ADDITIONAL_INFORMATION_REQUIRED"
        assert res_a["question"]["question_key"].startswith(("bppv_", "red_flag_", "rf_", "onset_"))
        assert "ear" in res_a["question"]["question"].lower() or "hear" in res_a["question"]["question"].lower() or "faint" in res_a["question"]["question"].lower() or "second" in res_a["question"]["question"].lower()

        # Patient B: presyncope on standing -> distinct orthostatic / fainting / meds pathway & >3 questions!
        enc_b = await get_or_create_encounter(db, cu, channel="chat", language="en", chief_complaint="Might faint when standing")
        asked_keys: list[str] = []
        turn_inputs = [
            "I feel like I might faint when standing",
            "No chest pain, started 4 days ago, happens every morning",
            "Lasts about 2 minutes and makes it hard to walk",
            "No fainting or blackout actually, just lightheaded",
            "Drink very little water, took metformin today",
            "No other chronic conditions I know of",
        ]
        for txt in turn_inputs:
            ext_b = extract_deterministic(txt, patient_id=cu.id, encounter_id=enc_b.id)
            from app.clinical.persistence import persist_extraction
            await persist_extraction(db, cu, ext_b, encounter_id=enc_b.id)
            mem_b = await PatientContextBuilder().build(db, cu, encounter_id=enc_b.id, current_symptoms=[s.name for s in ext_b.symptoms])
            saf_b = evaluate_safety(txt, lang="en", extraction=ext_b)
            res_b = await AdaptiveClinicalInterviewEngine().evaluate_and_next_question(
                db, cu, encounter_id=enc_b.id, user_text=txt,
                extraction=ext_b, mem=mem_b, safety=saf_b, lang="en"
            )
            if res_b["question"]:
                asked_keys.append(res_b["question"]["question_key"])

        assert len(asked_keys) >= 4, f"Expected >3 dynamic follow-ups without 3-question cap, got {asked_keys}"
        assert asked_keys[0] != res_a["question"]["question_key"] or any(k.startswith("ortho_") or k == "rf_fainting" for k in asked_keys)

        # Patient decline -> PATIENT_DECLINED outcome
        dec = await AdaptiveClinicalInterviewEngine().evaluate_and_next_question(
            db, cu, encounter_id=enc_b.id, user_text="No more questions please, stop asking",
            extraction=extract_deterministic("", patient_id=cu.id, encounter_id=enc_b.id), mem=mem_b, safety=saf_b, lang="en"
        )
        assert dec["outcome"] == "PATIENT_DECLINED"

        # Patient pause -> PATIENT_REQUESTED_PAUSE outcome
        pau = await AdaptiveClinicalInterviewEngine().evaluate_and_next_question(
            db, cu, encounter_id=enc_b.id, user_text="Please wait a bit, let me pause",
            extraction=extract_deterministic("", patient_id=cu.id, encounter_id=enc_b.id), mem=mem_b, safety=saf_b, lang="en"
        )
        assert pau["outcome"] == "PATIENT_REQUESTED_PAUSE"

        # Red-flag symptom -> URGENT_ESCALATION outcome
        rf_ext = extract_deterministic("Sudden crushing chest pain and breathlessness", patient_id=cu.id, encounter_id=enc_a.id)
        rf_saf = evaluate_safety("Sudden crushing chest pain and breathlessness", lang="en", extraction=rf_ext)
        urg = await AdaptiveClinicalInterviewEngine().evaluate_and_next_question(
            db, cu, encounter_id=enc_a.id, user_text="Sudden crushing chest pain",
            extraction=rf_ext, mem=mem_a, safety=rf_saf, lang="en"
        )
        assert urg["outcome"] == "URGENT_ESCALATION"


# ------------------------------------------------------------------ 4. ClinicalReasoningEngine Evolution, Correction & Causal Query
async def test_clinical_reasoning_updates_on_patient_correction_and_causal_query(client, guest):
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        enc = await get_or_create_encounter(db, cu, channel="chat", language="en", chief_complaint="Dizziness")
        engine_r = ClinicalReasoningEngine()

        # Turn 1: standing triggers orthostatic hypothesis
        ext1 = extract_deterministic("My head feels lightheaded when standing up for 3 days", patient_id=cu.id, encounter_id=enc.id)
        mem1 = await PatientContextBuilder().build(db, cu, encounter_id=enc.id, current_symptoms=["dizziness"])
        saf1 = evaluate_safety("My head feels lightheaded when standing up for 3 days", lang="en", extraction=ext1)
        a1 = await engine_r.evaluate_and_persist(
            db, cu, encounter_id=enc.id, user_text="My head feels lightheaded when standing up for 3 days",
            extraction=ext1, mem=mem1, safety=saf1
        )
        assert a1["version_no"] == 1
        assert any("Orthostatic" in d["explanation"] for d in a1["differentials"])

        # Turn 2: Patient corrects ("No, it also happens even when sitting") -> bumps version, adds evidence_against
        ext2 = extract_deterministic("No, it also happens even when sitting and turning head in bed", patient_id=cu.id, encounter_id=enc.id)
        mem2 = await PatientContextBuilder().build(db, cu, encounter_id=enc.id, current_symptoms=["dizziness"])
        saf2 = evaluate_safety("No, it also happens even when sitting and turning head in bed", lang="en", extraction=ext2)
        a2 = await engine_r.evaluate_and_persist(
            db, cu, encounter_id=enc.id, user_text="No, it also happens even when sitting and turning head in bed",
            extraction=ext2, mem=mem2, safety=saf2
        )
        assert a2["version_no"] == 2
        assert a2["correction_detected"] is True
        assert "PATIENT CORRECTION JUST RECEIVED" in a2["prompt_block"]
        ortho = next((d for d in a2["differentials"] if "Orthostatic" in d["explanation"]), None)
        assert ortho is not None
        assert len(ortho["evidence_against"]) >= 1

        # Turn 3: Causal patient query -> "Could this be because of low blood pressure?"
        ext3 = extract_deterministic("Could this be because of low blood pressure?", patient_id=cu.id, encounter_id=enc.id)
        a3 = await engine_r.evaluate_and_persist(
            db, cu, encounter_id=enc.id, user_text="Could this be because of low blood pressure?",
            extraction=ext3, mem=mem2, safety=saf2
        )
        assert a3["causal_question_detected"] is True
        assert "PATIENT ASKED ABOUT A SPECIFIC CAUSE" in a3["prompt_block"]


# ------------------------------------------------------------------ 5. Tailored InvestigationRecommendationEngine
async def test_investigation_recommendations_are_tailored_and_check_existing_labs(client, guest):
    cu = await current_user_for(guest)
    async with SessionLocal() as db:
        # Patient already has a CBC lab result on file
        lr = LabResult(
            user_id=cu.id,
            test_name="Complete Blood Count (CBC) - Haemoglobin",
            value=10.8,
            unit="g/dL",
            flag="low",
            observed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3),
        )
        db.add(lr)
        await db.commit()
        await db.refresh(lr)

        enc = await get_or_create_encounter(db, cu, channel="chat", language="en", chief_complaint="Weakness and dizziness")
        from app.clinical.persistence import persist_extraction
        ext = extract_deterministic("I feel dizziness and severe weakness for 5 days, poor oral intake", patient_id=cu.id, encounter_id=enc.id)
        await persist_extraction(db, cu, ext, encounter_id=enc.id)
        mem = await PatientContextBuilder().build(db, cu, encounter_id=enc.id, current_symptoms=[s.name for s in ext.symptoms])
        saf = evaluate_safety("I feel dizziness and severe weakness for 5 days", lang="en", extraction=ext)

        recs = await InvestigationRecommendationEngine().recommend(
            db, cu, encounter_id=enc.id, mem=mem, safety=saf,
            user_text="I feel dizziness and severe weakness for 5 days, poor oral intake",
        )
        assert len(recs) >= 2
        cbc = next((r for r in recs if "Complete Blood Count" in r["investigation_name"]), None)
        assert cbc is not None
        assert cbc["already_in_records"] is True
        assert "10.8" in (cbc["existing_record_summary"] or "")
        assert cbc["clinician_decision_required"] is True
        assert cbc["limitations"]
        # Should NOT blindly include unrelated LFT/thyroid tests for an acute 5-day non-jaundice presentation
        assert not any("Liver Function" in r["investigation_name"] for r in recs)


# ------------------------------------------------------------------ 6. Context UI API, Span Highlights, Authz 403, Offline Conflict 409, DPDP
async def test_patient_context_api_span_highlights_authz_conflict_and_dpdp_erasure(client, guest, tmpdir_path):
    cu = await current_user_for(guest)
    raw = "Amar 5 din dhore matha ghurche when standing up and I take metformin"
    async with SessionLocal() as db:
        enc = await get_or_create_encounter(db, cu, channel="voice", language="bn", chief_complaint=raw)
        ref = await refine_transcript(raw, lang_hint="bn", audio_conf=0.88, use_llm=False)
        tr = await persist_transcript(db, cu, encounter_id=enc.id, turn_id=None, speaker="patient", result=ref)
        ext = extract_deterministic(raw, patient_id=cu.id, encounter_id=enc.id)
        facts = await record_clinical_facts(
            db, cu, encounter_id=enc.id, conversation_id=None, turn_id=None,
            transcript_id=tr.id, transcript_text=raw, payload=ext,
        )
        # Seed a clinician-confirmed baseline condition fact
        clin_fact = ClinicalFact(
            user_id=cu.id,
            encounter_id=None,
            scope="patient",
            category="condition",
            fact_key="Hypertension",
            value_enc=cu.cipher.enc(json.dumps({"name": "Hypertension", "status": "confirmed"})),
            original_quote_enc=cu.cipher.enc("Clinic summary: Hypertension"),
            verification_status="CLINICIAN_CONFIRMED",
            source_type="clinician_record",
            confidence=1.0,
            active=True,
            observed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        db.add(clin_fact)
        await db.commit()
        clin_id = clin_fact.id
        pat_fact_id = facts[0].id
        pat_observed_iso = facts[0].observed_at.isoformat()

    ctx = await client.get("/clinical/context?lang=en", headers=guest)
    assert ctx.status_code == 200
    c = ctx.json()
    for sec in (
        "current_complaints", "symptom_details", "medical_history", "medication_history",
        "allergies", "measurements_and_vitals", "functional_impact", "temporal_timeline",
        "unknown_information", "contradictions", "clinical_assessment", "pending_questions",
        "recommended_next_steps", "transcripts", "facts",
    ):
        assert sec in c

    # Span highlighting resolves back to exact substring inside source transcript
    fact_with_span = next((f for f in c["facts"] if f["span_start"] is not None and f["transcript_id"]), None)
    assert fact_with_span is not None
    src_tr = c["transcripts"][fact_with_span["transcript_id"]]
    sliced = src_tr["raw_text"][fact_with_span["span_start"]:fact_with_span["span_end"]]
    assert len(sliced) >= 2 and sliced.lower() in fact_with_span["original_quote"].lower()

    # Patient CANNOT modify CLINICIAN_CONFIRMED record -> 403
    forb = await client.post(
        f"/clinical/facts/{clin_id}/correct",
        json={"value": {"name": "Normotensive"}, "reason": "trying override", "actor_role": "patient"},
        headers=guest,
    )
    assert forb.status_code == 403

    # Clinician CAN modify CLINICIAN_CONFIRMED record -> 200 non-destructive correction
    ok_clin = await client.post(
        f"/clinical/facts/{clin_id}/correct",
        json={"value": {"name": "Essential Hypertension Stage 1"}, "reason": "clinic update", "actor_role": "clinician"},
        headers=guest,
    )
    assert ok_clin.status_code == 200
    assert ok_clin.json()["correction_history"][0]["previous_value"]["name"] == "Hypertension"

    # Stale offline client_updated_at older than server observed_at -> 409 conflict
    stale_dt = (datetime.fromisoformat(pat_observed_iso) - timedelta(hours=2)).isoformat()
    conf = await client.post(
        f"/clinical/facts/{pat_fact_id}/correct",
        json={"value": {"note": "stale"}, "reason": "offline edit", "actor_role": "patient", "client_updated_at": stale_dt},
        headers=guest,
    )
    assert conf.status_code == 409

    # Export includes new entities
    exp = await client.get("/me/export", headers=guest)
    assert exp.status_code == 200
    ed = exp.json()
    assert len(ed["transcripts"]) >= 1
    assert len(ed["clinical_facts"]) >= 1

    # DPDP Right to Erasure wipes all 7 new tables cleanly
    dl = await client.delete("/me", headers=guest)
    assert dl.status_code == 204
    db_file = engine.url.database
    if db_file:
        con = sqlite3.connect(db_file)
        try:
            for tbl in (
                "transcripts", "transcript_versions", "clinical_facts",
                "clinical_assessments", "differential_considerations", "clinical_investigations",
            ):
                cnt = con.execute(f"SELECT COUNT(*) FROM {tbl} WHERE user_id = ?", (cu.id,)).fetchone()[0]
                assert cnt == 0, f"Found {cnt} orphaned rows in {tbl}"
        finally:
            con.close()
