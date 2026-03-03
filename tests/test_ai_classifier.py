import pytest

pytest.importorskip("sklearn")
pytest.importorskip("joblib")

from app.services.ai_classifier import AiClassifier


def test_ai_classifier_learns_to_separate_on_topic_and_off_topic() -> None:
    classifier = AiClassifier()
    classifier.train(
        texts=[
            "ile kalorii ma jablko",
            "podaj makro dla kurczaka",
            "jaka bedzie pogoda jutro",
            "wynik meczu wczoraj",
        ],
        labels=[1, 1, 0, 0],
    )

    on_topic_probability = classifier.predict("kalorie i bialko dla owsianki")
    off_topic_probability = classifier.predict("prognoza pogody i polityka")

    assert 0.0 <= on_topic_probability <= 1.0
    assert 0.0 <= off_topic_probability <= 1.0
    assert on_topic_probability > off_topic_probability
    assert on_topic_probability > 0.5
    assert off_topic_probability <= 0.5


def test_ai_classifier_can_save_and_load_model(tmp_path) -> None:
    classifier = AiClassifier()
    classifier.train(
        texts=[
            "meal calories protein",
            "food macros apple",
            "weather tomorrow rain",
            "latest politics news",
        ],
        labels=[1, 1, 0, 0],
    )
    model_path = tmp_path / "ai_gateway_classifier.joblib"

    classifier.save_model(model_path)

    loaded_classifier = AiClassifier()
    loaded_classifier.load_model(model_path)

    original_prediction = classifier.predict("protein and calories for rice")
    loaded_prediction = loaded_classifier.predict("protein and calories for rice")

    assert loaded_prediction == pytest.approx(original_prediction, rel=1e-6)
