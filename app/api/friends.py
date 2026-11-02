from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.database import models as db
from app.database.session import get_session

router = APIRouter(prefix="/friends", tags=["friends"])


class FriendRequestBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)


def _require_registered(user: User) -> uuid.UUID:
    if user.is_guest:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sign in to use friends.")
    return uuid.UUID(user.id)


@router.get("")
async def list_friends(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    user_id = _require_registered(user)
    rows = (await session.execute(
        select(db.FriendRequestModel, db.UserModel)
        .join(db.UserModel, or_(
            and_(db.FriendRequestModel.requester_id == user_id, db.UserModel.id == db.FriendRequestModel.recipient_id),
            and_(db.FriendRequestModel.recipient_id == user_id, db.UserModel.id == db.FriendRequestModel.requester_id),
        ))
        .where(or_(db.FriendRequestModel.requester_id == user_id, db.FriendRequestModel.recipient_id == user_id))
        .order_by(db.FriendRequestModel.created_at.desc())
    )).all()
    friends, incoming, outgoing = [], [], []
    for request, player in rows:
        item = {"request_id": str(request.id), "user_id": str(player.id), "username": player.username,
                "nationality": player.nationality or "global", "created_at": request.created_at.isoformat()}
        if request.status == "accepted": friends.append(item)
        elif request.recipient_id == user_id: incoming.append(item)
        else: outgoing.append(item)
    return {"friends": friends, "incoming_requests": incoming, "outgoing_requests": outgoing}


@router.get("/search")
async def search_players(username: str = Query(min_length=1, max_length=64), user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    user_id = _require_registered(user)
    query = username.strip().lower()
    if not query: return {"players": []}
    rows = (await session.execute(
        select(db.UserModel).where(db.UserModel.is_guest.is_(False), db.UserModel.id != user_id,
            func.lower(db.UserModel.username).like(f"%{query}%")).order_by(func.lower(db.UserModel.username)).limit(20)
    )).scalars().all()
    return {"players": [{"user_id": str(row.id), "username": row.username, "nationality": row.nationality or "global"} for row in rows]}


@router.post("/requests", status_code=status.HTTP_201_CREATED)
async def send_friend_request(body: FriendRequestBody, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    user_id = _require_registered(user)
    target = (await session.execute(select(db.UserModel).where(func.lower(db.UserModel.username) == body.username.strip().lower(), db.UserModel.is_guest.is_(False)))).scalar_one_or_none()
    if target is None: raise HTTPException(status_code=404, detail="Player not found.")
    if target.id == user_id: raise HTTPException(status_code=400, detail="You cannot add yourself.")
    existing = (await session.execute(select(db.FriendRequestModel).where(or_(
        and_(db.FriendRequestModel.requester_id == user_id, db.FriendRequestModel.recipient_id == target.id),
        and_(db.FriendRequestModel.requester_id == target.id, db.FriendRequestModel.recipient_id == user_id),
    )))).scalar_one_or_none()
    if existing:
        if existing.status == "accepted": raise HTTPException(status_code=409, detail="You are already friends.")
        if existing.recipient_id == user_id: raise HTTPException(status_code=409, detail="This player has already sent you a request.")
        raise HTTPException(status_code=409, detail="A friend request is already pending.")
    request = db.FriendRequestModel(requester_id=user_id, recipient_id=target.id)
    session.add(request)
    try: await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="A friend request is already pending.") from exc
    return {"request_id": str(request.id), "status": "pending", "username": target.username}


@router.post("/requests/{request_id}/accept")
async def accept_friend_request(request_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    user_id = _require_registered(user)
    request = await session.get(db.FriendRequestModel, request_id)
    if request is None or request.recipient_id != user_id or request.status != "pending": raise HTTPException(status_code=404, detail="Friend request not found.")
    request.status = "accepted"
    await session.commit()
    return {"request_id": str(request.id), "status": "accepted"}


@router.delete("/requests/{request_id}")
async def remove_friend_request(request_id: uuid.UUID, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)):
    user_id = _require_registered(user)
    request = await session.get(db.FriendRequestModel, request_id)
    if request is None or user_id not in (request.requester_id, request.recipient_id): raise HTTPException(status_code=404, detail="Friend request not found.")
    await session.delete(request)
    await session.commit()
    return {"status": "removed"}
