from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/health", tags=["metrics"])


@router.get("/")
async def check_health():
    return JSONResponse(status_code=200, content={"message": "ok"})
