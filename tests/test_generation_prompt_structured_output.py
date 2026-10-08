from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import unittest
from uuid import UUID

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bookdistill.contracts.generation import LLMRequest, TaskType, TraceContext  # noqa: E402
from bookdistill.generation.prompts import default_prompt_registry  # noqa: E402
from bookdistill.model_config import ModelProfileInput  # noqa: E402
from bookdistill.contracts.generation import GenerationConfig  # noqa: E402
from bookdistill.generation.langchain_provider import LangChainChatProvider  # noqa: E402
from bookdistill.generation.factory import _build_provider  # noqa: E402
from bookdistill.generation.errors import ProviderConfigurationError  # noqa: E402
from bookdistill.settings import Settings  # noqa: E402


class PromptAndStructuredOutputCapabilityTests(unittest.TestCase):
    def test_factory_passes_structured_output_switch_to_active_adapter(self) -> None:
        settings = Settings(
            database_url="sqlite://", storage_root=PROJECT_ROOT,
            llm_provider="openai-compatible", llm_model="model", llm_api_key="placeholder",
            llm_adapter="langchain", structured_output_backend="langchain",
        )
        with patch("bookdistill.generation.factory.LangChainChatProvider") as adapter:
            self.assertIs(_build_provider(settings), adapter.return_value)
        self.assertEqual(adapter.call_args.kwargs["structured_output_backend"], "langchain")
        with self.assertRaises(ProviderConfigurationError):
            _build_provider(replace(settings, llm_adapter="legacy"))

    def test_each_generation_task_renders_versioned_system_and_user_messages(self) -> None:
        registry = default_prompt_registry()
        for index, task_type in enumerate(TaskType):
            with self.subTest(task_type=task_type):
                version = {
                    TaskType.RAG_ANSWER: "rag-answer.v1",
                    TaskType.SUMMARY: "summary.v1",
                    TaskType.KNOWLEDGE_EXTRACTION: "knowledge-extraction.v1",
                    TaskType.QUESTION_GENERATION: "question-generation.v1",
                    TaskType.VIEWPOINT_COMPARISON: "viewpoint-comparison.v1",
                }[task_type]
                request = LLMRequest(
                    task_type=task_type, prompt_version=version, input={"evidence": []},
                    output_schema={"type": "object"},
                    trace_context=TraceContext(
                        request_id=UUID(int=index + 1), trace_id=UUID(int=index + 10),
                    ),
                )
                messages = registry.get(task_type, version).render_messages(request)
                self.assertEqual([item["role"] for item in messages], ["system", "user"])
                self.assertIn("untrusted", messages[0]["content"])
                self.assertIn('"required_output_schema"', messages[1]["content"])

    def test_model_profile_accepts_each_explicit_output_capability(self) -> None:
        for capability in ("json_schema", "json_object", "tool_calling", "text_parser"):
            with self.subTest(capability=capability):
                profile = ModelProfileInput(
                    name="custom", provider="custom", model="model",
                    base_url="https://example.com/v1", response_format=capability,
                    api_key="placeholder",
                )
                self.assertEqual(profile.response_format, capability)

    def test_langchain_adapter_selects_declared_mode_and_preserves_metadata(self) -> None:
        selected_methods: list[str] = []

        class FakeModel:
            def bind(self, **kwargs):
                return self

            def with_structured_output(self, schema, *, method, include_raw):
                selected_methods.append(method)
                return self

            def invoke(self, messages):
                raw = SimpleNamespace(
                    content='{"ok":true}',
                    response_metadata={"finish_reason": "stop", "request_id": "req-1"},
                    usage_metadata={"input_tokens": 12, "output_tokens": 4},
                )
                return {"raw": raw, "parsed": {"ok": True}}

        class FakeChatPromptTemplate:
            @classmethod
            def from_messages(cls, messages):
                return cls()

            def format_messages(self, **kwargs):
                return kwargs

        lc_openai = ModuleType("langchain_openai")
        lc_openai.ChatOpenAI = lambda **kwargs: FakeModel()
        lc_core = ModuleType("langchain_core")
        lc_prompts = ModuleType("langchain_core.prompts")
        lc_prompts.ChatPromptTemplate = FakeChatPromptTemplate
        with patch.dict(sys.modules, {
            "langchain_openai": lc_openai,
            "langchain_core": lc_core,
            "langchain_core.prompts": lc_prompts,
        }):
            for capability, method in (
                ("json_schema", "json_schema"),
                ("json_object", "json_mode"),
                ("tool_calling", "function_calling"),
                ("text_parser", None),
            ):
                provider = LangChainChatProvider(
                    model_name="fake-model", api_key="placeholder",
                    base_url="https://example.com/v1", response_format=capability,
                )
                result = provider.complete(
                    system_prompt="system", user_prompt="user",
                    output_schema={"type": "object"},
                    generation_config=GenerationConfig(),
                )
                self.assertEqual(result.request_id, "req-1")
                self.assertEqual(result.usage.input_tokens, 12)
                self.assertEqual(result.usage.output_tokens, 4)
                self.assertEqual(result.finish_reason, "stop")
                if method:
                    self.assertEqual(selected_methods[-1], method)
                else:
                    self.assertEqual(selected_methods, ["json_schema", "json_mode", "function_calling"])

    def test_legacy_output_backend_uses_raw_model_response(self) -> None:
        bound_options = []
        model_options = []

        class FakeModel:
            def __init__(self, **kwargs):
                self.options = kwargs
                model_options.append(kwargs)

            def bind(self, **kwargs):
                bound_options.append(kwargs)
                return self

            def with_structured_output(self, *args, **kwargs):
                raise AssertionError("legacy mode must not use LangChain structured output")

            def invoke(self, messages):
                return SimpleNamespace(
                    content='{"ok":true}', response_metadata={"finish_reason": "stop"},
                    usage_metadata={"input_tokens": 2, "output_tokens": 3},
                )

        class FakePrompt:
            @classmethod
            def from_messages(cls, messages):
                return cls()

            def format_messages(self, **kwargs):
                return kwargs

        lc_openai = ModuleType("langchain_openai")
        lc_openai.ChatOpenAI = FakeModel
        lc_core = ModuleType("langchain_core")
        lc_prompts = ModuleType("langchain_core.prompts")
        lc_prompts.ChatPromptTemplate = FakePrompt
        with patch.dict(sys.modules, {
            "langchain_openai": lc_openai, "langchain_core": lc_core,
            "langchain_core.prompts": lc_prompts,
        }):
            provider = LangChainChatProvider(
                model_name="model", api_key="placeholder",
                base_url="https://example.com/v1", response_format="json_object",
                structured_output_backend="legacy",
            )
            result = provider.complete(
                system_prompt="system", user_prompt="user", output_schema={"type": "object"},
                generation_config=GenerationConfig(temperature=0.3, max_output_tokens=70),
            )
        self.assertEqual(result.content, '{"ok":true}')
        self.assertEqual(bound_options, [{"response_format": {"type": "json_object"}}])
        self.assertEqual(model_options[0]["temperature"], 0.3)
        self.assertEqual(model_options[0]["max_tokens"], 70)
        self.assertEqual(model_options[0]["max_retries"], 0)


if __name__ == "__main__":
    unittest.main()
