"""Configuration-driven generation composition."""

from __future__ import annotations

from contextvars import ContextVar
from threading import Lock

from bookdistill.model_config import ModelConfigStore
from bookdistill.settings import Settings

from .errors import ProviderConfigurationError
from .gateway import LLMGateway
from .local_provider import TransformersLocalProvider
from .prompts import default_prompt_registry
from .providers import DemoLLMProvider, OpenAICompatibleProvider
from .langchain_provider import LangChainChatProvider
from .ports import LLMProvider
from .distillation import DistillationService
from .service import RagAnswerService


def build_generation_service(
    settings: Settings, model_config: ModelConfigStore | None = None,
) -> RagAnswerService | None:
    gateway = _build_gateway(settings, model_config)
    return RagAnswerService(gateway) if gateway is not None else None


def build_distillation_service(
    settings: Settings, model_config: ModelConfigStore | None = None,
) -> DistillationService | None:
    gateway = _build_gateway(settings, model_config)
    return DistillationService(gateway) if gateway is not None else None


def _build_gateway(
    settings: Settings, model_config: ModelConfigStore | None = None,
) -> LLMGateway | None:
    if model_config is not None:
        return LLMGateway(_SavedModelProvider(settings, model_config), default_prompt_registry())
    provider = _build_provider(settings)
    return LLMGateway(provider, default_prompt_registry()) if provider else None


def _build_provider(settings: Settings) -> LLMProvider | None:
    if settings.llm_provider == "disabled":
        return None
    if settings.llm_provider == "demo":
        return DemoLLMProvider()
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
        return provider
    if settings.llm_provider != "openai-compatible":
        raise ProviderConfigurationError(
            f"Unsupported BOOKDISTILL_LLM_PROVIDER: {settings.llm_provider}"
        )
    if settings.llm_adapter not in {"legacy", "langchain"}:
        raise ProviderConfigurationError(
            "BOOKDISTILL_LLM_ADAPTER must be legacy or langchain."
        )
    if settings.structured_output_backend not in {"legacy", "langchain"}:
        raise ProviderConfigurationError(
            "BOOKDISTILL_STRUCTURED_OUTPUT_BACKEND must be legacy or langchain."
        )
    if settings.structured_output_backend == "langchain" and settings.llm_adapter != "langchain":
        raise ProviderConfigurationError(
            "LangChain structured output requires BOOKDISTILL_LLM_ADAPTER=langchain."
        )
    provider_type = (
        LangChainChatProvider if settings.llm_adapter == "langchain"
        else OpenAICompatibleProvider
    )
    provider = provider_type(
        model_name=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
        response_format=settings.llm_response_format,
        **({"structured_output_backend": settings.structured_output_backend}
           if settings.llm_adapter == "langchain" else {}),
    )
    return provider


class _SavedModelProvider:
    """Resolve the selected profile for each generation, including worker jobs."""

    def __init__(self, settings: Settings, store: ModelConfigStore) -> None:
        self.settings = settings
        self.store = store
        self._lock = Lock()
        self._cached_key: tuple | None = None
        self._cached_provider: LLMProvider | None = None
        self._last_provider: ContextVar[LLMProvider | None] = ContextVar(
            "bookdistill_active_provider", default=None
        )

    @property
    def provider_name(self) -> str:
        provider = self._last_provider.get()
        return provider.provider_name if provider else "unconfigured"

    @property
    def model_name(self) -> str:
        provider = self._last_provider.get()
        return provider.model_name if provider else "unconfigured"

    def complete(self, *, system_prompt, user_prompt, output_schema, generation_config):
        settings = self.store.effective_settings(self.settings)
        key = (
            settings.llm_provider, settings.llm_model, settings.llm_base_url,
            settings.llm_api_key, settings.llm_response_format,
            settings.llm_adapter, settings.structured_output_backend,
            settings.llm_timeout_seconds,
            settings.llm_local_model_path, settings.llm_local_adapter_path,
        )
        with self._lock:
            if key != self._cached_key:
                self._cached_provider = _build_provider(settings)
                self._cached_key = key
            provider = self._cached_provider
        if provider is None:
            raise ProviderConfigurationError("LLM provider is not configured.")
        self._last_provider.set(provider)
        return provider.complete(
            system_prompt=system_prompt, user_prompt=user_prompt,
            output_schema=output_schema, generation_config=generation_config,
        )
