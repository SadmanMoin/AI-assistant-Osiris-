"""SQLite metadata for automations, runs, schedules, and drafts."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from osiris.util import new_id, utc_iso

_SCHEMA = """
CREATE TABLE IF NOT EXISTS automations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    current_version INTEGER NOT NULL,
    permissions_json TEXT NOT NULL,
    integrations_json TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_execution_at TEXT,
    last_status TEXT
);

CREATE TABLE IF NOT EXISTS automation_versions (
    automation_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    script_path TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    permissions_json TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    test_results_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    PRIMARY KEY (automation_id, version)
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    automation_id TEXT,
    name TEXT,
    version INTEGER,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    summary TEXT,
    error TEXT,
    log_path TEXT,
    action_requests_json TEXT
);

CREATE TABLE IF NOT EXISTS schedules (
    id TEXT PRIMARY KEY,
    automation_id TEXT NOT NULL,
    name TEXT NOT NULL,
    cadence TEXT NOT NULL,
    time_of_day TEXT,
    days_json TEXT,
    interval_minutes INTEGER,
    parameters_json TEXT,
    timezone TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    last_status TEXT,
    retry_limit INTEGER NOT NULL,
    retry_count INTEGER NOT NULL,
    lock_token TEXT,
    locked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    location TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integrations (
    provider TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_drafts (
    id TEXT PRIMARY KEY,
    execution_id TEXT,
    message_id TEXT,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS social_drafts (
    id TEXT PRIMARY KEY,
    execution_id TEXT,
    platform TEXT NOT NULL,
    title TEXT,
    body TEXT NOT NULL,
    hashtags_json TEXT,
    status TEXT NOT NULL,
    scheduled_for TEXT,
    published_at TEXT,
    provider_post_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    dedupe_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    target TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    outcome TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    read_flag INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS app_flags (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _loads(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            connection.executescript(_SCHEMA)
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    # Flags -----------------------------------------------------------------

    def get_flag(self, key: str, default: str = "") -> str:
        with self.session() as conn:
            row = conn.execute("SELECT value FROM app_flags WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_flag(self, key: str, value: str) -> None:
        with self.session() as conn:
            conn.execute(
                "INSERT INTO app_flags(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # Audit and notifications ----------------------------------------------

    def add_audit(self, actor: str, action: str, target: str, outcome: str, detail: str) -> None:
        with self.session() as conn:
            conn.execute(
                "INSERT INTO audit_events(id, created_at, actor, action, target, outcome, detail) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (new_id(), utc_iso(), actor, action, target, outcome, detail),
            )

    def list_audit(self, limit: int = 100) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_notification(self, level: str, title: str, body: str) -> None:
        with self.session() as conn:
            conn.execute(
                "INSERT INTO notifications(id, created_at, level, title, body, read_flag) "
                "VALUES(?, ?, ?, ?, ?, 0)",
                (new_id(), utc_iso(), level, title, body),
            )

    def list_notifications(self, limit: int = 20) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # Automations -----------------------------------------------------------

    def insert_automation(self, record: dict) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO automations(
                    id, name, description, status, enabled, current_version,
                    permissions_json, integrations_json, parameters_json,
                    created_at, updated_at, last_execution_at, last_status
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["name"],
                    record["description"],
                    record["status"],
                    1 if record["enabled"] else 0,
                    record["current_version"],
                    json.dumps(record["permissions"]),
                    json.dumps(record["integrations"]),
                    json.dumps(record["parameters"]),
                    record["created_at"],
                    record["updated_at"],
                    record.get("last_execution_at"),
                    record.get("last_status"),
                ),
            )

    def update_automation(self, automation_id: str, **fields: Any) -> None:
        mapping = {
            "name": "name",
            "description": "description",
            "status": "status",
            "enabled": "enabled",
            "current_version": "current_version",
            "permissions": "permissions_json",
            "integrations": "integrations_json",
            "parameters": "parameters_json",
            "updated_at": "updated_at",
            "last_execution_at": "last_execution_at",
            "last_status": "last_status",
        }
        assignments = []
        values: list[Any] = []
        for key, column in mapping.items():
            if key not in fields:
                continue
            value = fields[key]
            if key in {"permissions", "integrations", "parameters"}:
                value = json.dumps(value)
            if key == "enabled":
                value = 1 if value else 0
            assignments.append(f"{column} = ?")
            values.append(value)
        if not assignments:
            return
        values.append(automation_id)
        with self.session() as conn:
            conn.execute(
                f"UPDATE automations SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def _automation_from_row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["permissions"] = _loads(item.pop("permissions_json"), [])
        item["integrations"] = _loads(item.pop("integrations_json"), [])
        item["parameters"] = _loads(item.pop("parameters_json"), {})
        return item

    def get_automation(self, automation_id: str) -> dict | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM automations WHERE id = ?", (automation_id,)).fetchone()
        return self._automation_from_row(row)

    def get_automation_by_name(self, name: str) -> dict | None:
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM automations WHERE lower(name) = lower(?)",
                (name,),
            ).fetchone()
        return self._automation_from_row(row)

    def list_automations(self, query: str = "", include_archived: bool = True) -> list[dict]:
        clauses = []
        values: list[Any] = []
        if not include_archived:
            clauses.append("status != 'archived'")
        if query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            clauses.append("(name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
            values.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.session() as conn:
            rows = conn.execute(
                f"SELECT * FROM automations {where} ORDER BY updated_at DESC",
                values,
            ).fetchall()
        return [self._automation_from_row(row) for row in rows]

    def insert_version(self, record: dict) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO automation_versions(
                    automation_id, version, script_path, source_sha256,
                    permissions_json, parameters_json, validation_json,
                    test_results_json, status, created_at, approved_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["automation_id"],
                    record["version"],
                    record["script_path"],
                    record["source_sha256"],
                    json.dumps(record["permissions"]),
                    json.dumps(record["parameters"]),
                    json.dumps(record["validation"]),
                    json.dumps(record["test_results"]),
                    record["status"],
                    record["created_at"],
                    record.get("approved_at"),
                ),
            )

    def update_version(self, automation_id: str, version: int, **fields: Any) -> None:
        mapping = {
            "status": "status",
            "validation": "validation_json",
            "test_results": "test_results_json",
            "approved_at": "approved_at",
            "permissions": "permissions_json",
            "script_path": "script_path",
            "source_sha256": "source_sha256",
        }
        assignments = []
        values: list[Any] = []
        for key, column in mapping.items():
            if key not in fields:
                continue
            value = fields[key]
            if key in {"validation", "test_results", "permissions"}:
                value = json.dumps(value)
            assignments.append(f"{column} = ?")
            values.append(value)
        if not assignments:
            return
        values.extend([automation_id, version])
        with self.session() as conn:
            conn.execute(
                f"UPDATE automation_versions SET {', '.join(assignments)} "
                "WHERE automation_id = ? AND version = ?",
                values,
            )

    def _version_from_row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["permissions"] = _loads(item.pop("permissions_json"), [])
        item["parameters"] = _loads(item.pop("parameters_json"), {})
        item["validation"] = _loads(item.pop("validation_json"), {})
        item["test_results"] = _loads(item.pop("test_results_json"), {})
        return item

    def get_version(self, automation_id: str, version: int) -> dict | None:
        with self.session() as conn:
            row = conn.execute(
                "SELECT * FROM automation_versions WHERE automation_id = ? AND version = ?",
                (automation_id, version),
            ).fetchone()
        return self._version_from_row(row)

    def list_versions(self, automation_id: str) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM automation_versions WHERE automation_id = ? ORDER BY version DESC",
                (automation_id,),
            ).fetchall()
        return [self._version_from_row(row) for row in rows]

    def latest_version_number(self, automation_id: str) -> int:
        with self.session() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS version FROM automation_versions WHERE automation_id = ?",
                (automation_id,),
            ).fetchone()
        if row is None or row["version"] is None:
            return 0
        return int(row["version"])

    # Executions ------------------------------------------------------------

    def begin_execution(self, record: dict) -> str | None:
        """Insert a running execution. Returns None when one is already running."""
        automation_id = record.get("automation_id") or ""
        with self.session() as conn:
            if automation_id:
                existing = conn.execute(
                    "SELECT id FROM executions WHERE automation_id = ? AND status = 'running'",
                    (automation_id,),
                ).fetchone()
                if existing:
                    return None
            conn.execute(
                """
                INSERT INTO executions(
                    id, automation_id, name, version, trigger, status,
                    started_at, finished_at, summary, error, log_path, action_requests_json
                ) VALUES(?, ?, ?, ?, ?, 'running', ?, NULL, '', '', '', '[]')
                """,
                (
                    record["id"],
                    automation_id,
                    record.get("name", ""),
                    record.get("version"),
                    record["trigger"],
                    record["started_at"],
                ),
            )
        return record["id"]

    def finish_execution(
        self,
        execution_id: str,
        *,
        status: str,
        summary: str,
        error: str,
        log_path: str,
        action_requests: list,
    ) -> None:
        with self.session() as conn:
            conn.execute(
                """
                UPDATE executions
                SET status = ?, finished_at = ?, summary = ?, error = ?,
                    log_path = ?, action_requests_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    utc_iso(),
                    summary,
                    error,
                    log_path,
                    json.dumps(action_requests),
                    execution_id,
                ),
            )

    def has_running(self, automation_id: str) -> bool:
        with self.session() as conn:
            row = conn.execute(
                "SELECT id FROM executions WHERE automation_id = ? AND status = 'running'",
                (automation_id,),
            ).fetchone()
        return row is not None

    def list_executions(self, status: str | None = None, limit: int = 100) -> list[dict]:
        with self.session() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM executions WHERE status = ? ORDER BY started_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM executions ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["action_requests"] = _loads(item.pop("action_requests_json"), [])
            items.append(item)
        return items

    def count_executions(self, *, status: str | None = None, since: str | None = None) -> int:
        clauses = []
        values: list[Any] = []
        if status:
            clauses.append("status = ?")
            values.append(status)
        if since:
            clauses.append("started_at >= ?")
            values.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.session() as conn:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM executions {where}", values).fetchone()
        return int(row["count"])

    def reconcile_stale_runs(self, older_than_iso: str) -> int:
        with self.session() as conn:
            cursor = conn.execute(
                """
                UPDATE executions
                SET status = 'failed', finished_at = ?, error = ?,
                    summary = 'The run was interrupted when the application restarted.'
                WHERE status = 'running' AND started_at < ?
                """,
                (utc_iso(), "Interrupted by restart.", older_than_iso),
            )
            return cursor.rowcount

    # Schedules -------------------------------------------------------------

    def insert_schedule(self, record: dict) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO schedules(
                    id, automation_id, name, cadence, time_of_day, days_json,
                    interval_minutes, parameters_json, timezone, enabled, next_run_at, last_run_at,
                    last_status, retry_limit, retry_count, lock_token, locked_at, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["automation_id"],
                    record["name"],
                    record["cadence"],
                    record.get("time_of_day"),
                    json.dumps(record.get("days") or []),
                    record.get("interval_minutes"),
                    json.dumps(record.get("parameters") or {}),
                    record["timezone"],
                    1 if record["enabled"] else 0,
                    record.get("next_run_at"),
                    record.get("last_run_at"),
                    record.get("last_status"),
                    record["retry_limit"],
                    record.get("retry_count", 0),
                    None,
                    None,
                    record["created_at"],
                ),
            )

    def _schedule_from_row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["days"] = _loads(item.pop("days_json"), [])
        item["parameters"] = _loads(item.pop("parameters_json"), {})
        return item

    def list_schedules(self) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute("SELECT * FROM schedules ORDER BY next_run_at").fetchall()
        return [self._schedule_from_row(row) for row in rows]

    def get_schedule(self, schedule_id: str) -> dict | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        return self._schedule_from_row(row)

    def set_schedule_enabled(self, schedule_id: str, enabled: bool) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE schedules SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, schedule_id),
            )

    def disable_schedules_for(self, automation_id: str) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE schedules SET enabled = 0 WHERE automation_id = ?",
                (automation_id,),
            )

    def due_schedules(self, now_iso: str) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                """
                SELECT * FROM schedules
                WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
                ORDER BY next_run_at
                """,
                (now_iso,),
            ).fetchall()
        return [self._schedule_from_row(row) for row in rows]

    def claim_schedule(self, schedule_id: str, token: str, now_iso: str, stale_before_iso: str) -> bool:
        with self.session() as conn:
            cursor = conn.execute(
                """
                UPDATE schedules
                SET lock_token = ?, locked_at = ?
                WHERE id = ? AND enabled = 1
                  AND (lock_token IS NULL OR lock_token = '' OR locked_at IS NULL OR locked_at < ?)
                """,
                (token, now_iso, schedule_id, stale_before_iso),
            )
            return cursor.rowcount == 1

    def release_schedule(self, schedule_id: str, **fields: Any) -> None:
        assignments = ["lock_token = NULL", "locked_at = NULL"]
        values: list[Any] = []
        for key in ("next_run_at", "last_run_at", "last_status", "retry_count"):
            if key in fields:
                assignments.append(f"{key} = ?")
                values.append(fields[key])
        values.append(schedule_id)
        with self.session() as conn:
            conn.execute(
                f"UPDATE schedules SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    # Projects and integrations --------------------------------------------

    def insert_project(self, record: dict) -> None:
        with self.session() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, kind, location, enabled, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    record["id"],
                    record["name"],
                    record["kind"],
                    record["location"],
                    1 if record["enabled"] else 0,
                    record["created_at"],
                ),
            )

    def list_projects(self) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY name").fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            items.append(item)
        return items

    def delete_project(self, project_id: str) -> None:
        with self.session() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def set_project_enabled(self, project_id: str, enabled: bool) -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE projects SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, project_id),
            )

    def upsert_integration(self, provider: str, display_name: str, mode: str, status: str) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO integrations(provider, display_name, mode, status, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(provider) DO UPDATE SET
                    display_name = excluded.display_name,
                    mode = excluded.mode,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (provider, display_name, mode, status, utc_iso()),
            )

    def list_integrations(self) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute("SELECT * FROM integrations ORDER BY display_name").fetchall()
        return [dict(row) for row in rows]

    def get_integration(self, provider: str) -> dict | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM integrations WHERE provider = ?", (provider,)).fetchone()
        return dict(row) if row else None

    # Drafts and deliveries -------------------------------------------------

    def save_email_draft(self, record: dict) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO email_drafts(
                    id, execution_id, message_id, recipient, subject, body, status, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record.get("execution_id"),
                    record.get("message_id"),
                    record["recipient"],
                    record["subject"],
                    record["body"],
                    record["status"],
                    record["created_at"],
                ),
            )

    def list_email_drafts(self, limit: int = 50) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM email_drafts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_social_draft(self, record: dict) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO social_drafts(
                    id, execution_id, platform, title, body, hashtags_json, status,
                    scheduled_for, published_at, provider_post_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record.get("execution_id"),
                    record["platform"],
                    record.get("title", ""),
                    record["body"],
                    json.dumps(record.get("hashtags") or []),
                    record["status"],
                    record.get("scheduled_for"),
                    record.get("published_at"),
                    record.get("provider_post_id"),
                    record["created_at"],
                ),
            )

    def list_social_drafts(self, limit: int = 50) -> list[dict]:
        with self.session() as conn:
            rows = conn.execute(
                "SELECT * FROM social_drafts ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["hashtags"] = _loads(item.pop("hashtags_json"), [])
            items.append(item)
        return items

    def get_social_draft(self, draft_id: str) -> dict | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM social_drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["hashtags"] = _loads(item.pop("hashtags_json"), [])
        return item

    def update_social_draft(self, draft_id: str, **fields: Any) -> None:
        mapping = {
            "status": "status",
            "scheduled_for": "scheduled_for",
            "published_at": "published_at",
            "provider_post_id": "provider_post_id",
            "body": "body",
            "hashtags": "hashtags_json",
        }
        assignments = []
        values: list[Any] = []
        for key, column in mapping.items():
            if key not in fields:
                continue
            value = json.dumps(fields[key]) if key == "hashtags" else fields[key]
            assignments.append(f"{column} = ?")
            values.append(value)
        if not assignments:
            return
        values.append(draft_id)
        with self.session() as conn:
            conn.execute(
                f"UPDATE social_drafts SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def reserve_delivery(self, key: str, kind: str, target: str) -> bool:
        try:
            with self.session() as conn:
                conn.execute(
                    """
                    INSERT INTO deliveries(dedupe_key, kind, status, target, detail, created_at, updated_at)
                    VALUES(?, ?, 'reserved', ?, '', ?, ?)
                    """,
                    (key, kind, target, utc_iso(), utc_iso()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def finish_delivery(self, key: str, status: str, detail: str = "") -> None:
        with self.session() as conn:
            conn.execute(
                "UPDATE deliveries SET status = ?, detail = ?, updated_at = ? WHERE dedupe_key = ?",
                (status, detail, utc_iso(), key),
            )

    def get_delivery(self, key: str) -> dict | None:
        with self.session() as conn:
            row = conn.execute("SELECT * FROM deliveries WHERE dedupe_key = ?", (key,)).fetchone()
        return dict(row) if row else None

    # Dashboard -------------------------------------------------------------

    def dashboard(self, today_start_iso: str) -> dict:
        return {
            "automations": len(self.list_automations(include_archived=False)),
            "completed_today": self.count_executions(status="completed", since=today_start_iso),
            "running": self.count_executions(status="running"),
            "failed_today": self.count_executions(status="failed", since=today_start_iso),
            "upcoming": [item for item in self.list_schedules() if item["enabled"]][:5],
            "recent": self.list_executions(limit=8),
            "integrations": self.list_integrations(),
            "notifications": self.list_notifications(limit=5),
        }
