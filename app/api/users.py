from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.auth.models import User

router = APIRouter()


@router.get("/me")
async def read_me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "nationality": getattr(user, "nationality", "global") or "global"}
