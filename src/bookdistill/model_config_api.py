"""Authenticated management of persisted model profiles."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from bookdistill.model_config import ModelConfigStore, ModelProfileInput
from bookdistill.settings import Settings


class ActivationRequest(BaseModel):
    profile_id: str


def create_model_config_router(store: ModelConfigStore, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1/model-config", tags=["model-config"])

    @router.get("")
    def list_profiles() -> dict:
        return store.describe(settings)

    @router.post("/profiles", status_code=201)
    def save_profile(payload: ModelProfileInput) -> dict:
        try:
            return store.save(payload)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/active")
    def activate(payload: ActivationRequest) -> dict:
        try:
            return store.activate(payload.profile_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error

    @router.delete("/profiles/{profile_id}", status_code=204)
    def delete_profile(profile_id: str) -> Response:
        try:
            store.delete(profile_id)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return Response(status_code=204)

    return router
