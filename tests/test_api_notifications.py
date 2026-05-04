from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from app.main import app
from tests.types import AuthHeaders

client = TestClient(app)


def test_legacy_notification_surfaces_are_removed(
    auth_headers: AuthHeaders,
) -> None:
    assert client.get(
        "/api/v1/users/me/notifications",
        headers=auth_headers("user-1"),
    ).status_code == 404
    assert client.post(
        "/api/v1/users/me/notifications",
        json={},
        headers=auth_headers("user-1"),
    ).status_code == 404
    assert client.post(
        "/api/v1/users/me/notifications/reconcile-plan",
        json={
            "startIso": "2026-03-03T00:00:00.000Z",
            "endIso": "2026-03-03T23:59:59.999Z",
        },
        headers=auth_headers("user-1"),
    ).status_code == 404
    assert client.post(
        "/api/v1/users/me/notifications/n-1/delete",
        headers=auth_headers("user-1"),
    ).status_code == 404


def test_get_notification_prefs_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    get_notification_prefs = mocker.patch(
        "app.api.routes.notifications.notification_service.get_notification_prefs",
        return_value={"smartRemindersEnabled": True, "motivationEnabled": True, "daysAhead": 7},
    )

    response = client.get(
        "/api/v1/users/me/notifications/preferences",
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "notifications": {
            "smartRemindersEnabled": True,
            "motivationEnabled": True,
            "statsEnabled": None,
            "weekdays0to6": None,
            "daysAhead": 7,
            "quietHours": None,
        },
    }
    get_notification_prefs.assert_called_once_with("user-1")


def test_post_notification_prefs_returns_backend_payload(
    mocker: MockerFixture,
    auth_headers: AuthHeaders,
) -> None:
    update_notification_prefs = mocker.patch(
        "app.api.routes.notifications.notification_service.update_notification_prefs",
        return_value={
            "smartRemindersEnabled": False,
            "motivationEnabled": False,
            "statsEnabled": True,
        },
    )

    response = client.post(
        "/api/v1/users/me/notifications/preferences",
        json={
            "notifications": {
                "smartRemindersEnabled": False,
                "motivationEnabled": False,
                "statsEnabled": True,
            }
        },
        headers=auth_headers("user-1"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "notifications": {
            "smartRemindersEnabled": False,
            "motivationEnabled": False,
            "statsEnabled": True,
            "weekdays0to6": None,
            "daysAhead": None,
            "quietHours": None,
        },
        "updated": True,
    }
    update_notification_prefs.assert_called_once_with(
        "user-1",
        {
            "smartRemindersEnabled": False,
            "motivationEnabled": False,
            "statsEnabled": True,
        },
    )
