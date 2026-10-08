"""Deterministic provider used by tests and local integration."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
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


class DemoLLMProvider:
    """Zero-credential extractive provider for the default end-to-end demo."""

    provider_name = "demo"
    model_name = "demo-extractive-v1"

    def complete(self, *, system_prompt: str, user_prompt: str,
                 output_schema: dict[str, object],
                 generation_config: GenerationConfig) -> ProviderResult:
        del system_prompt, generation_config
        data = json.loads(user_prompt)
        evidence = data.get("evidence", [])
        properties = output_schema.get("properties", {})
        if "answer" in properties:
            if not evidence:
                payload = {"answer": "Insufficient evidence / 证据不足。", "claims": [],
                           "insufficient_evidence": True, "follow_up": None}
            else:
                source = evidence[0]
                text = source["text"].strip()
                payload = {
                    "answer": text,
                    "claims": [{"text": text, "evidence_ids": [source["evidence_id"]]}],
                    "insufficient_evidence": False,
                    "follow_up": None,
                }
        elif "title" in properties and "sections" in properties:
            source = evidence[0]
            payload = {
                "title": data.get("title", "Summary"), "overview": source["text"],
                "sections": [{"heading": "Evidence summary", "content": source["text"],
                              "evidence_ids": [source["evidence_id"]]}],
                "key_takeaways": [{"text": source["text"],
                                     "evidence_ids": [source["evidence_id"]]}],
            }
        elif "points" in properties:
            source = evidence[0]
            payload = {"points": [{
                "concept": "Evidence-backed concept / 证据知识点",
                "definition": source["text"], "attributes": [], "prerequisites": [],
                "related_concepts": [], "misconceptions": [],
                "evidence_ids": [source["evidence_id"]],
            }]}
        elif "questions" in properties:
            source = evidence[0]
            requested_count = int(data.get("count", 1))
            question_type = (data.get("types") or ["concept"])[0]
            difficulty = data.get("difficulty", "medium")
            if difficulty == "mixed":
                difficulty = "medium"
            payload = {"questions": [{
                "stem": f"What does the cited passage state? / 引用段落表达了什么？ ({index + 1})",
                "question_type": question_type, "difficulty": difficulty,
                "options": [], "answer": source["text"],
                "explanation": source["text"],
                "evidence_ids": [source["evidence_id"]],
            } for index in range(requested_count)]}
        elif "per_book" in properties:
            books = data.get("books", [])
            payload = {
                "per_book": [{
                    "book_id": book["book_id"], "book_title": book["book_title"],
                    "viewpoint": book["evidence"][0]["text"],
                    "evidence_ids": [book["evidence"][0]["evidence_id"]],
                } for book in books],
                "commonalities": [], "differences": [], "applicability": [],
            }
        else:
            raise ProviderRequestError(
                "The demo provider does not support this output schema.", retryable=False
            )
        content = json.dumps(payload, ensure_ascii=False)
        return ProviderResult(
            content=content, request_id="demo-local",
            usage=ModelUsage(input_tokens=len(user_prompt), output_tokens=len(content)),
        )


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
        if response_format not in {"json_schema", "json_object", "tool_calling", "text_parser"}:
            raise ProviderConfigurationError(
                "Unsupported declared structured output capability."
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
        response_format: dict[str, object] | None = None
        if self.response_format == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "bookdistill_response",
                    "strict": True,
                    "schema": output_schema,
                },
            }
        elif self.response_format == "json_object":
            response_format = {"type": "json_object"}
        body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": generation_config.temperature,
            "max_tokens": generation_config.max_output_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format
        if self.response_format == "tool_calling":
            body["tools"] = [{"type": "function", "function": {
                "name": "bookdistill_response", "strict": True,
                "parameters": output_schema,
            }}]
            body["tool_choice"] = {
                "type": "function", "function": {"name": "bookdistill_response"},
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
            if error.code in {401, 403, 404}:
                raise ProviderConfigurationError(
                    f"LLM provider configuration was rejected (HTTP {error.code})."
                ) from None
            retryable = error.code == 429 or error.code >= 500
            raise ProviderRequestError(
                f"LLM provider returned HTTP {error.code}.",
                retryable=retryable,
                status_code=error.code,
            ) from None
        except (URLError, TimeoutError) as error:
            raise ProviderRequestError(
                "LLM provider could not be reached.", retryable=True
            ) from None
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProviderRequestError(
                "LLM provider returned an unreadable response.", retryable=False
            ) from None

        try:
            choice = payload["choices"][0]
            message = choice["message"]
            content = message.get("content")
            if self.response_format == "tool_calling" and not content:
                content = message["tool_calls"][0]["function"]["arguments"]
            if not isinstance(content, str) or not content.strip():
                raise ProviderRequestError(
                    "LLM provider returned empty message content.", retryable=True
                )
            usage = payload.get("usage") or {}
            return ProviderResult(
                content=content,
                finish_reason=choice.get("finish_reason", "unknown"),
                request_id=str(payload.get("id", "unknown")),
                usage=ModelUsage(
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                ),
                raw_response_reference=(
                    "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
                ),
            )
        except (KeyError, IndexError, TypeError) as error:
            raise ProviderRequestError(
                "LLM provider response did not match Chat Completions format.",
                retryable=False,
            ) from error


# Compatibility import for callers of the old provider module.
from .langchain_provider import LangChainChatProvider
