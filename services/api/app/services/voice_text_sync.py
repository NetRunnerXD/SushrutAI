"""VoiceTextSynchronizationService: Single source of truth for Voice TTS + Screen UI sections."""
from __future__ import annotations

import re
from typing import Any

from app.services.emergency_triage import EmergencyAssessment


def _clean_speech(text: str) -> str:
    s = re.sub(r"```[\s\S]*?```", " ", text or "")
    s = re.sub(r"[*_#`>|]", " ", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


class VoiceTextSynchronizationService:
    """Produces synchronized screen sections and ordered spoken segments from one structured object."""

    def synchronize(
        self,
        *,
        lang: str,
        structured: dict[str, Any],
        specialty: dict[str, Any] | None = None,
        investigations: dict[str, Any] | None = None,
        diet: dict[str, Any] | None = None,
        emergency: EmergencyAssessment | None = None,
        followup_question: str | None = None,
        channel: str = "text",
        minor_note: str | None = None,
    ) -> dict[str, Any]:
        clean = (lang or "en").split("-")[0].lower()
        sections: list[dict[str, Any]] = []
        segments: list[dict[str, str]] = []

        def add_sec(sid: str, title: str, screen_text: str, spoken_text: str, items: list[Any] | None = None) -> None:
            sp = _clean_speech(spoken_text)
            sc = (screen_text or "").strip()
            if not sc and not sp:
                return
            sections.append({
                "id": sid,
                "title": title,
                "screen_text": sc,
                "spoken_text": sp,
                "items": items or [],
            })
            if sp:
                segments.append({"section_id": sid, "text": sp})

        # 1. Emergency Alert (always first and stops routine items if critical)
        if emergency and emergency.triggered and emergency.emergency:
            add_sec(
                "emergency",
                emergency.screen_title,
                "\n".join(emergency.messages),
                emergency.spoken_alert,
                emergency.call_numbers,
            )
            spoken_full = " ".join(seg["text"] for seg in segments)
            structured["sync_sections"] = sections
            structured["spoken_segments"] = segments
            structured["spoken_response"] = spoken_full
            return {"sync_sections": sections, "spoken_segments": segments, "spoken_response": spoken_full}

        # 2. Clinical Acknowledgement & Main Narrative
        reply_clean = _clean_speech(str(structured.get("reply") or ""))
        if reply_clean:
            sents = re.split(r"(?<=[.!?।])\s+", reply_clean)
            opening = " ".join(sents[:2]).strip()
            if len(opening) > 260:
                opening = opening[:257].rsplit(" ", 1)[0] + "."
            title = "প্রাথমিক মূল্যায়ন" if clean == "bn" else ("प्रारंभिक आकलन" if clean == "hi" else "Preliminary Assessment")
            add_sec("acknowledgement", title, reply_clean, opening)

        if minor_note:
            title_m = "অভিভাবক নির্দেশনা" if clean == "bn" else ("अभिभावक मार्गदर्शन" if clean == "hi" else "Guardian Guidance")
            add_sec("minor_guidance", title_m, minor_note, minor_note)

        # 3. Possible Causes (labeled as possibilities, never confirmed diagnosis)
        causes = list(structured.get("possible_conditions") or structured.get("possible_explanations") or [])
        if causes:
            names = [str(c.get("name") or c.get("name_en") or "").strip() for c in causes if isinstance(c, dict)]
            names = [n for n in names if n][:3]
            if names:
                if clean == "bn":
                    sp = f"সম্ভাব্য কারণগুলোর মধ্যে {', '.join(names)} থাকতে পারে, তবে নিশ্চিত নির্ণয় চিকিৎসকের পরীক্ষার পরই সম্ভব।"
                    title_c = "সম্ভাব্য কারণ (নিশ্চিত রোগ নির্ণয় নয়)"
                elif clean == "hi":
                    sp = f"संभावित कारणों में {', '.join(names)} शामिल हो सकते हैं, लेकिन पक्की पुष्टि डॉक्टर की जांच के बाद ही होगी।"
                    title_c = "संभावित कारण (पुष्ट निदान नहीं)"
                else:
                    sp = f"Possible considerations include {', '.join(names)}, though only a clinician can confirm after examination."
                    title_c = "Possible Considerations (Not a Confirmed Diagnosis)"
                add_sec("causes", title_c, ", ".join(names), sp, causes)

        # 4. Recommended Doctor Specialty
        if specialty and specialty.get("primary_department"):
            doc_name = specialty.get("specialty") or specialty["primary_department"]
            reason = specialty.get("reason") or ""
            if clean == "bn":
                sp = f"আপনার লক্ষণগুলো বিবেচনা করে {specialty.get('primary_department_local') or doc_name} ({doc_name})-এর পরামর্শ নেওয়া যেতে পারে।"
                if reason:
                    sp += f" {reason}"
                title_d = "প্রস্তাবিত চিকিৎসক"
            elif clean == "hi":
                sp = f"आपके लक्षणों के आधार पर {doc_name} ({specialty['primary_department']}) से परामर्श लेना उचित रहेगा।"
                if reason:
                    sp += f" {reason}"
                title_d = "अनुशंसित डॉक्टर"
            else:
                sp = f"Based on your symptoms, consulting a {doc_name} ({specialty['primary_department']}) is recommended."
                if reason:
                    sp += f" {reason}"
                title_d = "Recommended Doctor"
            add_sec("specialist", title_d, f"{doc_name} — {reason}".strip(" —"), sp, [specialty])

        # 5. Suggested Investigations (clinician-considered)
        inv_items = list((investigations or {}).get("items") or structured.get("recommended_tests") or [])
        if inv_items:
            t_names = [str(i.get("test_name") or i.get("investigation_name") or "").strip() for i in inv_items]
            t_names = [n for n in t_names if n][:3]
            if t_names:
                if clean == "bn":
                    sp = f"আপনার চিকিৎসক প্রয়োজন মনে করলে {', '.join(t_names)} এবং অন্যান্য প্রয়োজনীয় পরীক্ষা বিবেচনা করতে পারেন।"
                    title_i = "সম্ভাব্য পরীক্ষা-নিরীক্ষা (চিকিৎসকের পরামর্শ সাপেক্ষে)"
                elif clean == "hi":
                    sp = f"यदि आपके डॉक्टर आवश्यक समझें तो {', '.join(t_names)} जैसी जांचें कराने की सलाह दे सकते हैं।"
                    title_i = "सुझाई गई जांचें (डॉक्टर के निर्णय के अनुसार)"
                else:
                    sp = f"Your doctor may consider tests such as {', '.join(t_names)} depending on clinical examination."
                    title_i = "Suggested Investigations (For Clinician Consideration)"
                add_sec("investigations", title_i, ", ".join(t_names), sp, inv_items)

        # 6. Dietary & Lifestyle Guidance
        if diet:
            helpful = list(diet.get("suitable_foods") or diet.get("helpful_foods") or [])
            limit = list(diet.get("foods_to_limit") or [])
            hydration = str(diet.get("hydration_guidance") or diet.get("hydration") or "")
            if helpful or limit or hydration:
                if clean == "bn":
                    sp = "খাদ্য ও জীবনযাত্রা নির্দেশনা: "
                    if helpful:
                        sp += f"সহনশীলতা অনুযায়ী {helpful[0]} খেতে পারেন। "
                    if limit:
                        sp += f"সীমিত রাখুন: {limit[0]}। "
                    if hydration:
                        sp += hydration
                    title_f = "খাবার ও জীবনযাত্রার পরামর্শ"
                elif clean == "hi":
                    sp = "खान-पान और जीवनशैली सलाह: "
                    if helpful:
                        sp += f"सुपाच्य आहार में {helpful[0]} ले सकते हैं। "
                    if limit:
                        sp += f"इनसे बचें: {limit[0]}। "
                    if hydration:
                        sp += hydration
                    title_f = "आहार और जीवनशैली मार्गदर्शन"
                else:
                    sp = "Food & lifestyle guidance: "
                    if helpful:
                        sp += f"Consider tolerable foods like {helpful[0]}. "
                    if limit:
                        sp += f"Limit {limit[0]}. "
                    if hydration:
                        sp += hydration
                    title_f = "Dietary & Lifestyle Guidance"
                screen_f = "\n".join([*(f"+ {x}" for x in helpful[:2]), *(f"- {x}" for x in limit[:2]), hydration]).strip()
                add_sec("diet", title_f, screen_f, sp, [diet])

        # 7. Follow-up Question (strictly 1 question spoken at a time in voice mode)
        fq = _clean_speech(followup_question or "")
        if not fq:
            fqs = list(structured.get("follow_up_questions") or [])
            if fqs:
                fq = _clean_speech(str(fqs[0]))
        if fq:
            if clean == "bn":
                sp_q = f"আরও স্পষ্টতার জন্য বলবেন: {fq}"
                title_q = "পরবর্তী প্রশ্ন"
            elif clean == "hi":
                sp_q = f"अधिक स्पष्टता के लिए बताएं: {fq}"
                title_q = "अगला प्रश्न"
            else:
                sp_q = f"To help clarify: {fq}"
                title_q = "Follow-up Question"
            add_sec("followup", title_q, fq, sp_q, [fq])

        # Deduplicate identical segments while keeping order
        dedup_segments: list[dict[str, str]] = []
        seen_txt: set[str] = set()
        for seg in segments:
            key = seg["text"].strip().lower()
            if key and key not in seen_txt:
                seen_txt.add(key)
                dedup_segments.append(seg)

        spoken_full = " ".join(seg["text"] for seg in dedup_segments).strip()
        if len(spoken_full) > 1200:
            spoken_full = spoken_full[:1197].rsplit(" ", 1)[0] + "."

        structured["sync_sections"] = sections
        structured["spoken_segments"] = dedup_segments
        structured["spoken_response"] = spoken_full
        return {
            "sync_sections": sections,
            "spoken_segments": dedup_segments,
            "spoken_response": spoken_full,
        }
