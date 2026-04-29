class DomainError(Exception):
    code: str = "domain_error"
    status_code: int = 400

class ConsentRequiredError(DomainError):
    code = "AI_CHAT_CONSENT_REQUIRED"
    status_code = 403

class OutOfScopeError(DomainError):
    code = "chat_out_of_scope"
    status_code = 400

class InvalidPlannerOutputError(DomainError):
    code = "AI_CHAT_CONTEXT_UNAVAILABLE"
    status_code = 503

class ToolExecutionError(DomainError):
    code = "AI_CHAT_CONTEXT_UNAVAILABLE"
    status_code = 503

class AiProviderError(DomainError):
    code = "AI_CHAT_PROVIDER_UNAVAILABLE"
    status_code = 503


class AiProviderTimeoutError(DomainError):
    code = "AI_CHAT_TIMEOUT"
    status_code = 504


class AiProviderRetryableError(AiProviderError):
    status_code = 503


class AiProviderNonRetryableError(AiProviderError):
    status_code = 503


class AiChatIdempotencyConflictError(DomainError):
    code = "AI_CHAT_IDEMPOTENCY_CONFLICT"
    status_code = 409


class AiCreditsExhaustedDomainError(DomainError):
    code = "AI_CREDITS_EXHAUSTED"
    status_code = 402

    def __init__(self, message: str, *, credits_status: object) -> None:
        super().__init__(message)
        self.credits_status = credits_status
