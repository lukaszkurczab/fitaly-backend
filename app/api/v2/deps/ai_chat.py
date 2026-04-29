"""Dependency graph for canonical AI Chat v2 orchestration."""

from functools import lru_cache

from app.core.openai_client import get_openai_client
from app.core.token_counter import TokenCounter
from app.db.firebase import get_firestore
from app.domain.ai_runs.services.ai_run_service import AiRunService
from app.domain.chat.context_builder import ContextBuilder
from app.domain.chat.generator import ChatGenerator
from app.domain.chat.orchestrator import ChatOrchestrator
from app.domain.chat.planner import ChatPlanner
from app.domain.chat.prompt_composer import PromptComposer
from app.domain.chat.retry_policy import RetryPolicy
from app.domain.chat_memory.services.message_service import MessageService
from app.domain.chat_memory.services.summary_service import SummaryService
from app.domain.chat_memory.services.thread_service import ThreadService
from app.domain.meals.services.meal_query_service import MealQueryService
from app.domain.meals.services.nutrition_summary_service import NutritionSummaryService
from app.domain.meals.services.period_comparison_service import PeriodComparisonService
from app.domain.tools.compare_periods import ComparePeriodsTool
from app.domain.tools.get_app_help_context import GetAppHelpContextTool
from app.domain.tools.get_goal_context import GetGoalContextTool
from app.domain.tools.get_meal_logging_quality import GetMealLoggingQualityTool
from app.domain.tools.get_nutrition_period_summary import GetNutritionPeriodSummaryTool
from app.domain.tools.get_profile_summary import GetProfileSummaryTool
from app.domain.tools.get_recent_chat_summary import GetRecentChatSummaryTool
from app.domain.tools.registry import ToolRegistry
from app.domain.tools.resolve_time_scope import ResolveTimeScopeTool
from app.domain.users.services.consent_service import ConsentService
from app.domain.users.services.user_profile_service import UserProfileService
from app.infra.firestore.repositories.ai_run_repository import AiRunRepository
from app.infra.firestore.repositories.chat_message_repository import ChatMessageRepository
from app.infra.firestore.repositories.chat_thread_repository import ChatThreadRepository
from app.infra.firestore.repositories.memory_summary_repository import MemorySummaryRepository
from app.services import ai_credits_service


@lru_cache(maxsize=1)
def _orchestrator_singleton() -> ChatOrchestrator:
    firestore_client = get_firestore()

    thread_repository = ChatThreadRepository(firestore_client)  # type: ignore[arg-type]
    message_repository = ChatMessageRepository(firestore_client)  # type: ignore[arg-type]
    summary_repository = MemorySummaryRepository(firestore_client)  # type: ignore[arg-type]
    run_repository = AiRunRepository(firestore_client)  # type: ignore[arg-type]

    thread_service = ThreadService(thread_repository)
    message_service = MessageService(message_repository, thread_service)
    summary_service = SummaryService(summary_repository)
    ai_run_service = AiRunService(run_repository)

    user_profile_service = UserProfileService()
    consent_service = ConsentService(user_profile_service)

    meal_query_service = MealQueryService(firestore_client)  # type: ignore[arg-type]
    nutrition_summary_service = NutritionSummaryService(meal_query_service)
    period_comparison_service = PeriodComparisonService(nutrition_summary_service)

    tool_registry = ToolRegistry(
        tools=[
            ResolveTimeScopeTool(),
            GetProfileSummaryTool(user_profile_service),
            GetGoalContextTool(user_profile_service),
            GetNutritionPeriodSummaryTool(nutrition_summary_service),
            ComparePeriodsTool(period_comparison_service),
            GetMealLoggingQualityTool(nutrition_summary_service),
            GetRecentChatSummaryTool(summary_service, message_service),
            GetAppHelpContextTool(),
        ]
    )

    openai_client = get_openai_client()
    planner = ChatPlanner(openai_client)
    token_counter = TokenCounter()
    context_builder = ContextBuilder(token_counter=token_counter)
    prompt_composer = PromptComposer()
    generator = ChatGenerator(openai_client)
    retry_policy = RetryPolicy()

    return ChatOrchestrator(
        consent_service=consent_service,
        thread_service=thread_service,
        message_service=message_service,
        summary_service=summary_service,
        ai_run_service=ai_run_service,
        planner=planner,
        tool_registry=tool_registry,
        context_builder=context_builder,
        prompt_composer=prompt_composer,
        generator=generator,
        retry_policy=retry_policy,
        credits_service=ai_credits_service,
    )


def get_chat_orchestrator() -> ChatOrchestrator:
    return _orchestrator_singleton()
