"""Persisted OpenAI-compatible model profiles shared by API and worker."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from bookdistill.settings import Settings


class ModelProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    response_format: str = "json_object"
    api_key: str = Field(default="", max_length=1000)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username or parsed.password
            or parsed.query or parsed.fragment
        ):
            raise ValueError("base_url must be an http(s) URL without credentials, query or fragment")
        return value.strip().rstrip("/")

    @field_validator("response_format")
    @classmethod
    def valid_response_format(cls, value: str) -> str:
        if value not in {"json_schema", "json_object", "tool_calling", "text_parser"}:
            raise ValueError(
                "response_format must declare json_schema, json_object, tool_calling, or text_parser"
            )
        return value


class ModelConfigStore:
    def __init__(self, storage_root: Path) -> None:
        self.path = storage_root / "model-profiles.json"
        self._lock = Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "active_profile_id": "environment", "profiles": []}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("profiles"), list):
            raise ValueError("Invalid saved model configuration")
        return data

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=".model-profiles-", delete=False,
            ) as file:
                temp_path = Path(file.name)
                os.chmod(temp_path, 0o600)
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def _public(profile: dict) -> dict:
        return {
            key: profile[key]
            for key in ("profile_id", "name", "provider", "model", "base_url", "response_format")
        } | {"has_api_key": bool(profile.get("api_key"))}

    def describe(self, settings: Settings) -> dict:
        data = self._read()
        environment = {
            "profile_id": "environment", "name": "服务器 .env 默认配置",
            "provider": settings.llm_provider, "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "response_format": settings.llm_response_format,
            "has_api_key": bool(settings.llm_api_key),
        }
        return {
            "active_profile_id": data.get("active_profile_id", "environment"),
            "profiles": [environment, *(self._public(item) for item in data["profiles"])],
        }

    def save(self, value: ModelProfileInput) -> dict:
        with self._lock:
            data = self._read()
            existing = next(
                (item for item in data["profiles"] if item["profile_id"] == value.profile_id),
                None,
            )
            if value.profile_id and existing is None:
                raise KeyError("Model profile not found")
            if existing and not value.api_key.strip() and (
                existing["provider"] != value.provider.strip()
                or existing["base_url"] != value.base_url
            ):
                raise ValueError("Enter the model API key again when changing provider or Base URL")
            api_key = value.api_key.strip() or (existing or {}).get("api_key", "")
            if not api_key:
                raise ValueError("An API key is required; local servers may use a placeholder key")
            profile = {
                "profile_id": value.profile_id or str(uuid4()),
                "name": value.name.strip(), "provider": value.provider.strip(),
                "model": value.model.strip(), "base_url": value.base_url,
                "response_format": value.response_format, "api_key": api_key,
            }
            if existing:
                data["profiles"] = [
                    profile if item["profile_id"] == value.profile_id else item
                    for item in data["profiles"]
                ]
            else:
                data["profiles"].append(profile)
            data["active_profile_id"] = profile["profile_id"]
            self._write(data)
            return self._public(profile)

    def activate(self, profile_id: str) -> dict:
        with self._lock:
            data = self._read()
            if profile_id != "environment" and not any(
                item["profile_id"] == profile_id for item in data["profiles"]
            ):
                raise KeyError("Model profile not found")
            data["active_profile_id"] = profile_id
            self._write(data)
            return {"active_profile_id": profile_id}

    def delete(self, profile_id: str) -> None:
        if profile_id == "environment":
            raise ValueError("The environment profile cannot be removed")
        with self._lock:
            data = self._read()
            old_count = len(data["profiles"])
            data["profiles"] = [
                item for item in data["profiles"] if item["profile_id"] != profile_id
            ]
            if len(data["profiles"]) == old_count:
                raise KeyError("Model profile not found")
            if data.get("active_profile_id") == profile_id:
                data["active_profile_id"] = "environment"
            self._write(data)

    def effective_settings(self, settings: Settings) -> Settings:
        data = self._read()
        active_id = data.get("active_profile_id", "environment")
        if active_id == "environment":
            return settings
        profile = next(
            (item for item in data["profiles"] if item["profile_id"] == active_id),
            None,
        )
        if profile is None:
            return settings
        return replace(
            settings, llm_provider="openai-compatible", llm_model=profile["model"],
            llm_base_url=profile["base_url"], llm_api_key=profile["api_key"],
            llm_response_format=profile["response_format"],
        )
