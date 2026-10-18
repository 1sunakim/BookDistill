"""Durable run/checkpoint store; one Agent service process per SQLite file."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from uuid import uuid4


TERMINAL = {"succeeded", "refused", "failed"}


class IdempotencyConflict(Exception):
    pass


class RunStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                request_hash TEXT NOT NULL, conversation_id TEXT NOT NULL,
                thread_id TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
                request_json TEXT NOT NULL, checkpoint_json TEXT NOT NULL,
                result_json TEXT, error_code TEXT, updated_at TEXT NOT NULL
            )""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create(self, request: dict[str, Any], idempotency_key: str) -> tuple[dict[str, Any], bool]:
        raw = json.dumps(request, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM runs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                if existing["request_hash"] != digest:
                    raise IdempotencyConflict("idempotency key belongs to another request")
                return self._decode(existing), False
            run_id = str(uuid4())
            conversation_id = request.get("conversation_id") or run_id
            now = datetime.now(timezone.utc).isoformat()
            db.execute("""INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (run_id, idempotency_key, digest, conversation_id, conversation_id,
                        "queued", "searching", raw, "{}", None, None, now))
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            return self._decode(row), True

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode(row) if row else None

    def incomplete_ids(self) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT run_id FROM runs WHERE status NOT IN ('succeeded','refused','failed')").fetchall()
        return [row["run_id"] for row in rows]

    def save(self, run_id: str, *, status: str | None = None, phase: str | None = None,
             checkpoint: dict[str, Any] | None = None, result: dict[str, Any] | None = None,
             error_code: str | None = None) -> dict[str, Any]:
        updates = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if status is not None:
            updates["status"] = status
        if phase is not None:
            updates["phase"] = phase
        if checkpoint is not None:
            updates["checkpoint_json"] = json.dumps(checkpoint, ensure_ascii=False)
        if result is not None:
            updates["result_json"] = json.dumps(result, ensure_ascii=False)
        if error_code is not None:
            updates["error_code"] = error_code
        assignments = ", ".join(f"{key}=?" for key in updates)
        with self._lock, self._connect() as db:
            db.execute(f"UPDATE runs SET {assignments} WHERE run_id=?", (*updates.values(), run_id))
        record = self.get(run_id)
        if record is None:
            raise KeyError(run_id)
        return record

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["request"] = json.loads(data.pop("request_json"))
        data["checkpoint"] = json.loads(data.pop("checkpoint_json"))
        raw_result = data.pop("result_json")
        data["result"] = json.loads(raw_result) if raw_result else None
        return data
