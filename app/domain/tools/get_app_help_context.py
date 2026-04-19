from app.domain.tools.base import DomainTool
from app.schemas.ai_chat.tools import AppHelpContextDto


class GetAppHelpContextTool(DomainTool):
    name = "get_app_help_context"

    def __init__(self) -> None:
        self._knowledge_base: dict[str, list[str]] = {
            "meal_logging": [
                "Posilek dodasz przez ekran Meals i przycisk dodawania.",
                "Mozesz dodac posilek manualnie albo przez analize zdjecia.",
                "Edycja i usuwanie posilkow wplywa na statystyki dnia i tygodnia.",
            ],
            "calorie_target": [
                "Cel kalorii ustawisz w profilu uzytkownika.",
                "Zmiana calorieTarget wplywa na porownania postepu i podsumowania.",
            ],
            "profile": [
                "W profilu ustawisz cel, poziom aktywnosci i preferencje zywieniowe.",
                "Jezyk aplikacji wplywa na jezyk odpowiedzi AI Chat v2.",
            ],
            "default": [
                "AI Chat v2 odpowiada na podstawie danych uzytkownika z backendu.",
                "Przy niskim pokryciu logowania posilkow odpowiedzi zawieraja ostrzezenie o jakosci danych.",
            ],
        }

    async def execute(self, *, user_id: str, args: dict) -> dict:
        del user_id
        topic = str(args.get("topic") or "default").strip().lower() or "default"
        facts = self._knowledge_base.get(topic, self._knowledge_base["default"])
        dto = AppHelpContextDto.model_validate({"topic": topic, "answerFacts": facts})
        return dto.model_dump(by_alias=True)
