from app.services.sanitization_service import sanitize_request


def test_sanitize_request_masks_emails_and_coarsens_numbers() -> None:
    sanitized = sanitize_request("I am 31 and my email is foo@example.com")

    assert sanitized == "I am 30-40 and my email is [email]"
    