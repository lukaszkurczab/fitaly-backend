from app.services.ai_chat_prompt_service import build_chat_prompt


def test_build_chat_prompt_derives_policy_from_raw_context() -> None:
    prompt = build_chat_prompt(
        "What should I eat tonight?",
        {
            "language": "en",
            "profile": {
                "preferences": ["highProtein", "glutenFree"],
                "allergies": ["gluten"],
                "activityLevel": "moderate",
                "goal": "maintain",
                "sex": "male",
                "age": "31",
                "height": "182",
                "weight": "82",
                "unitsSystem": "metric",
                "calorieTarget": 2200,
                "aiStyle": "friendly",
                "aiFocus": "mealPlanning",
                "aiNote": "Please avoid long intros.",
            },
            "meals": [
                {
                    "timestamp": "2026-03-03T10:00:00.000Z",
                    "name": "Pasta",
                }
            ],
            "history": [
                {"from": "user", "text": "I want something light"},
                {"from": "ai", "text": "Try more vegetables"},
            ],
        },
        language="en",
    )

    assert "Reply in en." in prompt
    assert "TONE=F" in prompt
    assert "FOCUS=MP" in prompt
    assert "Tone guidance: friendly." in prompt
    assert "Focus guidance: meal planning." in prompt
    assert "FLAGS=highProtein,glutenFree" in prompt
    assert "AVOID=pszenica,jeczmien,zyto,makaron pszenny,pieczywo pszenne" in prompt
    assert "USER_NOTE=Please avoid long intros." in prompt
    assert "PROFILE=g=maintain" in prompt
    assert "MEALS=1|2026-03-03:Pasta" in prompt
    assert "HISTORY=user: I want something light | ai: Try more vegetables" in prompt
    assert "USER_MESSAGE=What should I eat tonight?" in prompt


def test_build_chat_prompt_supports_legacy_context_shape() -> None:
    prompt = build_chat_prompt(
        "Suggest dinner",
        {
            "flags": ["highProtein"],
            "avoid": ["sugar"],
            "tone": "C",
            "focus": "QA",
            "profile": "g=maintain; kcal=2200",
            "mealsSummary": "2|2026-03-03:Pasta",
            "history": ["Question one", "Question two"],
        },
        language="en",
    )

    assert "TONE=C" in prompt
    assert "FOCUS=QA" in prompt
    assert "FLAGS=highProtein" in prompt
    assert "AVOID=sugar" in prompt
    assert "PROFILE=g=maintain; kcal=2200" in prompt
    assert "MEALS=2|2026-03-03:Pasta" in prompt
    assert "HISTORY=Question one | Question two" in prompt


def test_build_chat_prompt_marks_diet_recommendation_requests_as_in_scope() -> None:
    prompt = build_chat_prompt(
        "Jaką dietę polecasz?",
        {"language": "pl"},
        language="pl",
    )

    assert "Questions about diets, nutrition styles, foods, ingredients, meal ideas, eating habits, and general meal-planning are in scope." in prompt
    assert "Requests like 'What diet do you recommend?', 'Suggest a new diet', or 'How should I eat to lose weight?' are in scope and should be answered." in prompt
    assert "Mogę pomóc tylko w tematach żywienia, diety, jedzenia i posiłków." in prompt


def test_build_chat_prompt_treats_chat_history_meta_questions_as_in_scope() -> None:
    prompt = build_chat_prompt(
        "O co pytałem wcześniej?",
        {
            "history": [
                {"from": "user", "text": "Jak zjeść więcej białka?"},
                {"from": "ai", "text": "Dodaj jogurt skyr i jajka do śniadania."},
            ]
        },
        language="pl",
    )

    assert "Meta requests about this conversation are in scope" in prompt
    assert "If the user asks what they asked earlier, answer from HISTORY." in prompt
    assert (
        "Only for clearly unrelated non-diet topics (for example weather, crypto prices, horoscopes, sports scores), do not answer that topic"
        in prompt
    )
    assert "HISTORY=user: Jak zjeść więcej białka? | ai: Dodaj jogurt skyr i jajka do śniadania." in prompt
