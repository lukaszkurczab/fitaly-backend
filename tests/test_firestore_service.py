import asyncio
from types import SimpleNamespace

from pytest_mock import MockerFixture

from app.services.firestore_service import get_document, set_document, update_document


def _build_firestore_chain():
    client = SimpleNamespace()
    collection_ref = SimpleNamespace()
    document_ref = SimpleNamespace()

    return client, collection_ref, document_ref


def test_get_document_returns_dict_when_document_exists(mocker: MockerFixture) -> None:
    client, collection_ref, document_ref = _build_firestore_chain()
    snapshot = mocker.Mock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {"name": "apple", "calories": 52}

    client.collection = mocker.Mock(return_value=collection_ref)
    collection_ref.document = mocker.Mock(return_value=document_ref)
    document_ref.get = mocker.Mock(return_value=snapshot)
    mocker.patch("app.db.firebase.get_firestore", return_value=client)

    result = asyncio.run(get_document("foods", "apple"))

    client.collection.assert_called_once_with("foods")
    collection_ref.document.assert_called_once_with("apple")
    document_ref.get.assert_called_once_with()
    assert result == {"name": "apple", "calories": 52}


def test_get_document_returns_none_when_document_does_not_exist(
    mocker: MockerFixture,
) -> None:
    client, collection_ref, document_ref = _build_firestore_chain()
    snapshot = mocker.Mock()
    snapshot.exists = False

    client.collection = mocker.Mock(return_value=collection_ref)
    collection_ref.document = mocker.Mock(return_value=document_ref)
    document_ref.get = mocker.Mock(return_value=snapshot)
    mocker.patch("app.db.firebase.get_firestore", return_value=client)

    result = asyncio.run(get_document("foods", "missing"))

    client.collection.assert_called_once_with("foods")
    collection_ref.document.assert_called_once_with("missing")
    document_ref.get.assert_called_once_with()
    assert result is None
    snapshot.to_dict.assert_not_called()


def test_set_document_calls_set_with_correct_data(mocker: MockerFixture) -> None:
    client, collection_ref, document_ref = _build_firestore_chain()
    payload = {"name": "banana", "calories": 89}

    client.collection = mocker.Mock(return_value=collection_ref)
    collection_ref.document = mocker.Mock(return_value=document_ref)
    document_ref.set = mocker.Mock()
    mocker.patch("app.db.firebase.get_firestore", return_value=client)

    asyncio.run(set_document("foods", "banana", payload))

    client.collection.assert_called_once_with("foods")
    collection_ref.document.assert_called_once_with("banana")
    document_ref.set.assert_called_once_with(payload)


def test_update_document_calls_update_with_correct_data(mocker: MockerFixture) -> None:
    client, collection_ref, document_ref = _build_firestore_chain()
    payload = {"calories": 90}

    client.collection = mocker.Mock(return_value=collection_ref)
    collection_ref.document = mocker.Mock(return_value=document_ref)
    document_ref.update = mocker.Mock()
    mocker.patch("app.db.firebase.get_firestore", return_value=client)

    asyncio.run(update_document("foods", "banana", payload))

    client.collection.assert_called_once_with("foods")
    collection_ref.document.assert_called_once_with("banana")
    document_ref.update.assert_called_once_with(payload)
