"""Provider-neutral gateway with one controlled structure-repair attempt."""

from __future__ import annotations

import json
from time import perf_counter
from typing import TypeVar

from pydantic import ValidationError

from pydantic import BaseModel
from bookdistill.contracts.generation import LLMRequest, LLMResponse
from .errors import InvalidModelOutputError
from .ports import LLMProvider
from .prompts import PromptRegistry


DraftT = TypeVar("DraftT", bound=BaseModel)


class LLMGateway:
    def __init__(self, provider: LLMProvider, prompts: PromptRegistry) -> None:
        self._provider = provider
        self._prompts = prompts

    def generate(
        self, request: LLMRequest, response_model: type[DraftT]
    ) -> tuple[LLMResponse, DraftT]:
        prompt = self._prompts.get(request.task_type, request.prompt_version)
        system_prompt, user_prompt = prompt.render(request)
        started = perf_counter()
        result = self._provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_schema=request.output_schema,
            generation_config=request.generation_config,
        )
        repair_attempted = False
        try:
            parsed, draft = self._parse(result.content, response_model)
        except (json.JSONDecodeError, ValidationError, ValueError) as first_error:
            repair_attempted = True
            result = self._provider.complete(
                system_prompt=(
                    "Repair the response into valid JSON matching the supplied schema. "
                    "Do not add facts or citations. Return JSON only."
                ),
                user_prompt=result.content,
                output_schema=request.output_schema,
                generation_config=request.generation_config,
            )
            try:
                parsed, draft = self._parse(result.content, response_model)
            except (json.JSONDecodeError, ValidationError, ValueError) as second_error:
                raise InvalidModelOutputError(
                    f"model output failed schema validation after repair: {second_error}",
                    repair_attempted=True,
                ) from first_error

        response = LLMResponse(
            task_type=request.task_type,
            content=parsed,
            finish_reason=result.finish_reason,
            model_provider=self._provider.provider_name,
            model_name=self._provider.model_name,
            prompt_version=request.prompt_version,
            usage=result.usage,
            latency_ms=(perf_counter() - started) * 1000,
            request_id=result.request_id,
            raw_response_reference=result.raw_response_reference,
            repair_attempted=repair_attempted,
        )
        return response, draft

    @staticmethod
    def _parse(
        content: str, response_model: type[DraftT]
    ) -> tuple[dict[str, object], DraftT]:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("model output must be a JSON object")
        return parsed, response_model.model_validate(parsed)
