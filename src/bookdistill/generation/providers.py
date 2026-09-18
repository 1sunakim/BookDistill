"""Deterministic provider used by tests and local integration."""

from __future__ import annotations

from collections.abc import Callable
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bookdistill.contracts.generation import GenerationConfig, ModelUsage

from .errors import ProviderConfigurationError, ProviderRequestError
from .ports import ProviderResult


class StaticLLMProvider:
    provider_name = "static"
    model_name = "static-test-model"

    def __init__(self, responses: list[str] | Callable[[str, str], str]) -> None:
        self._responses = responses
        self.calls = 0

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, object],
        generation_config: GenerationConfig,
    ) -> ProviderResult:
        del output_schema, generation_config
        self.calls += 1
        if callable(self._responses):
            content = self._responses(system_prompt, user_prompt)
        else:
            if not self._responses:
                raise RuntimeError("StaticLLMProvider has no response configured")
            content = self._responses.pop(0)
        return ProviderResult(content=content, request_id=f"static-{self.calls}")


class OpenAICompatibleProvider:
    """Chat Completions adapter usable with OpenAI-compatible vendors.

    The adapter uses the standard library so vendor SDK types never leak into
    BookDistill. ``base_url`` should include the API version, for example
    ``https://api.openai.com/v1``.
    """

    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        response_format: str = "json_schema",
    ) -> None:
        if not model_name:
            raise ProviderConfigurationError("BOOKDISTILL_LLM_MODEL is required.")
        if not api_key:
            raise ProviderConfigurationError("BOOKDISTILL_LLM_API_KEY is required.")
        if response_format not in {"json_schema", "json_object"}:
            raise ProviderConfigurationError(
                "BOOKDISTILL_LLM_RESPONSE_FORMAT must be json_schema or json_object."
            )
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.response_format = response_format

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, object],
        generation_config: GenerationConfig,
    ) -> ProviderResult:
        response_format: dict[str, object]
        if self.response_format == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "bookdistill_response",
                    "strict": True,
                    "schema": output_schema,
                },
            }
        else:
            response_format = {"type": "json_object"}
        body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": generation_config.temperature,
            "max_tokens": generation_config.max_output_tokens,
            "response_format": response_format,
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            raise ProviderRequestError(
                f"LLM provider returned HTTP {error.code}.",
                retryable=retryable,
                status_code=error.code,
            ) from error
        except (URLError, TimeoutError) as error:
            raise ProviderRequestError(
                "LLM provider could not be reached.", retryable=True
            ) from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProviderRequestError(
                "LLM provider returned an unreadable response.", retryable=False
            ) from error

        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
            usage = payload.get("usage") or {}
            return ProviderResult(
                content=content,
                finish_reason=choice.get("finish_reason", "unknown"),
                request_id=str(payload.get("id", "unknown")),
                usage=ModelUsage(
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                ),
            )
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderRequestError(
                "LLM provider response did not match Chat Completions format.",
                retryable=False,
            ) from error
