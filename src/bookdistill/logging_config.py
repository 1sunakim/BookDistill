"""Small JSON logging setup with fields used by the PRD."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": "bookdistill",
            "module": record.name,
            "event": record.getMessage(),
        }
        for key in ("request_id", "trace_id", "job_id", "subject_id", "duration_ms", "error_code", "stage"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
