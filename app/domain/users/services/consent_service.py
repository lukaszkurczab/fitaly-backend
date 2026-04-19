from app.core.errors import ConsentRequiredError
from app.domain.users.services.user_profile_service import UserProfileService


class ConsentService:
    def __init__(self, user_profile_service: UserProfileService) -> None:
        self.user_profile_service = user_profile_service

    async def has_ai_health_data_consent(self, *, user_id: str) -> bool:
        profile = await self.user_profile_service.get_profile(user_id=user_id)
        if profile is None:
            return False
        if profile.ai_health_data_consent_at:
            return True
        # Transitional compatibility for old profiles.
        return profile.survey_completed

    async def ensure_ai_health_data_consent(self, *, user_id: str) -> None:
        has_consent = await self.has_ai_health_data_consent(user_id=user_id)
        if not has_consent:
            raise ConsentRequiredError("AI health data consent required.")
