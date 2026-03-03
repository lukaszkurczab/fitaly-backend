"""Helpers for blocking disallowed prompt topics.

The blocked keyword list is intentionally small for now and can be expanded
later as moderation rules evolve.
"""

import re

from app.core.exceptions import ContentBlockedError

BLOCKED_KEYWORDS = [
    "medycyna",
    "choroba",
    "lek",
    "symptom",
    "medicine",
    "disease",
    "therapy",
]

NUTRITION_KEYWORDS_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "pl": (
        "kalorie",
        "kcal",
        "bialko",
        "bialka",
        "tluszcz",
        "tluszcze",
        "weglowodany",
        "makro",
        "posilek",
        "jedzenie",
        "produkt",
        "dieta",
    ),
    "en": (
        "calories",
        "kcal",
        "protein",
        "fat",
        "carbs",
        "macro",
        "meal",
        "food",
        "ingredient",
        "diet",
    ),
}

OFF_TOPIC_KEYWORDS_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "pl": (
        "pogoda",
        "polityka",
        "wybory",
        "mecz",
        "film",
        "serial",
        "programowanie",
        "kod",
        "bitcoin",
        "kryptowaluty",
    ),
    "en": (
        "weather",
        "rain",
        "politics",
        "election",
        "match",
        "movie",
        "series",
        "programming",
        "code",
        "bitcoin",
        "crypto",
    ),
}


def _contains_keyword(message: str, keywords: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(keyword)}\b", message) for keyword in keywords)


def is_off_topic(message: str, language: str = "pl") -> bool:
    """Return ``True`` when the prompt looks unrelated to food or nutrition."""
    normalized_message = message.lower().strip()
    if not normalized_message:
        return False

    language_key = language.lower()
    nutrition_keywords = NUTRITION_KEYWORDS_BY_LANGUAGE.get(
        language_key,
        NUTRITION_KEYWORDS_BY_LANGUAGE["en"],
    )
    off_topic_keywords = OFF_TOPIC_KEYWORDS_BY_LANGUAGE.get(
        language_key,
        OFF_TOPIC_KEYWORDS_BY_LANGUAGE["en"],
    )

    if _contains_keyword(normalized_message, nutrition_keywords):
        return False

    return _contains_keyword(normalized_message, off_topic_keywords)


def check_allowed(message: str) -> None:
    """Raise when the message contains blocked medical keywords."""
    normalized_message = message.lower()
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", normalized_message):
            raise ContentBlockedError("Query contains medical terms not allowed")

    return None
