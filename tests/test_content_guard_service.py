import pytest

from app.core.exceptions import ContentBlockedError
from app.services.content_guard_service import check_allowed


def test_check_allowed_accepts_regular_diet_prompt() -> None:
    check_allowed("Zaproponuj lekkostrawna kolacje")


@pytest.mark.parametrize(
    "message",
    ["therapy advice", "Czy ta choroba ma zwiazek z dieta?"],
)
def test_check_allowed_blocks_medical_keywords(message: str) -> None:
    with pytest.raises(ContentBlockedError):
        check_allowed(message)
