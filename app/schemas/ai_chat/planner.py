from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

CapabilityName = Literal[
    "resolve_time_scope",
    "get_profile_summary",
    "get_goal_context",
    "get_nutrition_period_summary",
    "compare_periods",
    "get_meal_logging_quality",
    "get_recent_chat_summary",
    "get_app_help_context",
]

class CapabilityPlanDto(BaseModel):
    name: CapabilityName
    priority: int
    args: Dict[str, Any] = Field(default_factory=dict)

class QueryUnderstandingDto(BaseModel):
    requires_user_data: bool = Field(alias="requiresUserData")
    requested_scope_label: Optional[str] = Field(default=None, alias="requestedScopeLabel")
    mixed_request: bool = Field(alias="mixedRequest")
    topics: List[str]

class PlannerResultDto(BaseModel):
    task_type: Literal[
        "data_grounded_answer",
        "mixed_capability_answer",
        "app_help_only",
        "out_of_scope_refusal",
        "follow_up_required",
    ] = Field(alias="taskType")
    query_understanding: QueryUnderstandingDto = Field(alias="queryUnderstanding")
    capabilities: List[CapabilityPlanDto]
    response_mode: Literal[
        "concise_answer",
        "assessment_plus_guidance",
        "comparison_plus_guidance",
        "refusal_redirect",
    ] = Field(alias="responseMode")
    needs_follow_up: bool = Field(alias="needsFollowUp")
    follow_up_question: Optional[str] = Field(default=None, alias="followUpQuestion")