"""Read-only LLM research reviews for paper-trading diagnostics."""

from trading_bot.research_bot.analyzer import ResearchInput, build_research_input_from_audit_log
from trading_bot.research_bot.attribution import (
    AttributionSummary,
    build_paper_strategy_attribution,
)
from trading_bot.research_bot.chat_assistant import (
    StrategyChangeProposal,
    StrategyChatAssistant,
    StrategyChatMessage,
    StrategyChatResponse,
    build_strategy_chat_prompt,
)
from trading_bot.research_bot.exporter import (
    ChatGPTResearchExportWriter,
    build_chatgpt_markdown_export,
)
from trading_bot.research_bot.openai_client import (
    DEFAULT_RESEARCH_MODEL,
    OpenAIResearchClient,
    OpenAIResearchClientError,
)
from trading_bot.research_bot.reviewer import (
    ResearchReportWriter,
    ResearchReviewArtifact,
    ResearchReviewClient,
    ResearchReviewer,
)
from trading_bot.research_bot.schemas import (
    BacktestTask,
    ResearchHypothesis,
    ResearchReviewReport,
)

__all__ = [
    "BacktestTask",
    "AttributionSummary",
    "ChatGPTResearchExportWriter",
    "OpenAIResearchClient",
    "OpenAIResearchClientError",
    "DEFAULT_RESEARCH_MODEL",
    "ResearchHypothesis",
    "ResearchInput",
    "ResearchReportWriter",
    "ResearchReviewArtifact",
    "ResearchReviewClient",
    "ResearchReviewReport",
    "ResearchReviewer",
    "StrategyChatAssistant",
    "StrategyChatMessage",
    "StrategyChatResponse",
    "StrategyChangeProposal",
    "build_chatgpt_markdown_export",
    "build_paper_strategy_attribution",
    "build_strategy_chat_prompt",
    "build_research_input_from_audit_log",
]
