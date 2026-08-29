from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["metrics"])


@router.get("/")
async def check_health(request: Request):
    database = getattr(request.app.state, "database", None)
    db_ok = await database.health_check() if database is not None else False
    status_code = 200 if db_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if db_ok else "degraded", "database": db_ok},
    )
