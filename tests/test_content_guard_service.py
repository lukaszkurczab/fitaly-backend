from app.services.content_guard_service import has_nutrition_signal, is_off_topic


def test_is_off_topic_returns_false_for_nutrition_prompt() -> None:
    assert is_off_topic("Ile kalorii ma jablko?", "pl") is False


def test_is_off_topic_returns_false_for_polish_meal_terms_with_diacritics() -> None:
    assert is_off_topic("Zaproponuj wysokobiałkową kolację.", "pl") is False


def test_is_off_topic_returns_true_for_clear_off_topic_prompt() -> None:
    assert is_off_topic("Jaka bedzie pogoda jutro w Warszawie?", "pl") is True


def test_is_off_topic_returns_true_for_out_of_scope_prompt_without_keywords() -> None:
    assert is_off_topic("Flaga Boliwii", "pl") is True


def test_is_off_topic_uses_english_fallback_for_unknown_language() -> None:
    assert is_off_topic("Will it rain tomorrow?", "de") is True


def test_has_nutrition_signal_detects_polish_stems() -> None:
    assert has_nutrition_signal("Mam pomysł na kolację", "pl") is True
