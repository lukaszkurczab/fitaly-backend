from app.services.content_guard_service import is_off_topic


def test_is_off_topic_returns_false_for_nutrition_prompt() -> None:
    assert is_off_topic("Ile kalorii ma jablko?", "pl") is False


def test_is_off_topic_returns_true_for_clear_off_topic_prompt() -> None:
    assert is_off_topic("Jaka bedzie pogoda jutro w Warszawie?", "pl") is True


def test_is_off_topic_uses_english_fallback_for_unknown_language() -> None:
    assert is_off_topic("Will it rain tomorrow?", "de") is True
