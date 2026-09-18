"""Configuration-driven generation composition."""

from __future__ import annotations

from bookdistill.settings import Settings

from .errors import ProviderConfigurationError
from .gateway import LLMGateway
from .local_provider import TransformersLocalProvider
from .prompts import default_prompt_registry
from .providers import OpenAICompatibleProvider
from .distillation import DistillationService
from .service import RagAnswerService


def build_generation_service(settings: Settings) -> RagAnswerService | None:
    gateway = _build_gateway(settings)
    return RagAnswerService(gateway) if gateway is not None else None


def build_distillation_service(settings: Settings) -> DistillationService | None:
    gateway = _build_gateway(settings)
    return DistillationService(gateway) if gateway is not None else None


def _build_gateway(settings: Settings) -> LLMGateway | None:
    if settings.llm_provider == "disabled":
        return None
    if settings.llm_provider == "transformers-local":
        if settings.llm_local_model_path is None:
            raise ProviderConfigurationError(
                "BOOKDISTILL_LLM_LOCAL_MODEL_PATH is required."
            )
        provider = TransformersLocalProvider(
            model_path=settings.llm_local_model_path,
            adapter_path=settings.llm_local_adapter_path,
            device=settings.llm_local_device,
            dtype=settings.llm_local_dtype,
            max_input_tokens=settings.llm_local_max_input_tokens,
        )
        return LLMGateway(provider, default_prompt_registry())
    if settings.llm_provider != "openai-compatible":
        raise ProviderConfigurationError(
            f"Unsupported BOOKDISTILL_LLM_PROVIDER: {settings.llm_provider}"
        )
    provider = OpenAICompatibleProvider(
        model_name=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        response_format=settings.llm_response_format,
    )
    return LLMGateway(provider, default_prompt_registry())
