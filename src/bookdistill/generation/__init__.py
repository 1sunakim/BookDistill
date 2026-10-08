"""Evidence-grounded generation MVP."""

from .errors import (
    GenerationError,
    InvalidCitationError,
    InvalidModelOutputError,
    ModelOutputTruncatedError,
    ModelStateConflictError,
    ProviderConfigurationError,
    ProviderRequestError,
)
from .factory import build_distillation_service, build_generation_service
from .gateway import LLMGateway
from .jobs import DistillationJobHandler, RagAnswerJobHandler
from .distillation import DistillationService
from .orchestration import RetrieveAndAnswerService
from .ports import LLMProvider, ProviderResult
from .prompts import PromptDefinition, PromptRegistry, default_prompt_registry
from .providers import DemoLLMProvider, OpenAICompatibleProvider, StaticLLMProvider
from .langchain_provider import LangChainChatProvider
from .local_provider import TransformersLocalProvider
from .service import RagAnswerService

__all__ = [
    "GenerationError",
    "InvalidCitationError",
    "InvalidModelOutputError",
    "ModelOutputTruncatedError",
    "ModelStateConflictError",
    "ProviderConfigurationError",
    "ProviderRequestError",
    "LLMGateway",
    "RagAnswerJobHandler",
    "DistillationJobHandler",
    "DistillationService",
    "RetrieveAndAnswerService",
    "LLMProvider",
    "PromptDefinition",
    "PromptRegistry",
    "ProviderResult",
    "RagAnswerService",
    "StaticLLMProvider",
    "DemoLLMProvider",
    "OpenAICompatibleProvider",
    "LangChainChatProvider",
    "TransformersLocalProvider",
    "build_generation_service",
    "build_distillation_service",
    "default_prompt_registry",
]
