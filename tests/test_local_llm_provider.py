from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bookdistill.generation import TransformersLocalProvider, build_generation_service
from bookdistill.generation.errors import ProviderConfigurationError
from bookdistill.settings import Settings


class TransformersLocalProviderTests(unittest.TestCase):
    def test_initialization_is_lazy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = TransformersLocalProvider(model_path=Path(directory))

        self.assertEqual(provider.provider_name, "transformers-local")
        self.assertIsNone(provider._model)
        self.assertIsNone(provider._tokenizer)

    def test_missing_model_is_rejected_before_framework_import(self) -> None:
        with self.assertRaises(ProviderConfigurationError):
            TransformersLocalProvider(model_path=Path("definitely-missing-model"))

    def test_json_candidate_unwraps_fence_and_leading_text(self) -> None:
        self.assertEqual(
            TransformersLocalProvider._json_candidate(
                '```json\n{"answer":"ok"}\n```'
            ),
            '{"answer":"ok"}',
        )

    def test_factory_selects_local_provider_without_loading_weights(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = Settings(
                database_url=f"sqlite:///{(root / 'test.db').as_posix()}",
                storage_root=root,
                llm_provider="transformers-local",
                llm_local_model_path=root,
            )
            service = build_generation_service(settings)

        self.assertIsNotNone(service)
        provider = service._gateway._provider
        self.assertIsInstance(provider, TransformersLocalProvider)
        self.assertIsNone(provider._model)
        self.assertEqual(
            TransformersLocalProvider._json_candidate(
                'Here is the result: {"answer":"ok"} done.'
            ),
            '{"answer":"ok"}',
        )


if __name__ == "__main__":
    unittest.main()
