from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "synapse-api"}


@router.get("/model-tiers")
def list_model_tiers():
    """Return available model tiers for the configured provider."""
    from app.config import get_available_tiers
    return get_available_tiers()
