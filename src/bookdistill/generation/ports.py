"""Replaceable provider port; no vendor SDK is imported by the domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from bookdistill.contracts.generation import GenerationConfig, ModelUsage


@dataclass(frozen=True, slots=True)
class ProviderResult:
    content: str
    finish_reason: str = "stop"
    request_id: str = "local"
    usage: ModelUsage = field(default_factory=ModelUsage)
    raw_response_reference: str | None = None


class LLMProvider(Protocol):
    provider_name: str
    model_name: str

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, object],
        generation_config: GenerationConfig,
    ) -> ProviderResult: ...
