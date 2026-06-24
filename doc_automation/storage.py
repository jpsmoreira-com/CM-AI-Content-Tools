from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterable, Iterator, List, Optional

from .config import DB_PATH


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_storage() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS work_item_state (
                portal TEXT NOT NULL,
                work_item_id INTEGER NOT NULL,
                iteration_path TEXT,
                triage_status TEXT NOT NULL DEFAULT 'pending',
                selected_base_branch TEXT,
                work_type TEXT,
                branch_name TEXT,
                reviewer_display_name TEXT,
                reviewer_unique_name TEXT,
                reviewer_id TEXT,
                branch_status TEXT,
                branch_error TEXT,
                branch_created_at TEXT,
                pr_status TEXT,
                pr_id INTEGER,
                pr_url TEXT,
                pr_error TEXT,
                copilot_status TEXT,
                copilot_error TEXT,
                copilot_context_path TEXT,
                copilot_workspace_path TEXT,
                copilot_agent_name TEXT,
                copilot_provider_log_path TEXT,
                copilot_process_id TEXT,
                copilot_prepared_at TEXT,
                agent_result_status TEXT,
                agent_result_path TEXT,
                agent_result_summary TEXT,
                agent_result_error TEXT,
                agent_result_checked_at TEXT,
                push_status TEXT,
                push_commit TEXT,
                push_error TEXT,
                pushed_at TEXT,
                final_report_path TEXT,
                final_report_created_at TEXT,
                agent_repair_count INTEGER NOT NULL DEFAULT 0,
                agent_repair_last_started_at TEXT,
                agent_repair_last_reason TEXT,
                rerun_active INTEGER NOT NULL DEFAULT 0,
                rerun_started_at TEXT,
                auto_flow_enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (portal, work_item_id)
            )
            """
        )
        _ensure_column(connection, "work_item_state", "copilot_status", "TEXT")
        _ensure_column(connection, "work_item_state", "copilot_error", "TEXT")
        _ensure_column(connection, "work_item_state", "copilot_context_path", "TEXT")
        _ensure_column(connection, "work_item_state", "copilot_workspace_path", "TEXT")
        _ensure_column(connection, "work_item_state", "copilot_agent_name", "TEXT")
        _ensure_column(connection, "work_item_state", "copilot_provider_log_path", "TEXT")
        _ensure_column(connection, "work_item_state", "copilot_process_id", "TEXT")
        _ensure_column(connection, "work_item_state", "copilot_prepared_at", "TEXT")
        _ensure_column(connection, "work_item_state", "agent_result_status", "TEXT")
        _ensure_column(connection, "work_item_state", "agent_result_path", "TEXT")
        _ensure_column(connection, "work_item_state", "agent_result_summary", "TEXT")
        _ensure_column(connection, "work_item_state", "agent_result_error", "TEXT")
        _ensure_column(connection, "work_item_state", "agent_result_checked_at", "TEXT")
        _ensure_column(connection, "work_item_state", "push_status", "TEXT")
        _ensure_column(connection, "work_item_state", "push_commit", "TEXT")
        _ensure_column(connection, "work_item_state", "push_error", "TEXT")
        _ensure_column(connection, "work_item_state", "pushed_at", "TEXT")
        _ensure_column(connection, "work_item_state", "final_report_path", "TEXT")
        _ensure_column(connection, "work_item_state", "final_report_created_at", "TEXT")
        _ensure_column(connection, "work_item_state", "agent_repair_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "work_item_state", "agent_repair_last_started_at", "TEXT")
        _ensure_column(connection, "work_item_state", "agent_repair_last_reason", "TEXT")
        _ensure_column(connection, "work_item_state", "rerun_active", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "work_item_state", "rerun_started_at", "TEXT")
        _ensure_column(connection, "work_item_state", "auto_flow_enabled", "INTEGER NOT NULL DEFAULT 0")


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    existing_columns = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name in existing_columns:
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_work_item_states(portal: str, work_item_ids: Iterable[int]) -> Dict[int, Dict[str, object]]:
    ids = [int(work_item_id) for work_item_id in work_item_ids]
    if not ids:
        return {}

    placeholders = ",".join("?" for _ in ids)
    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM work_item_state
            WHERE portal = ?
              AND work_item_id IN ({placeholders})
            """,
            [portal, *ids],
        ).fetchall()

    return {
        int(row["work_item_id"]): {key: row[key] for key in row.keys()}
        for row in rows
    }


