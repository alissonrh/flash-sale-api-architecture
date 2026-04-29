from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/")
def root():
    return {
        "message": "Flash Sale API no ar",
        "docs": "/docs",
        "health": "/health",
    }


@router.get("/health")
def health():
    return {"status": "ok"}