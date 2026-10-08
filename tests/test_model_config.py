from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from bookdistill.api.app import create_app
from bookdistill.model_config import ModelConfigStore
from bookdistill.model_config import ModelProfileInput
from bookdistill.contracts.generation import GenerationConfig
from bookdistill.generation.factory import _SavedModelProvider
from bookdistill.generation.ports import ProviderResult
from bookdistill.settings import Settings
from bookdistill.task_system.dispatch import NullDispatcher


class ModelConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings = Settings(
            database_url=f"sqlite:///{(root / 'test.db').as_posix()}",
            storage_root=root / "data", api_key="admin-secret", llm_provider="demo",
        )
        self.app = create_app(self.settings, NullDispatcher())
        self.client = TestClient(self.app)
        self.headers = {"X-BookDistill-API-Key": "admin-secret"}

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.engine.dispose()
        self.temporary.cleanup()

    def test_profile_is_persisted_redacted_and_selected(self) -> None:
        self.assertEqual(self.client.get("/api/v1/model-config").status_code, 401)
        response = self.client.post("/api/v1/model-config/profiles", headers=self.headers, json={
            "name": "My DeepSeek", "provider": "deepseek", "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com", "response_format": "json_object",
            "api_key": "private-model-key",
        })
        self.assertEqual(response.status_code, 201, response.text)
        profile_id = response.json()["profile_id"]
        self.assertNotIn("private-model-key", response.text)

        listing = self.client.get("/api/v1/model-config", headers=self.headers)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["active_profile_id"], profile_id)
        self.assertTrue(listing.json()["profiles"][1]["has_api_key"])
        self.assertNotIn("private-model-key", listing.text)
        effective = ModelConfigStore(self.settings.storage_root).effective_settings(self.settings)
        self.assertEqual(effective.llm_model, "deepseek-flash")
        self.assertEqual(effective.llm_api_key, "private-model-key")

        edited = self.client.post("/api/v1/model-config/profiles", headers=self.headers, json={
            "profile_id": profile_id, "name": "Updated", "provider": "deepseek",
            "model": "deepseek-v4-pro", "base_url": "https://api.deepseek.com",
            "response_format": "json_object", "api_key": "",
        })
        self.assertEqual(edited.status_code, 201, edited.text)
        self.assertEqual(
            ModelConfigStore(self.settings.storage_root).effective_settings(self.settings).llm_api_key,
            "private-model-key",
        )
        changed_destination = self.client.post(
            "/api/v1/model-config/profiles", headers=self.headers, json={
                "profile_id": profile_id, "name": "Other destination", "provider": "custom",
                "model": "model-two", "base_url": "https://other.example/v1",
                "response_format": "json_object", "api_key": "",
            },
        )
        self.assertEqual(changed_destination.status_code, 422)
        self.assertEqual(
            self.client.delete(f"/api/v1/model-config/profiles/{profile_id}", headers=self.headers).status_code,
            204,
        )
        self.assertEqual(
            ModelConfigStore(self.settings.storage_root).effective_settings(self.settings).llm_provider,
            "demo",
        )

    def test_invalid_url_and_missing_admin_key_are_rejected(self) -> None:
        response = self.client.post("/api/v1/model-config/profiles", headers=self.headers, json={
            "name": "Bad", "provider": "custom", "model": "test",
            "base_url": "file:///etc/passwd", "api_key": "private-key",
        })
        self.assertEqual(response.status_code, 422)
        self.assertFalse((self.settings.storage_root / "model-profiles.json").exists())

        no_key_settings = Settings(
            database_url=self.settings.database_url,
            storage_root=self.settings.storage_root, api_key="",
        )
        app = create_app(no_key_settings, NullDispatcher())
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/model-config").status_code, 503)
        app.state.engine.dispose()

    def test_running_provider_switches_profiles_without_restart(self) -> None:
        store = ModelConfigStore(self.settings.storage_root)
        first = store.save(ModelProfileInput(
            name="First", provider="custom", model="model-one",
            base_url="https://example.com/v1", api_key="first-key",
        ))
        provider = _SavedModelProvider(self.settings, store)
        with patch(
            "bookdistill.generation.providers.OpenAICompatibleProvider.complete",
            return_value=ProviderResult(content="{}"),
        ) as complete:
            provider.complete(
                system_prompt="test", user_prompt="test", output_schema={},
                generation_config=GenerationConfig(),
            )
            self.assertEqual(provider.model_name, "model-one")
            store.save(ModelProfileInput(
                profile_id=first["profile_id"], name="Second", provider="custom",
                model="model-two", base_url="https://example.com/v1", api_key="",
            ))
            provider.complete(
                system_prompt="test", user_prompt="test", output_schema={},
                generation_config=GenerationConfig(),
            )
            self.assertEqual(provider.model_name, "model-two")
            self.assertEqual(complete.call_count, 2)


if __name__ == "__main__":
    unittest.main()
