"""Helpers for blocking disallowed prompt topics.

The blocked keyword list is intentionally small for now and can be expanded
later as moderation rules evolve.
"""

import re
import unicodedata

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
        "kalori",
        "kcal",
        "bialk",
        "tluszcz",
        "weglowodan",
        "makro",
        "posilk",
        "kolac",
        "obiad",
        "sniadan",
        "przekask",
        "przepis",
        "jadlospis",
        "jedzenie",
        "produkt",
        "skladnik",
        "odzyw",
        "dieta",
    ),
    "en": (
        "calori",
        "kcal",
        "protein",
        "fat",
        "carbs",
        "macro",
        "meal",
        "breakfast",
        "lunch",
        "dinner",
        "snack",
        "recipe",
        "menu",
        "food",
        "ingredient",
        "nutrition",
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
        "kryptowalut",
        "flaga",
        "stolica",
        "panstwo",
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
        "flag",
        "capital",
        "country",
    ),
}


def _normalize_message(message: str) -> str:
    normalized = unicodedata.normalize("NFKD", message.lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _tokenize(message: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", _normalize_message(message)))


def _contains_keyword(tokens: tuple[str, ...], keywords: tuple[str, ...]) -> bool:
    return any(token.startswith(keyword) for token in tokens for keyword in keywords)


def has_nutrition_signal(message: str, language: str = "pl") -> bool:
    normalized_message = message.strip()
    if not normalized_message:
        return False

    language_key = language.lower()
    nutrition_keywords = NUTRITION_KEYWORDS_BY_LANGUAGE.get(
        language_key,
        NUTRITION_KEYWORDS_BY_LANGUAGE["en"],
    )
    tokens = _tokenize(normalized_message)
    if not tokens:
        return False

    return _contains_keyword(tokens, nutrition_keywords)


def is_off_topic(message: str, language: str = "pl") -> bool:
    """Return ``True`` when the prompt looks unrelated to food or nutrition."""
    normalized_message = message.strip()
    if not normalized_message:
        return False

    language_key = language.lower()
    off_topic_keywords = OFF_TOPIC_KEYWORDS_BY_LANGUAGE.get(
        language_key,
        OFF_TOPIC_KEYWORDS_BY_LANGUAGE["en"],
    )
    tokens = _tokenize(normalized_message)
    if not tokens:
        return False

    if has_nutrition_signal(normalized_message, language):
        return False

    if _contains_keyword(tokens, off_topic_keywords):
        return True

    # Strict domain gate: if the message has no food/nutrition signal, reject it.
    return True


def check_allowed(message: str) -> None:
    """Raise when the message contains blocked medical keywords."""
    normalized_message = message.lower()
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", normalized_message):
            raise ContentBlockedError("Query contains medical terms not allowed")

    return None