def list_auto_flow_states() -> List[Dict[str, object]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM work_item_state
            WHERE COALESCE(pr_status, '') NOT IN ('created', 'exists')
              AND (
                auto_flow_enabled = 1
                OR COALESCE(push_status, '') = 'pushed'
                OR (
                  COALESCE(branch_name, '') != ''
                  AND COALESCE(agent_result_path, '') != ''
                  AND (
                    COALESCE(agent_result_status, '') IN ('green_light', 'ready_for_push', 'success', 'waiting')
                    OR (
                      COALESCE(agent_result_status, '') = 'error'
                      AND COALESCE(agent_result_error, '') LIKE 'Agent result file changed recently;%'
                    )
                  )
                )
              )
            ORDER BY updated_at ASC
            """
        ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def save_work_item_plan(
    *,
    portal: str,
    work_item_id: int,
    iteration_path: str,
    triage_status: str,
    selected_base_branch: str,
    work_type: str,
    branch_name: str,
    reviewer_display_name: str,
    reviewer_unique_name: str,
    reviewer_id: str,
) -> None:
    timestamp = utc_now()
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO work_item_state (
                portal,
                work_item_id,
                iteration_path,
                triage_status,
                selected_base_branch,
                work_type,
                branch_name,
                reviewer_display_name,
                reviewer_unique_name,
                reviewer_id,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(portal, work_item_id) DO UPDATE SET
                iteration_path = excluded.iteration_path,
                triage_status = excluded.triage_status,
                selected_base_branch = excluded.selected_base_branch,
                work_type = excluded.work_type,
                branch_name = excluded.branch_name,
                reviewer_display_name = excluded.reviewer_display_name,
                reviewer_unique_name = excluded.reviewer_unique_name,
                reviewer_id = excluded.reviewer_id,
                updated_at = excluded.updated_at
            """,
            (
                portal,
                work_item_id,
                iteration_path,
                triage_status,
                selected_base_branch,
                work_type,
                branch_name,
                reviewer_display_name,
                reviewer_unique_name,
                reviewer_id,
                timestamp,
            ),
        )


def mark_branch_result(
    *,
    portal: str,
    work_item_id: int,
    branch_name: str,
    branch_status: str,
    branch_error: str = "",
    created_at: Optional[str] = None,
) -> None:
    timestamp = utc_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE work_item_state
            SET branch_name = ?,
                branch_status = ?,
                branch_error = ?,
                branch_created_at = ?,
                updated_at = ?
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (
                branch_name,
                branch_status,
                branch_error,
                created_at or timestamp,
                timestamp,
                portal,
                work_item_id,
            ),
        )


