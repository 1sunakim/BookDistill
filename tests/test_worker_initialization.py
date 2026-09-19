"""Regression tests for lightweight Celery application discovery."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import patch


def test_importing_worker_does_not_build_runtime() -> None:
    """Celery Beat must not initialize or download retrieval models at import time."""
    sys.modules.pop("bookdistill.worker", None)
    with patch("bookdistill.bootstrap.build_runtime") as build_runtime:
        module = importlib.import_module("bookdistill.worker")

    build_runtime.assert_not_called()
    assert module.celery_app.main == "bookdistill"
