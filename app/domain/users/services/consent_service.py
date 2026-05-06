from app.core.errors import ConsentRequiredError
from app.domain.users.services.user_profile_service import UserProfileService
from app.services import user_account_service


class ConsentService:
    def __init__(self, user_profile_service: UserProfileService) -> None:
        self.user_profile_service = user_profile_service

    async def has_ai_health_data_consent(self, *, user_id: str) -> bool:
        profile = await self.user_profile_service.get_profile(user_id=user_id)
        if profile is None:
            return False
        return profile.is_ready and profile.has_ai_health_data_consent

    async def ensure_ai_health_data_consent(self, *, user_id: str) -> None:
        has_consent = await self.has_ai_health_data_consent(user_id=user_id)
        if not has_consent:
            raise ConsentRequiredError("AI health data consent required.")

    async def grant_ai_health_data_consent(
        self,
        *,
        user_id: str,
        auth_email: str | None = None,
    ) -> dict[str, object]:
        return await user_account_service.record_ai_health_data_consent(
            user_id,
            auth_email=auth_email,
        )
