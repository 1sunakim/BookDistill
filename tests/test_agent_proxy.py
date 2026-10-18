"""Agent proxy scope, authentication and failure isolation."""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bookdistill.agent_proxy import create_agent_proxy_router
from bookdistill.api.app import create_app
from bookdistill.contracts.document_processing import BookStatus
from bookdistill.settings import Settings
from bookdistill.task_system.dispatch import NullDispatcher


BOOKS = ["00000000-0000-4000-8000-000000000011", "00000000-0000-4000-8000-000000000012"]
RUN = "00000000-0000-4000-8000-000000000099"


def settings(tmp_path: Path, **override) -> Settings:
    return Settings(database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
                    storage_root=tmp_path / "data", api_key="book-key",
                    agent_enabled=True, agent_api_key="agent-key",
                    agent_url="http://agent.test", **override)


def test_proxy_sends_server_key_and_limits_book_scope(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"run_id": RUN, "status": "queued"})

    ready = {UUID(book_id) for book_id in BOOKS}
    app = FastAPI()
    app.include_router(create_agent_proxy_router(
        settings(tmp_path),
        lambda book_id: SimpleNamespace(status=BookStatus.READY) if book_id in ready else None,
        httpx.MockTransport(handler),
    ))
    with TestClient(app) as client:
        headers = {"Idempotency-Key": "user-run"}
        invalid = client.post("/api/v1/agent/runs", headers=headers,
                              json={"question": "compare", "book_ids": [BOOKS[0], str(UUID(int=99))]})
        assert invalid.status_code == 422
        assert not calls
        duplicate = client.post("/api/v1/agent/runs", headers=headers,
                                json={"question": "compare", "book_ids": [BOOKS[0], BOOKS[0]]})
        assert duplicate.status_code == 422
        good = client.post("/api/v1/agent/runs", headers=headers,
                           json={"question": "compare", "book_ids": BOOKS})
        assert good.status_code == 202
        assert client.get(f"/api/v1/agent/runs/{RUN}").status_code == 200
        assert client.post(f"/api/v1/agent/runs/{RUN}/cancel").status_code == 200
        assert client.post(f"/api/v1/agent/runs/{RUN}/retry").status_code == 200
    assert len(calls) == 4
    assert calls[0].headers["X-Agent-API-Key"] == "agent-key"
    assert calls[0].headers["Idempotency-Key"] == "user-run"
    assert calls[0].url.path == "/v1/runs"
    assert calls[2].url.path == f"/v1/runs/{RUN}/cancel"
    assert calls[3].url.path == f"/v1/runs/{RUN}/retry"
    assert "book-key" not in str(calls[0].headers)


def test_proxy_disabled_and_unavailable_do_not_affect_other_routes(tmp_path):
    disabled = Settings(database_url=f"sqlite:///{(tmp_path / 'disabled.db').as_posix()}",
                        storage_root=tmp_path / "disabled", api_key="book-key")
    app = FastAPI()
    app.include_router(create_agent_proxy_router(disabled, lambda _: None))
    with TestClient(app) as client:
        assert client.get(f"/api/v1/agent/runs/{RUN}").status_code == 503
    app = FastAPI()
    app.include_router(create_agent_proxy_router(
        settings(tmp_path), lambda _: SimpleNamespace(status=BookStatus.READY),
        httpx.MockTransport(lambda _: httpx.Response(500, text="private upstream error")),
    ))
    with TestClient(app) as client:
        failed = client.post("/api/v1/agent/runs", headers={"Idempotency-Key": "x"},
                             json={"question": "compare", "book_ids": BOOKS})
        assert failed.status_code == 503
        assert "private upstream error" not in failed.text


def test_full_app_protects_agent_status_get(tmp_path):
    configured = Settings(database_url=f"sqlite:///{(tmp_path / 'full.db').as_posix()}",
                          storage_root=tmp_path / "full", api_key="book-key",
                          agent_enabled=True, agent_api_key="agent-key",
                          agent_url="http://127.0.0.1:9", agent_timeout_seconds=0.2)
    app = create_app(configured, NullDispatcher())
    try:
        with TestClient(app) as client:
            assert client.get(f"/api/v1/agent/runs/{RUN}").status_code == 401
            response = client.get(f"/api/v1/agent/runs/{RUN}",
                                  headers={"X-BookDistill-API-Key": "book-key"})
            assert response.status_code == 503
            assert response.json()["code"] == "AGENT_UNAVAILABLE"
    finally:
        app.state.engine.dispose()


def test_enabled_requires_both_server_keys(tmp_path):
    with pytest.raises(ValueError, match="requires BookDistill and Agent API keys"):
        Settings(database_url="sqlite:///test.db", storage_root=tmp_path,
                 agent_enabled=True, api_key="", agent_api_key="agent-key")
