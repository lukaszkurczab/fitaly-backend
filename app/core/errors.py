class DomainError(Exception):
    code: str = "domain_error"
    status_code: int = 400

class ConsentRequiredError(DomainError):
    code = "ai_health_data_consent_required"
    status_code = 403

class OutOfScopeError(DomainError):
    code = "chat_out_of_scope"
    status_code = 400

class InvalidPlannerOutputError(DomainError):
    code = "invalid_planner_output"
    status_code = 502

class ToolExecutionError(DomainError):
    code = "tool_execution_failed"
    status_code = 500

class AiProviderError(DomainError):
    code = "ai_provider_failed"
    status_code = 502


class AiProviderRetryableError(AiProviderError):
    code = "ai_provider_retryable_failed"
    status_code = 503


class AiProviderNonRetryableError(AiProviderError):
    code = "ai_provider_non_retryable_failed"
    status_code = 502
