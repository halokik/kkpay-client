"""Persistent webhook idempotency helpers.

The store only deduplicates gateway callbacks. Applications must still update
their own order and fulfillment records atomically whenever possible.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType
from typing import Any

from .errors import KKPayIdempotencyError
from .models import CallbackData


class IdempotencyClaim:
    """A single attempt to own and process a webhook event."""

    def __init__(
        self,
        store: "SQLiteIdempotencyStore",
        key: str,
        payload_hash: str,
        claim_id: str,
        *,
        acquired: bool,
        completed: bool,
        attempts: int,
    ) -> None:
        self._store = store
        self.key = key
        self._payload_hash = payload_hash
        self._claim_id = claim_id
        self.acquired = acquired
        self.completed = completed
        self.attempts = attempts
        self._finished = False

    def complete(self) -> None:
        """Mark an acquired event as successfully fulfilled."""
        if not self.acquired or self._finished:
            return
        self._store._finish(self.key, self._payload_hash, self._claim_id, "completed", "")
        self.completed = True
        self._finished = True

    def fail(self, error: object = "") -> None:
        """Release a failed event so a later gateway retry can claim it."""
        if not self.acquired or self._finished:
            return
        self._store._finish(self.key, self._payload_hash, self._claim_id, "failed", str(error))
        self._finished = True

    def __enter__(self) -> "IdempotencyClaim":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc is None:
            self.complete()
        else:
            self.fail(exc)
        return False


class SQLiteIdempotencyStore:
    """Small persistent callback deduplicator backed by SQLite.

    A callback can be reclaimed after a failure or after a stale processing
    lock. An already completed callback is never acquired again.
    """

    def __init__(self, path: str | Path, *, stale_after_seconds: float = 300.0) -> None:
        raw_path = str(path)
        if not raw_path:
            raise ValueError("idempotency database path must not be empty")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if raw_path != ":memory:":
            Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            raw_path = str(Path(raw_path).expanduser())
        self.path = raw_path
        self.stale_after_seconds = float(stale_after_seconds)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def __repr__(self) -> str:
        return f"SQLiteIdempotencyStore(path={self.path!r})"

    def _initialize(self) -> None:
        with self._lock:
            if self.path != ":memory:":
                self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA busy_timeout=10000")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS kkpay_webhook_events (
                    event_key TEXT PRIMARY KEY,
                    payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    claim_id TEXT NOT NULL DEFAULT '',
                    locked_at REAL NOT NULL,
                    completed_at REAL,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(kkpay_webhook_events)")
            }
            if "claim_id" not in columns:
                self._connection.execute(
                    "ALTER TABLE kkpay_webhook_events "
                    "ADD COLUMN claim_id TEXT NOT NULL DEFAULT ''"
                )

    @staticmethod
    def _hash_payload(payload: Mapping[str, Any]) -> str:
        unsigned = {key: value for key, value in payload.items() if key != "signature"}
        serialized = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def claim(
        self,
        callback_or_key: CallbackData | str,
        payload: Mapping[str, Any] | None = None,
    ) -> IdempotencyClaim:
        """Atomically try to acquire a callback for processing.

        ``acquired=False, completed=True`` means the event was already handled
        and the webhook endpoint can safely return ``ok``. ``completed=False``
        means another worker still owns a fresh processing lock.
        """
        if isinstance(callback_or_key, CallbackData):
            key = callback_or_key.idempotency_key
            event_payload: Mapping[str, Any] = callback_or_key.raw
        else:
            key = str(callback_or_key or "").strip()
            event_payload = payload or {"event_key": key}
        if not key:
            raise KKPayIdempotencyError("idempotency key must not be empty")

        payload_hash = self._hash_payload(event_payload)
        claim_id = uuid.uuid4().hex
        now = time.time()
        stale_before = now - self.stale_after_seconds

        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "SELECT payload_hash, state, attempts, locked_at "
                    "FROM kkpay_webhook_events WHERE event_key = ?",
                    (key,),
                ).fetchone()

                if row is None:
                    attempts = 1
                    self._connection.execute(
                        "INSERT INTO kkpay_webhook_events "
                        "(event_key, payload_hash, state, attempts, claim_id, locked_at) "
                        "VALUES (?, ?, 'processing', ?, ?, ?)",
                        (key, payload_hash, attempts, claim_id, now),
                    )
                    acquired = True
                    completed = False
                else:
                    if row["payload_hash"] != payload_hash:
                        raise KKPayIdempotencyError(
                            "callback payload conflicts with an existing idempotency key"
                        )
                    attempts = int(row["attempts"] or 0)
                    state = str(row["state"])
                    if state == "completed":
                        acquired = False
                        completed = True
                    elif state == "processing" and float(row["locked_at"] or 0) > stale_before:
                        acquired = False
                        completed = False
                    else:
                        attempts += 1
                        self._connection.execute(
                            "UPDATE kkpay_webhook_events SET state = 'processing', attempts = ?, "
                            "claim_id = ?, locked_at = ?, completed_at = NULL, last_error = '' "
                            "WHERE event_key = ?",
                            (attempts, claim_id, now, key),
                        )
                        acquired = True
                        completed = False
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

        return IdempotencyClaim(
            self,
            key,
            payload_hash,
            claim_id,
            acquired=acquired,
            completed=completed,
            attempts=attempts,
        )

    def _finish(
        self,
        key: str,
        payload_hash: str,
        claim_id: str,
        state: str,
        error: str,
    ) -> None:
        if state not in {"completed", "failed"}:
            raise KKPayIdempotencyError("invalid idempotency state")
        now = time.time()
        completed_at = now if state == "completed" else None
        with self._lock:
            cursor = self._connection.execute(
                "UPDATE kkpay_webhook_events SET state = ?, completed_at = ?, "
                "last_error = ? WHERE event_key = ? AND payload_hash = ? "
                "AND claim_id = ? AND state = 'processing'",
                (state, completed_at, error[:500], key, payload_hash, claim_id),
            )
            if cursor.rowcount != 1:
                raise KKPayIdempotencyError("idempotency claim is no longer active")

    def get_state(self, key: str) -> str | None:
        """Return ``processing``, ``completed``, ``failed``, or ``None``."""
        with self._lock:
            row = self._connection.execute(
                "SELECT state FROM kkpay_webhook_events WHERE event_key = ?",
                (str(key),),
            ).fetchone()
        return str(row["state"]) if row else None

    def purge_completed(self, *, older_than_seconds: float = 30 * 86400) -> int:
        """Delete old completed deduplication records and return the row count."""
        cutoff = time.time() - float(older_than_seconds)
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM kkpay_webhook_events "
                "WHERE state = 'completed' AND completed_at IS NOT NULL AND completed_at < ?",
                (cutoff,),
            )
        return int(cursor.rowcount)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteIdempotencyStore":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.close()
        return False
