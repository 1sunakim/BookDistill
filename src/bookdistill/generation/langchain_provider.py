"""LangChain adapter for OpenAI-compatible chat completion endpoints."""

from __future__ import annotations

import hashlib
import json
from urllib.parse import urlsplit

from bookdistill.contracts.generation import GenerationConfig, ModelUsage

from .errors import ProviderConfigurationError, ProviderRequestError
from .ports import ProviderResult


class LangChainChatProvider:
    """Provider-neutral boundary around LangChain's OpenAI-compatible chat model."""

    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        model_name: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        response_format: str = "json_schema",
        structured_output_backend: str = "langchain",
    ) -> None:
        if not model_name:
            raise ProviderConfigurationError("BOOKDISTILL_LLM_MODEL is required.")
        if not api_key:
            raise ProviderConfigurationError("BOOKDISTILL_LLM_API_KEY is required.")
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ProviderConfigurationError("LLM Base URL must be a valid http(s) URL.")
        if response_format not in {"json_schema", "json_object", "tool_calling", "text_parser"}:
            raise ProviderConfigurationError(
                "Unsupported declared structured output capability."
            )
        if structured_output_backend not in {"legacy", "langchain"}:
            raise ProviderConfigurationError("Unsupported structured output backend.")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as error:
            raise ProviderConfigurationError(
                "LangChain adapter dependencies are missing; install the langchain extra."
            ) from error

        self.model_name = model_name
        self._response_format = response_format
        self._structured_output_backend = structured_output_backend
        self._chat_type = ChatOpenAI
        self._client_options = {
            "model": model_name, "api_key": api_key,
            "base_url": base_url.rstrip("/"), "timeout": timeout_seconds,
            "max_retries": 0,
        }

    def complete(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        output_schema: dict[str, object],
        generation_config: GenerationConfig,
    ) -> ProviderResult:
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages([
            ("system", "{system_prompt}"), ("human", "{user_prompt}"),
        ])
        messages = prompt.format_messages(
            system_prompt=system_prompt, user_prompt=user_prompt,
        )
        model = self._chat_type(
            **self._client_options,
            temperature=generation_config.temperature,
            max_tokens=generation_config.max_output_tokens,
        )
        try:
            if self._structured_output_backend == "legacy":
                response = self._legacy_model(model, output_schema).invoke(messages)
            elif self._response_format == "text_parser":
                response = model.invoke(messages)
            else:
                method = {
                    "json_schema": "json_schema", "json_object": "json_mode",
                    "tool_calling": "function_calling",
                }[self._response_format]
                response = model.with_structured_output(
                    output_schema, method=method, include_raw=True,
                ).invoke(messages)
        except Exception as error:
            status = _status_code(error)
            if status in {401, 403, 404}:
                raise ProviderConfigurationError(
                    f"LLM provider configuration was rejected (HTTP {status})."
                ) from None
            retryable = status == 429 or (status is not None and status >= 500)
            if status is not None:
                message = f"LLM provider returned HTTP {status}."
            elif _is_timeout(error):
                message = "LLM provider request timed out."
                retryable = True
            elif _is_connection_error(error):
                message = "LLM provider could not be reached."
                retryable = True
            else:
                message = "LLM provider request failed."
            raise ProviderRequestError(message, retryable=retryable, status_code=status) from None

        raw_response = response.get("raw") if isinstance(response, dict) else response
        parsed = response.get("parsed") if isinstance(response, dict) else None
        # Keep raw text on structured parsing failure so LLMGateway can apply its
        # existing single, constrained repair attempt.
        if hasattr(parsed, "model_dump"):
            parsed = parsed.model_dump(mode="json")
        content = (json.dumps(parsed, ensure_ascii=False) if isinstance(parsed, dict)
                   else _text_content(getattr(raw_response, "content", None)))
        if not content and self._response_format == "tool_calling":
            tool_calls = getattr(raw_response, "tool_calls", None) or []
            if tool_calls and isinstance(tool_calls[0], dict):
                arguments = tool_calls[0].get("args")
                if isinstance(arguments, dict):
                    content = json.dumps(arguments, ensure_ascii=False)
        if not content:
            raise ProviderRequestError(
                "LLM provider returned empty message content.", retryable=True
            )
        metadata = getattr(raw_response, "response_metadata", None) or {}
        usage = getattr(raw_response, "usage_metadata", None) or {}
        request_id = metadata.get("request_id") or metadata.get("id") or "unknown"
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        return ProviderResult(
            content=content,
            finish_reason=str(metadata.get("finish_reason") or "unknown"),
            request_id=str(request_id),
            usage=ModelUsage(
                input_tokens=input_tokens if isinstance(input_tokens, int) else None,
                output_tokens=output_tokens if isinstance(output_tokens, int) else None,
            ),
            raw_response_reference=(
                "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
            ),
        )

    def _legacy_model(self, model, output_schema: dict[str, object]):
        if self._response_format == "json_schema":
            return model.bind(response_format={
                "type": "json_schema", "json_schema": {
                    "name": "bookdistill_response", "strict": True,
                    "schema": output_schema,
                },
            })
        if self._response_format == "json_object":
            return model.bind(response_format={"type": "json_object"})
        if self._response_format == "tool_calling":
            return model.bind_tools([{
                "type": "function", "function": {
                    "name": "bookdistill_response", "strict": True,
                    "parameters": output_schema,
                },
            }], tool_choice="bookdistill_response")
        return model

def _text_content(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [
            item["text"] for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        text = "".join(parts).strip()
        return text or None
    return None


def _status_code(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    if value is None:
        value = getattr(error, "status_code", None)
    return value if isinstance(value, int) else None


def _is_timeout(error: Exception) -> bool:
    return "timeout" in type(error).__name__.lower() or "timeout" in str(error).lower()


def _is_connection_error(error: Exception) -> bool:
    name = type(error).__name__.lower()
    return "connect" in name or "connection" in name
