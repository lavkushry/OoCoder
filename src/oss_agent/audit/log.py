"""Append-only audit log to JSONL and optional DB session."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from oss_agent.db.models import AuditLogEntry


class FileAndDbAuditLog:
    def __init__(self, path: Path, session: Session | None = None) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._session = session

    def append(
        self,
        *,
        actor: str,
        event: str,
        payload: Mapping[str, Any],
        task_id: str | None = None,
    ) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "actor": actor,
            "event": event,
            "task_id": task_id,
            "payload": dict(payload),
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        if self._session is not None:
            self._session.add(
                AuditLogEntry(
                    task_id=task_id,
                    actor=actor,
                    event=event,
                    payload=dict(payload),
                )
            )
            self._session.flush()
