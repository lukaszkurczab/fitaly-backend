from app.db import firebase


def test_get_firestore_uses_initialized_firebase_app(mocker) -> None:
    firebase.get_firestore.cache_clear()

    app = object()
    client = object()

    init_firebase = mocker.patch("app.db.firebase.init_firebase", return_value=app)
    firestore_client = mocker.patch(
        "app.db.firebase.admin_firestore.client",
        return_value=client,
    )

    result = firebase.get_firestore()

    init_firebase.assert_called_once_with()
    firestore_client.assert_called_once_with(app=app)
    assert result is client

    firebase.get_firestore.cache_clear()