def mark_pr_result(
    *,
    portal: str,
    work_item_id: int,
    pr_status: str,
    pr_id: Optional[int] = None,
    pr_url: str = "",
    pr_error: str = "",
) -> None:
    timestamp = utc_now()
    complete_rerun = pr_status in {"created", "exists", "linked", "parent-linked"}
    with connect() as connection:
        connection.execute(
            """
            UPDATE work_item_state
            SET pr_status = ?,
                pr_id = ?,
                pr_url = ?,
                pr_error = ?,
                rerun_active = CASE WHEN ? THEN 0 ELSE rerun_active END,
                updated_at = ?
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (
                pr_status,
                pr_id,
                pr_url,
                pr_error,
                1 if complete_rerun else 0,
                timestamp,
                portal,
                work_item_id,
            ),
        )


def start_rerun_state(
    *,
    portal: str,
    work_item_id: int,
    selected_base_branch: str,
    work_type: str,
    branch_name: str,
    triage_status: str = "rerun",
) -> None:
    timestamp = utc_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE work_item_state
            SET triage_status = ?,
                selected_base_branch = ?,
                work_type = ?,
                branch_name = ?,
                branch_status = '',
                branch_error = '',
                branch_created_at = '',
                pr_status = '',
                pr_id = NULL,
                pr_url = '',
                pr_error = '',
                copilot_status = '',
                copilot_error = '',
                copilot_context_path = '',
                copilot_agent_name = '',
                copilot_provider_log_path = '',
                copilot_process_id = '',
                copilot_prepared_at = '',
                agent_result_status = '',
                agent_result_path = '',
                agent_result_summary = '',
                agent_result_error = '',
                agent_result_checked_at = '',
                push_status = '',
                push_commit = '',
                push_error = '',
                pushed_at = '',
                final_report_path = '',
                final_report_created_at = '',
                rerun_active = 1,
                rerun_started_at = ?,
                auto_flow_enabled = 0,
                updated_at = ?
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (
                triage_status,
                selected_base_branch,
                work_type,
                branch_name,
                timestamp,
                timestamp,
                portal,
                work_item_id,
            ),
        )


def mark_copilot_result(
    *,
    portal: str,
    work_item_id: int,
    copilot_status: str,
    copilot_context_path: str = "",
    copilot_workspace_path: str = "",
    copilot_agent_name: str = "",
    copilot_provider_log_path: str = "",
    copilot_process_id: str = "",
    copilot_error: str = "",
    prepared_at: Optional[str] = None,
    agent_result_path: str = "",
    auto_flow_enabled: bool = False,
) -> None:
    timestamp = utc_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE work_item_state
            SET copilot_status = ?,
                copilot_context_path = ?,
                copilot_workspace_path = ?,
                copilot_agent_name = ?,
                copilot_provider_log_path = CASE WHEN ? != '' THEN ? ELSE copilot_provider_log_path END,
                copilot_process_id = CASE WHEN ? != '' THEN ? ELSE copilot_process_id END,
                copilot_error = ?,
                copilot_prepared_at = ?,
                agent_result_path = CASE WHEN ? != '' THEN ? ELSE agent_result_path END,
                auto_flow_enabled = ?,
                updated_at = ?
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (
                copilot_status,
                copilot_context_path,
                copilot_workspace_path,
                copilot_agent_name,
                copilot_provider_log_path,
                copilot_provider_log_path,
                copilot_process_id,
                copilot_process_id,
                copilot_error,
                prepared_at or timestamp,
                agent_result_path,
                agent_result_path,
                1 if auto_flow_enabled else 0,
                timestamp,
                portal,
                work_item_id,
            ),
        )


def mark_auto_flow_enabled(
    *,
    portal: str,
    work_item_id: int,
    enabled: bool,
) -> None:
    timestamp = utc_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE work_item_state
            SET auto_flow_enabled = ?,
                updated_at = ?
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (
                1 if enabled else 0,
                timestamp,
                portal,
                work_item_id,
            ),
        )


def mark_agent_repair_started(
    *,
    portal: str,
    work_item_id: int,
    reason: str = "",
    max_attempts: int | None = None,
) -> int:
    timestamp = utc_now()
    with connect() as connection:
        if max_attempts is None:
            cursor = connection.execute(
                """
                UPDATE work_item_state
                SET agent_repair_count = COALESCE(agent_repair_count, 0) + 1,
                    agent_repair_last_started_at = ?,
                    agent_repair_last_reason = ?,
                    updated_at = ?
                WHERE portal = ?
                  AND work_item_id = ?
                """,
                (
                    timestamp,
                    str(reason or "").strip()[:2000],
                    timestamp,
                    portal,
                    work_item_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                UPDATE work_item_state
                SET agent_repair_count = COALESCE(agent_repair_count, 0) + 1,
                    agent_repair_last_started_at = ?,
                    agent_repair_last_reason = ?,
                    updated_at = ?
                WHERE portal = ?
                  AND work_item_id = ?
                  AND COALESCE(agent_repair_count, 0) < ?
                """,
                (
                    timestamp,
                    str(reason or "").strip()[:2000],
                    timestamp,
                    portal,
                    work_item_id,
                    int(max_attempts),
                ),
            )
        row = connection.execute(
            """
            SELECT COALESCE(agent_repair_count, 0) AS count
            FROM work_item_state
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (portal, work_item_id),
        ).fetchone()
    if max_attempts is not None and cursor.rowcount == 0:
        return 0
    return int(row["count"] if row else 0)


def mark_agent_result(
    *,
    portal: str,
    work_item_id: int,
    agent_result_status: str,
    agent_result_path: str = "",
    agent_result_summary: str = "",
    agent_result_error: str = "",
) -> None:
    timestamp = utc_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE work_item_state
            SET agent_result_status = ?,
                agent_result_path = CASE WHEN ? != '' THEN ? ELSE agent_result_path END,
                agent_result_summary = CASE WHEN ? != '' THEN ? ELSE agent_result_summary END,
                agent_result_error = ?,
                agent_result_checked_at = ?,
                updated_at = ?
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (
                agent_result_status,
                agent_result_path,
                agent_result_path,
                agent_result_summary,
                agent_result_summary,
                agent_result_error,
                timestamp,
                timestamp,
                portal,
                work_item_id,
            ),
        )


def mark_push_result(
    *,
    portal: str,
    work_item_id: int,
    push_status: str,
    push_commit: str = "",
    push_error: str = "",
    pushed_at: Optional[str] = None,
) -> None:
    timestamp = utc_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE work_item_state
            SET push_status = ?,
                push_commit = ?,
                push_error = ?,
                pushed_at = ?,
                updated_at = ?
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (
                push_status,
                push_commit,
                push_error,
                pushed_at or (timestamp if push_status == "pushed" else ""),
                timestamp,
                portal,
                work_item_id,
            ),
        )


def mark_final_report(
    *,
    portal: str,
    work_item_id: int,
    final_report_path: str,
    created_at: Optional[str] = None,
) -> None:
    timestamp = utc_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE work_item_state
            SET final_report_path = ?,
                final_report_created_at = ?,
                updated_at = ?
            WHERE portal = ?
              AND work_item_id = ?
            """,
            (
                final_report_path,
                created_at or timestamp,
                timestamp,
                portal,
                work_item_id,
            ),
        )
