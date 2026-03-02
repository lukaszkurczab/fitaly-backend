from fastapi import APIRouter, HTTPException, status

from app.schemas.logs import ErrorLogRequest
from app.services import error_logger

router = APIRouter()


@router.post("/logs/error", status_code=status.HTTP_201_CREATED)
def create_error_log(request: ErrorLogRequest) -> dict[str, str]:
    try:
        error_logger.log_error(
            request.message,
            source=request.source,
            stack=request.stack,
            context=request.context,
            userId=request.userId,
        )
        return {"detail": "logged"}
    except Exception as exc:
        error_logger.capture_exception(exc)
        raise HTTPException(status_code=500, detail="Failed to log error") from exc
