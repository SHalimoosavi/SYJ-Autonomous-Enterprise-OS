"""
Versioned REST routes. Plain Starlette Route objects (no FastAPI
APIRouter/Depends) -- see app/main.py for why FastAPI itself is off the
Termux dev path.
"""
from starlette.responses import JSONResponse
from starlette.routing import Route


async def health(request):
    return JSONResponse({"status": "ok", "service": "SAEOS"})


routes = [
    Route("/api/v1/health", health),
]
