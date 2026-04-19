from fastapi import APIRouter

from app.api.v2.endpoints.ai_chat import router as ai_chat_router
from app.api.routes.coach import router as coach_router
from app.api.routes.habits import router as habits_router
from app.api.routes.nutrition_state import router as nutrition_state_router
from app.api.routes.reminders import router as reminders_router
from app.api.routes.telemetry import router as telemetry_router
from app.api.routes.weekly_reports import router as weekly_reports_router

router = APIRouter()

router.include_router(telemetry_router, tags=["telemetry"], prefix="")

router.include_router(habits_router, tags=["habits"], prefix="")
router.include_router(nutrition_state_router, tags=["nutrition-state"], prefix="")
router.include_router(coach_router, tags=["coach"], prefix="")
router.include_router(reminders_router, tags=["reminders"], prefix="")
router.include_router(weekly_reports_router, tags=["weekly-reports"], prefix="")
router.include_router(ai_chat_router, prefix="")
