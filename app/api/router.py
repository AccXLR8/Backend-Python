from fastapi import APIRouter

from app.api import auth, health, games, matchmaking, profiles, users, skins, friends, shop

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router)
api_router.include_router(games.router, prefix="/games", tags=["games"])
api_router.include_router(matchmaking.router, prefix="/matchmaking", tags=["matchmaking"])
api_router.include_router(profiles.router, prefix="/profiles", tags=["profiles"])
api_router.include_router(friends.router)
api_router.include_router(skins.router, prefix="/skins", tags=["skins"])
api_router.include_router(shop.router, prefix="/shop", tags=["shop"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
