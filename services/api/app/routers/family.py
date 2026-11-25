"""Family profiles: an account holder manages the health records of children, parents or a spouse.

Each member is a separate User with its own data-encryption key and its own rows, so the existing endpoints work
unchanged when the client sends `X-Profile-Id: <member id>` (see security/auth.current_user). Members never log
in. Consents and privacy settings stay with the account holder, who acts as guardian (DPDP Act s.9 for
children). Removing a member crypto-shreds their data.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, scope_to
from app.models import HouseholdMember, PatientProfile, User
from app.models import _now as utcnow
from app.routers.profile import erase_user
from app.security import consent
from app.security.auth import CurrentUser, current_user
from app.security.crypto import Cipher, new_wrapped_dek

router = APIRouter(prefix="/family", tags=["family"])

MAX_MEMBERS = 20
Relation = Literal["child", "parent", "spouse", "sibling", "grandparent", "other"]


class MemberIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    relation: Relation
    dob: date | None = None
    sex: Literal["male", "female", "intersex"] | None = None


class MemberPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=40)
    relation: Relation | None = None


def _age(dob: str | None) -> int | None:
    if not dob:
        return None
    try:
        return int((utcnow().date() - date.fromisoformat(dob)).days // 365.25)
    except ValueError:
        return None


async def _link(db: AsyncSession, cu: CurrentUser, member_id: str) -> HouseholdMember:
    link = (await db.scalars(select(HouseholdMember).where(HouseholdMember.user_id == cu.account_id,
                                                           HouseholdMember.member_id == member_id))).first()
    if link is None:
        raise HTTPException(404, "Not found")
    return link


@router.get("")
async def list_members(cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    aid, acipher = cu.account_id, cu.account_cipher
    links = list(await db.scalars(select(HouseholdMember).where(HouseholdMember.user_id == aid)
                                  .order_by(HouseholdMember.created_at)))
    out = []
    for link in links:
        await scope_to(db, link.member_id, aid)  # the member's profile row is only visible in their scope
        member = await db.get(User, link.member_id)
        p = await db.get(PatientProfile, link.member_id)
        dob = Cipher(member.dek_wrapped, member.id).dec(p.dob_enc) if (member and p and p.dob_enc) else None
        out.append({"id": link.member_id, "name": acipher.dec(link.name_enc), "relation": link.relation,
                    "age": _age(dob), "sex": p.sex if p else None})
    await scope_to(db, cu.id, aid)
    return out


@router.post("", status_code=201)
async def add_member(body: MemberIn, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    aid = cu.account_id
    if "core_service" not in await consent.active_purposes(db, aid):
        raise HTTPException(403, "Consent 'core_service' is required for this action")
    n = await db.scalar(select(func.count()).select_from(HouseholdMember).where(HouseholdMember.user_id == aid))
    if n >= MAX_MEMBERS:
        raise HTTPException(400, f"You can manage up to {MAX_MEMBERS} family profiles")
    account = cu.account or cu.user
    member = User(dek_wrapped=new_wrapped_dek(), preferred_lang=account.preferred_lang, is_guest=False)
    db.add(member)
    await db.flush()
    db.add(HouseholdMember(user_id=aid, member_id=member.id, name_enc=cu.account_cipher.enc(body.name),
                           relation=body.relation))
    if body.dob or body.sex:
        await scope_to(db, member.id, aid)
        mc = Cipher(member.dek_wrapped, member.id)
        db.add(PatientProfile(user_id=member.id, dob_enc=mc.enc(body.dob.isoformat()) if body.dob else None,
                              sex=body.sex))
    await db.commit()
    await scope_to(db, cu.id, aid)
    return {"id": member.id, "name": body.name, "relation": body.relation,
            "age": _age(body.dob.isoformat() if body.dob else None), "sex": body.sex}


@router.patch("/{member_id}")
async def edit_member(member_id: str, body: MemberPatch, cu: CurrentUser = Depends(current_user),
                      db: AsyncSession = Depends(get_db)):
    link = await _link(db, cu, member_id)
    if body.name is not None:
        link.name_enc = cu.account_cipher.enc(body.name)
    if body.relation is not None:
        link.relation = body.relation
    await db.commit()
    return {"id": member_id, "name": cu.account_cipher.dec(link.name_enc), "relation": link.relation}


@router.delete("/{member_id}", status_code=204)
async def remove_member(member_id: str, cu: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Removes the profile and permanently erases all of that person's health data."""
    aid = cu.account_id
    link = await _link(db, cu, member_id)
    await db.execute(delete(HouseholdMember).where(HouseholdMember.id == link.id))
    await scope_to(db, member_id, aid)
    await erase_user(db, member_id, aid)
    await db.commit()


class ResolveIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    lang: str = "bn"
    auto_create: bool = False


@router.post("/resolve")
async def resolve_member_endpoint(
    body: ResolveIn,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a family relationship mention (e.g. 'Amar baba') to an isolated member profile."""
    from app.clinical.family_memory import (
        FIRST_OPEN_PROMPT,
        get_member_current_user,
        get_member_memory_summary,
        resolve_family_member,
    )

    res = await resolve_family_member(
        db, cu, body.text, lang=body.lang, auto_create_if_missing=body.auto_create
    )
    summary = None
    if res.matched and res.member_id:
        mcu = await get_member_current_user(db, cu, res.member_id)
        if mcu:
            summary = await get_member_memory_summary(
                db, mcu, name=res.member_name or "", relation=res.relation or ""
            )
            await scope_to(db, cu.id, cu.account_id)
    return {
        "matched": res.matched,
        "member_id": res.member_id,
        "member_name": res.member_name,
        "relation": res.relation,
        "canonical_role": res.canonical_role,
        "ambiguous": res.ambiguous,
        "candidates": res.candidates,
        "clarification_question": res.clarification_question,
        "auto_created": res.auto_created,
        "first_open_prompt": FIRST_OPEN_PROMPT.get(body.lang, FIRST_OPEN_PROMPT["bn"]),
        "summary": summary,
    }


@router.get("/{member_id}/summary")
async def member_summary(
    member_id: str,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """View a specific family member's isolated health memory summary and vital history."""
    from app.clinical.family_memory import get_member_current_user, get_member_memory_summary

    link = await _link(db, cu, member_id)
    mcu = await get_member_current_user(db, cu, member_id)
    if mcu is None:
        raise HTTPException(404, "Not found")
    name = cu.account_cipher.dec(link.name_enc)
    out = await get_member_memory_summary(db, mcu, name=name, relation=link.relation)
    await scope_to(db, cu.id, cu.account_id)
    return out


@router.get("/{member_id}/export")
async def export_member_record(
    member_id: str,
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export a family member's complete encrypted health profile & vital history."""
    from app.clinical.family_memory import get_member_current_user, get_member_memory_summary

    link = await _link(db, cu, member_id)
    mcu = await get_member_current_user(db, cu, member_id)
    if mcu is None:
        raise HTTPException(404, "Not found")
    name = cu.account_cipher.dec(link.name_enc)
    summary = await get_member_memory_summary(db, mcu, name=name, relation=link.relation)
    await scope_to(db, cu.id, cu.account_id)
    return {"exported_at": utcnow().isoformat() + "Z", "member": summary}


@router.get("/graph")
async def get_family_health_graph(
    cu: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the structured Family Health Knowledge Graph (nodes, edges, patterns, GNN status)."""
    from app.clinical.family_graph import build_family_health_graph

    if "core_service" not in await consent.active_purposes(db, cu.account_id):
        raise HTTPException(403, "Consent 'core_service' is required")
    return await build_family_health_graph(db, cu)


@router.get("/gnn/evaluate")
async def evaluate_family_gnn(cu: CurrentUser = Depends(current_user)):
    """Run the extensible RelationalGraphSAGE vs rule-baseline benchmark on synthetic family graphs."""
    from app.clinical.family_graph import evaluate_gnn_vs_rule_baseline

    return evaluate_gnn_vs_rule_baseline()
