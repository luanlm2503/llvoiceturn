from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
