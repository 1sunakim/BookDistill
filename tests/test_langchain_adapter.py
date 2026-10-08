from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from bookdistill.contracts.generation import GenerationConfig
from bookdistill.generation.errors import ProviderConfigurationError, ProviderRequestError
from bookdistill.generation.providers import LangChainChatProvider


class _FakePrompt:
    @classmethod
    def from_messages(cls, messages):
        return cls()

    def format_messages(self, **kwargs):
        return kwargs


def _install_langchain_mocks(monkeypatch, invocation):
    class FakeChat:
        def __init__(self, **kwargs):
            self.config = kwargs

        def bind(self, **kwargs):
            self.bound = kwargs
            return self

        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, messages):
            return invocation()

    openai = ModuleType("langchain_openai")
    openai.ChatOpenAI = FakeChat
    core = ModuleType("langchain_core")
    prompts = ModuleType("langchain_core.prompts")
    prompts.ChatPromptTemplate = _FakePrompt
    monkeypatch.setitem(sys.modules, "langchain_openai", openai)
    monkeypatch.setitem(sys.modules, "langchain_core", core)
    monkeypatch.setitem(sys.modules, "langchain_core.prompts", prompts)


def _provider():
    return LangChainChatProvider(
        model_name="test-model", api_key="test-secret",
        base_url="https://api.deepseek.com", response_format="json_object",
    )


def test_langchain_adapter_maps_text_usage_and_request_metadata(monkeypatch):
    raw = SimpleNamespace(
        response_metadata={"finish_reason": "stop", "id": "vendor-request-1"},
        usage_metadata={"input_tokens": 12, "output_tokens": 7},
    )
    _install_langchain_mocks(
        monkeypatch, lambda: {"raw": raw, "parsed": {"answer": "ok"}},
    )
    result = _provider().complete(
        system_prompt="system", user_prompt="question", output_schema={},
        generation_config=GenerationConfig(temperature=0.2, max_output_tokens=50),
    )
    assert result.content == '{"answer": "ok"}'
    assert result.request_id == "vendor-request-1"
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 7
    assert result.finish_reason == "stop"


@pytest.mark.parametrize("status", [401, 403, 404])
def test_langchain_auth_and_endpoint_errors_are_configuration_errors(monkeypatch, status):
    class HttpFailure(Exception):
        status_code = status

    _install_langchain_mocks(monkeypatch, lambda: (_ for _ in ()).throw(HttpFailure()))
    with pytest.raises(ProviderConfigurationError) as caught:
        _provider().complete(
            system_prompt="system", user_prompt="question", output_schema={},
            generation_config=GenerationConfig(),
        )
    assert str(status) in str(caught.value)
    assert "test-secret" not in str(caught.value)


@pytest.mark.parametrize("status,retryable", [(429, True), (503, True), (400, False)])
def test_langchain_http_errors_are_classified_without_vendor_body(
    monkeypatch, status, retryable,
):
    class HttpFailure(Exception):
        status_code = status

    _install_langchain_mocks(monkeypatch, lambda: (_ for _ in ()).throw(HttpFailure("secret-body")))
    with pytest.raises(ProviderRequestError) as caught:
        _provider().complete(
            system_prompt="system", user_prompt="question", output_schema={},
            generation_config=GenerationConfig(),
        )
    assert caught.value.retryable is retryable
    assert caught.value.details["status_code"] == status
    assert "secret-body" not in str(caught.value)
