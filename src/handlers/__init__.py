from aiogram import Router

from . import ai, boxes, photos, search, statuses


def setup_routers() -> Router:
    router = Router()
    router.include_router(boxes.router)
    router.include_router(search.router)
    router.include_router(photos.router)
    router.include_router(statuses.router)
    router.include_router(ai.router)
    return router
