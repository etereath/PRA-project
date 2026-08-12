from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from app.enums import (
    IncidentStatus,
    PricingSource,
    ReviewTaskStatus,
    SellerPhase,
    TaskActionType,
    TaskOriginType,
    TaskStatus,
)
from app.emergency_offline_fence import (
    EMERGENCY_HUMAN_PREEMPTED_TASK_MESSAGE,
    has_emergency_final_click_fence_won,
)
from app.exceptions import (
    MobileReviewErrorCode,
    MobileReviewTransactionError,
    NotificationDeliveryError,
    NotificationLeaseError,
)
from app.listing_identity import normalize_listing_text, require_listing_identity
from app.mobile_review import (
    normalize_mobile_review_resolution_payload,
    resolution_payload_summary,
)
from app.models import (
    ExecutionLog,
    ListingStatus,
    MobileReviewAtomicResult,
    NotificationDeliveryAttempt,
    NotificationDeliveryResult,
    NotificationLog,
    NotificationOutbox,
    OperationalIncidentEvent,
    RetryAuthorization,
    ReviewTask,
    ReviewToken,
    ScriptRun,
    ScriptRunItem,
    ShadowBotExecutionAttempt,
    ShadowBotOperationLedger,
    ShadowBotSideEffectCheckpoint,
    Task,
    TaskStatusHistory,
)
from app.repositories.sqlite_connection import (
    SQLiteConnectionConfig,
    SQLiteConnectionError,
    SQLiteConnectionFactory,
    SQLiteOperationalHealth,
    _execute_with_sqlite_retry,
    is_sqlite_concurrency_error,
)
from app.review_policy import (
    allowed_review_statuses,
    retry_task_deadline,
    review_business_decision,
    review_source_task_ids,
)
from app.runtime_schema import (
    LATEST_RUNTIME_SCHEMA_VERSION,
    V14_APPEND_ONLY_TABLES,
    V15_APPEND_ONLY_TABLES,
    V17_APPEND_ONLY_TABLES,
    RuntimeSchemaHealth,
    inspect_runtime_schema,
)
from app.utils import serialize_decimal, utc_now

TERMINAL_TASK_STATUSES = ("success", "skipped", "cancelled", "expired")

OPEN_TASK_DEDUPE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_open_dedupe
ON tasks(dedupe_key)
WHERE dedupe_key <> ''
  AND task_status NOT IN ('success', 'failed', 'skipped', 'cancelled', 'expired')
"""

MOBILE_REVIEW_ACTIONS = frozenset(
    {
        ReviewTaskStatus.APPROVED.value,
        ReviewTaskStatus.REJECTED.value,
        ReviewTaskStatus.ADJUSTED.value,
        ReviewTaskStatus.CANCELLED.value,
    }
)

MANUAL_REVIEW_SOURCE_ACTIONS = frozenset(
    {
        TaskActionType.CAPACITY_WARNING,
        TaskActionType.LABOR_REQUIRED,
        TaskActionType.MANUAL_PRICE_REVIEW,
        TaskActionType.BELOW_BREAK_EVEN_REVIEW,
        TaskActionType.SHORTAGE_WARNING,
        TaskActionType.COLD_STORAGE_WARNING,
        TaskActionType.CLEARANCE_WARNING,
        TaskActionType.MANUAL_REVIEW,
    }
)

ATOMIC_TASK_TRANSITIONS = {
    TaskStatus.PENDING: {
        TaskStatus.RUNNING,
        TaskStatus.MANUAL_REVIEW,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
        TaskStatus.EXPIRED,
    },
    TaskStatus.MANUAL_REVIEW: {
        TaskStatus.PENDING,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
        TaskStatus.EXPIRED,
    },
}

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS runtime_schema_migrations (
        schema_version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        trade_date TEXT,
        scope_type TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        dedupe_key TEXT NOT NULL DEFAULT '',
        internal_sku TEXT,
        platform_name TEXT,
        action_type TEXT NOT NULL,
        priority INTEGER NOT NULL,
        task_status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        scheduled_at TEXT,
        expires_at TEXT,
        expected_old_price TEXT,
        target_price TEXT,
        target_inventory INTEGER CHECK (
            target_inventory IS NULL OR target_inventory >= 0
        ),
        target_status TEXT,
        pricing_source TEXT,
        decision_trace_json TEXT NOT NULL DEFAULT '{}',
        result_message TEXT NOT NULL DEFAULT '',
        required_by TEXT,
        updated_at TEXT NOT NULL
    )
    """,
    OPEN_TASK_DEDUPE_INDEX_SQL,
    """
    CREATE TABLE IF NOT EXISTS review_tasks (
        review_task_id TEXT PRIMARY KEY,
        trade_date TEXT,
        scope_type TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        dedupe_key TEXT NOT NULL DEFAULT '',
        source_task_id TEXT,
        review_type TEXT NOT NULL,
        review_status TEXT NOT NULL,
        internal_sku TEXT,
        platform_name TEXT,
        reason TEXT NOT NULL DEFAULT '',
        review_payload_json TEXT NOT NULL DEFAULT '{}',
        resolution_payload_json TEXT NOT NULL DEFAULT '{}',
        required_by TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        resolved_by TEXT NOT NULL DEFAULT '',
        resolved_at TEXT,
        resolution_note TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(source_task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS review_tokens (
        token_id TEXT PRIMARY KEY,
        review_task_id TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        token_subject TEXT NOT NULL,
        allowed_actions TEXT NOT NULL DEFAULT '[]',
        expires_at TEXT NOT NULL,
        used_at TEXT,
        revoked_at TEXT,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL DEFAULT 'system',
        last_used_at TEXT,
        note TEXT,
        FOREIGN KEY(review_task_id) REFERENCES review_tasks(review_task_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_review_tokens_review_task_id
    ON review_tokens(review_task_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_review_tokens_expires_at
    ON review_tokens(expires_at)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_review_tasks_pending_dedupe
    ON review_tasks(dedupe_key)
    WHERE dedupe_key <> '' AND review_status = 'pending'
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_logs (
        log_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        executor_name TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT,
        success_flag INTEGER,
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        raw_output TEXT NOT NULL DEFAULT '',
        ai_model_version TEXT NOT NULL DEFAULT '',
        ai_summary TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_logs (
        notification_id TEXT PRIMARY KEY,
        related_task_id TEXT,
        related_review_task_id TEXT,
        recipient_type TEXT NOT NULL,
        recipient TEXT NOT NULL,
        channel TEXT NOT NULL,
        sent_at TEXT,
        send_status TEXT NOT NULL,
        dedupe_key TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(related_task_id) REFERENCES tasks(task_id),
        FOREIGN KEY(related_review_task_id) REFERENCES review_tasks(review_task_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_logs_dedupe
    ON notification_logs(dedupe_key)
    WHERE dedupe_key <> ''
    """,
    """
    CREATE TABLE IF NOT EXISTS task_status_history (
        history_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT NOT NULL,
        changed_by TEXT NOT NULL,
        changed_at TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS script_runs (
        script_run_id TEXT PRIMARY KEY,
        evaluator_id TEXT NOT NULL,
        evaluator_name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        run_mode TEXT NOT NULL,
        run_status TEXT NOT NULL,
        trade_date TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        summary_json TEXT NOT NULL DEFAULT '{}',
        error_message TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT 'system'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS script_run_items (
        item_id TEXT PRIMARY KEY,
        script_run_id TEXT NOT NULL,
        proposal_type TEXT NOT NULL,
        dedupe_key TEXT NOT NULL,
        severity TEXT NOT NULL,
        item_status TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL DEFAULT '{}',
        decision_trace_json TEXT NOT NULL DEFAULT '{}',
        related_task_id TEXT,
        related_review_task_id TEXT,
        related_notification_id TEXT,
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(script_run_id) REFERENCES script_runs(script_run_id),
        FOREIGN KEY(related_task_id) REFERENCES tasks(task_id),
        FOREIGN KEY(related_review_task_id) REFERENCES review_tasks(review_task_id),
        FOREIGN KEY(related_notification_id) REFERENCES notification_logs(notification_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_script_run_items_script_run_id
    ON script_run_items(script_run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_script_runs_started_at
    ON script_runs(started_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_operations (
        operation_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        product_identity_json TEXT NOT NULL DEFAULT '{}',
        expected_old_price TEXT NOT NULL,
        target_price TEXT NOT NULL,
        status TEXT NOT NULL,
        lock_owner TEXT NOT NULL DEFAULT '',
        approved_payload_hash TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_execution_attempts (
        execution_attempt_id TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL,
        execution_mode TEXT NOT NULL,
        shadowbot_run_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL,
        side_effect_state TEXT NOT NULL,
        started_at TEXT NOT NULL,
        instruction_hash TEXT NOT NULL DEFAULT '',
        request_file_sha256 TEXT NOT NULL DEFAULT '',
        queue_request_path TEXT NOT NULL DEFAULT '',
        ended_at TEXT,
        raw_output_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_execution_attempts_operation_id
    ON shadowbot_execution_attempts(operation_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_side_effect_checkpoints (
        operation_id TEXT NOT NULL,
        execution_attempt_id TEXT NOT NULL,
        side_effect_state TEXT NOT NULL,
        checkpoint_at TEXT NOT NULL,
        version INTEGER NOT NULL,
        PRIMARY KEY(operation_id, version),
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id),
        FOREIGN KEY(execution_attempt_id) REFERENCES shadowbot_execution_attempts(execution_attempt_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS retry_authorizations (
        retry_authorization_id TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL,
        source_execution_attempt_id TEXT NOT NULL,
        authorization_type TEXT NOT NULL,
        authorized_by TEXT NOT NULL,
        evidence_type TEXT NOT NULL,
        evidence_hash TEXT NOT NULL,
        approved_payload_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CONSUMED', 'EXPIRED', 'REVOKED')),
        max_uses INTEGER NOT NULL DEFAULT 1 CHECK (max_uses = 1),
        consumed_by_execution_attempt_id TEXT,
        expires_at TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        consumed_at TEXT,
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id),
        FOREIGN KEY(source_execution_attempt_id) REFERENCES shadowbot_execution_attempts(execution_attempt_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_retry_authorizations_evidence_hash
    ON retry_authorizations(evidence_hash)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_retry_authorizations_consumed_by_execution_attempt_id
    ON retry_authorizations(consumed_by_execution_attempt_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_retry_authorizations_operation_id
    ON retry_authorizations(operation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_retry_authorizations_status
    ON retry_authorizations(status)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_retry_authorizations_expires_at
    ON retry_authorizations(expires_at)
    """,
]

# Schema v6 is deliberately kept separate from the historical v1-v5 DDL.  The
# same statements upgrade an existing v5 database and initialize a new one,
# while preserving the published v5 migration history and notification_logs.
SCHEMA_V6_SQL = [
    """
    CREATE TABLE IF NOT EXISTS notification_outbox (
        notification_id TEXT PRIMARY KEY,
        notification_key TEXT NOT NULL UNIQUE,
        notification_type TEXT NOT NULL,
        related_task_id TEXT,
        related_review_task_id TEXT,
        recipient_type TEXT NOT NULL,
        recipient_ref TEXT NOT NULL,
        channel TEXT NOT NULL,
        priority INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN (
            'PENDING', 'LEASED', 'SENDING', 'RETRY_WAIT', 'SENT',
            'UNKNOWN_DELIVERY', 'FAILED', 'EXPIRED', 'CANCELLED'
        )),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
        next_attempt_at TEXT,
        deadline_at TEXT,
        lease_owner_token TEXT NOT NULL DEFAULT '',
        lease_version INTEGER NOT NULL DEFAULT 0 CHECK (lease_version >= 0),
        lease_expires_at TEXT,
        sent_at TEXT,
        provider_message_id TEXT NOT NULL DEFAULT '',
        last_error_code TEXT NOT NULL DEFAULT '',
        last_error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(related_task_id) REFERENCES tasks(task_id),
        FOREIGN KEY(related_review_task_id) REFERENCES review_tasks(review_task_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_delivery_attempts (
        delivery_attempt_id TEXT PRIMARY KEY,
        notification_id TEXT NOT NULL,
        attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
        status TEXT NOT NULL CHECK (status IN (
            'STARTED', 'ACKNOWLEDGED', 'TEMP_FAILED', 'PERM_FAILED', 'UNKNOWN'
        )),
        lease_owner_token TEXT NOT NULL,
        lease_version INTEGER NOT NULL CHECK (lease_version >= 0),
        request_fingerprint TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        provider_status_code TEXT NOT NULL DEFAULT '',
        provider_message_id TEXT NOT NULL DEFAULT '',
        response_fingerprint TEXT NOT NULL DEFAULT '',
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        UNIQUE(notification_id, attempt_no),
        FOREIGN KEY(notification_id) REFERENCES notification_outbox(notification_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_outbox_key
    ON notification_outbox(notification_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_notification_outbox_claim
    ON notification_outbox(status, priority, next_attempt_at, deadline_at, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_notification_outbox_lease_expires_at
    ON notification_outbox(lease_expires_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_notification_delivery_attempts_notification_id
    ON notification_delivery_attempts(notification_id)
    """,
]

SCHEMA_V7_SQL = [
    """
    CREATE TABLE IF NOT EXISTS listing_status (
        listing_status_id TEXT PRIMARY KEY,
        platform_name TEXT NOT NULL,
        internal_sku TEXT NOT NULL DEFAULT '',
        variety TEXT NOT NULL,
        grade TEXT NOT NULL,
        current_price TEXT NOT NULL,
        platform_stock_qty INTEGER NOT NULL DEFAULT 100 CHECK (platform_stock_qty >= 0),
        sold_qty INTEGER NOT NULL DEFAULT 0 CHECK (sold_qty >= 0),
        online_status TEXT NOT NULL DEFAULT 'online' CHECK (online_status IN ('online', 'offline')),
        source TEXT NOT NULL DEFAULT 'manual',
        updated_at TEXT NOT NULL,
        inventory_source TEXT NOT NULL DEFAULT 'default',
        inventory_observed_at TEXT,
        inventory_source_attempt_id TEXT NOT NULL DEFAULT '',
        UNIQUE(platform_name, variety, grade)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_listing_status_platform_variety
    ON listing_status(platform_name, variety, grade)
    """,
]


def _migrate_listing_status_to_v9(connection: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(listing_status)").fetchall()
    }
    table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'listing_status'"
    ).fetchone()
    table_sql = str(table_row[0] or "") if table_row else ""
    required = {
        "grade",
        "inventory_source",
        "inventory_observed_at",
        "inventory_source_attempt_id",
    }
    has_nonnegative_stock = re.search(
        r"CHECK\s*\(\s*platform_stock_qty\s*>=\s*0\s*\)", table_sql, re.IGNORECASE
    )
    has_platform_identity = re.search(
        r"UNIQUE\s*\(\s*platform_name\s*,\s*variety\s*,\s*grade\s*\)",
        table_sql,
        re.IGNORECASE,
    )
    if required.issubset(columns) and has_nonnegative_stock and has_platform_identity:
        return

    connection.execute("DROP TABLE IF EXISTS listing_status_v9")
    connection.execute(
        """
        CREATE TABLE listing_status_v9 (
            listing_status_id TEXT PRIMARY KEY,
            platform_name TEXT NOT NULL,
            internal_sku TEXT NOT NULL DEFAULT '',
            variety TEXT NOT NULL,
            grade TEXT NOT NULL,
            current_price TEXT NOT NULL,
            platform_stock_qty INTEGER NOT NULL DEFAULT 100 CHECK (platform_stock_qty >= 0),
            sold_qty INTEGER NOT NULL DEFAULT 0 CHECK (sold_qty >= 0),
            online_status TEXT NOT NULL DEFAULT 'online' CHECK (online_status IN ('online', 'offline')),
            source TEXT NOT NULL DEFAULT 'manual',
            updated_at TEXT NOT NULL,
            inventory_source TEXT NOT NULL DEFAULT 'default',
            inventory_observed_at TEXT,
            inventory_source_attempt_id TEXT NOT NULL DEFAULT '',
            UNIQUE(platform_name, variety, grade)
        )
        """
    )
    inventory_source_expr = (
        "inventory_source" if "inventory_source" in columns else "'default'"
    )
    observed_at_expr = (
        "inventory_observed_at" if "inventory_observed_at" in columns else "NULL"
    )
    attempt_expr = (
        "inventory_source_attempt_id"
        if "inventory_source_attempt_id" in columns
        else "''"
    )
    grade_expr = (
        "CASE WHEN TRIM(grade) <> '' THEN grade ELSE 'LEGACY_UNMAPPED:' || listing_status_id END"
        if "grade" in columns
        else "'LEGACY_UNMAPPED:' || listing_status_id"
    )
    connection.execute(
        f"""
        INSERT INTO listing_status_v9(
            listing_status_id, platform_name, internal_sku, variety, grade, current_price,
            platform_stock_qty, sold_qty, online_status, source, updated_at,
            inventory_source, inventory_observed_at, inventory_source_attempt_id
        )
        SELECT listing_status_id, platform_name, internal_sku, variety, {grade_expr}, current_price,
               platform_stock_qty, sold_qty, online_status, source, updated_at,
               {inventory_source_expr}, {observed_at_expr}, {attempt_expr}
        FROM listing_status
        """
    )
    connection.execute("DROP TABLE listing_status")
    connection.execute("ALTER TABLE listing_status_v9 RENAME TO listing_status")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS ix_listing_status_platform_variety "
        "ON listing_status(platform_name, variety, grade)"
    )


SCHEMA_V11_SQL = [
    """
    CREATE TABLE IF NOT EXISTS shadowbot_commit_batches (
        batch_id TEXT PRIMARY KEY,
        contract_version INTEGER NOT NULL CHECK (contract_version = 4),
        execution_profile TEXT NOT NULL CHECK (execution_profile IN ('development', 'production')),
        platform_name TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        instruction_hash TEXT NOT NULL DEFAULT '',
        execution_attempt_id TEXT NOT NULL DEFAULT '',
        result_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (status IN (
            'PREPARED', 'PUBLISHING', 'QUEUED', 'RUNNING', 'VERIFIED', 'PARTIAL', 'FAILED', 'UNKNOWN'
        )),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_commit_batch_items (
        batch_id TEXT NOT NULL,
        source_task_id TEXT NOT NULL,
        internal_sku TEXT NOT NULL,
        expected_product_name TEXT NOT NULL,
        expected_grade TEXT NOT NULL,
        expected_old_price TEXT NOT NULL,
        target_price TEXT NOT NULL,
        item_payload_sha256 TEXT NOT NULL,
        preflight_row INTEGER,
        preflight_price TEXT,
        execution_ordinal INTEGER,
        submit_attempted INTEGER NOT NULL DEFAULT 0 CHECK (submit_attempted IN (0, 1)),
        actual_price TEXT,
        status TEXT NOT NULL CHECK (status IN (
            'PENDING', 'NOT_ATTEMPTED', 'VERIFIED', 'NOT_APPLIED', 'FAILED', 'UNKNOWN'
        )),
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        PRIMARY KEY(batch_id, source_task_id),
        UNIQUE(batch_id, internal_sku),
        FOREIGN KEY(batch_id) REFERENCES shadowbot_commit_batches(batch_id),
        FOREIGN KEY(source_task_id) REFERENCES tasks(task_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_commit_batches_status
    ON shadowbot_commit_batches(status, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_commit_batch_items_status
    ON shadowbot_commit_batch_items(batch_id, status, execution_ordinal)
    """,
]

SCHEMA_V12_SQL = [
    """
    CREATE TABLE IF NOT EXISTS shadowbot_write_locks (
        write_identity_key TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL UNIQUE,
        item_execution_attempt_id TEXT NOT NULL,
        batch_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'UNKNOWN', 'RELEASED')),
        acquired_at TEXT NOT NULL,
        released_at TEXT,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id),
        FOREIGN KEY(item_execution_attempt_id) REFERENCES shadowbot_execution_attempts(execution_attempt_id),
        FOREIGN KEY(batch_id) REFERENCES shadowbot_commit_batches(batch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_commit_result_receipts (
        result_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        execution_attempt_id TEXT NOT NULL,
        instruction_hash TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        result_sha256 TEXT NOT NULL,
        source_result_path TEXT NOT NULL DEFAULT '',
        accepted_at TEXT NOT NULL,
        ack_state TEXT NOT NULL CHECK (ack_state IN ('PENDING', 'WRITTEN', 'FAILED')),
        ack_updated_at TEXT,
        last_projection_error TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(batch_id) REFERENCES shadowbot_commit_batches(batch_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_shadowbot_commit_batch_items_item_id
    ON shadowbot_commit_batch_items(item_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_commit_batch_items_operation_id
    ON shadowbot_commit_batch_items(operation_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_shadowbot_commit_batch_items_attempt_id
    ON shadowbot_commit_batch_items(item_execution_attempt_id)
    WHERE item_execution_attempt_id <> ''
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_shadowbot_write_locks_operation_id
    ON shadowbot_write_locks(operation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_commit_result_receipts_batch_id
    ON shadowbot_commit_result_receipts(batch_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_commit_result_receipts_ack_state
    ON shadowbot_commit_result_receipts(ack_state, accepted_at)
    """,
]

SCHEMA_V13_REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS shadowbot_batch_registry (
    batch_id TEXT PRIMARY KEY,
    batch_type TEXT NOT NULL CHECK (batch_type IN (
        'update_price', 'set_online', 'set_offline', 'sync_status'
    )),
    contract_version INTEGER NOT NULL CHECK (contract_version IN (4, 5)),
    platform_name TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

SCHEMA_V13_SQL = [
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_batch_registry_type
    ON shadowbot_batch_registry(batch_type, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_listing_action_batches (
        batch_id TEXT PRIMARY KEY,
        contract_version INTEGER NOT NULL CHECK (contract_version = 5),
        execution_profile TEXT NOT NULL CHECK (
            execution_profile IN ('development', 'production')
        ),
        action_type TEXT NOT NULL CHECK (
            action_type IN ('set_online', 'set_offline', 'sync_status')
        ),
        platform_name TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        instruction_hash TEXT NOT NULL DEFAULT '',
        execution_attempt_id TEXT NOT NULL DEFAULT '',
        result_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL CHECK (status IN (
            'PREPARED', 'PUBLISHING', 'QUEUED', 'RUNNING',
            'VERIFIED', 'PARTIAL', 'FAILED', 'UNKNOWN'
        )),
        batch_target_count INTEGER NOT NULL DEFAULT 0 CHECK (
            batch_target_count >= 0
        ),
        verified_count INTEGER NOT NULL DEFAULT 0 CHECK (verified_count >= 0),
        unknown_count INTEGER NOT NULL DEFAULT 0 CHECK (unknown_count >= 0),
        partial_effect_count INTEGER NOT NULL DEFAULT 0 CHECK (
            partial_effect_count >= 0
        ),
        not_attempted_count INTEGER NOT NULL DEFAULT 0 CHECK (
            not_attempted_count >= 0
        ),
        failed_count INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(batch_id) REFERENCES shadowbot_batch_registry(batch_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_listing_action_batch_items (
        item_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        source_task_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        item_execution_attempt_id TEXT NOT NULL,
        internal_sku TEXT NOT NULL,
        expected_product_name TEXT NOT NULL,
        expected_grade TEXT NOT NULL,
        item_payload_sha256 TEXT NOT NULL,
        write_identity_key TEXT NOT NULL,
        page_identity_key TEXT NOT NULL,
        expected_old_status TEXT NOT NULL,
        target_status TEXT NOT NULL,
        target_price TEXT,
        target_inventory INTEGER CHECK (
            target_inventory IS NULL OR target_inventory >= 0
        ),
        detail_effect_state TEXT NOT NULL DEFAULT 'NOT_STARTED' CHECK (
            detail_effect_state IN (
                'NOT_STARTED', 'NOT_APPLIED', 'VERIFIED', 'PARTIAL', 'UNKNOWN'
            )
        ),
        listing_effect_state TEXT NOT NULL DEFAULT 'NOT_STARTED' CHECK (
            listing_effect_state IN (
                'NOT_STARTED', 'NOT_APPLIED', 'VERIFIED', 'PARTIAL', 'UNKNOWN'
            )
        ),
        observed_price_before_action TEXT,
        observed_inventory_before_action INTEGER CHECK (
            observed_inventory_before_action IS NULL
            OR observed_inventory_before_action >= 0
        ),
        observed_price_after_detail_save TEXT,
        observed_inventory_after_detail_save INTEGER CHECK (
            observed_inventory_after_detail_save IS NULL
            OR observed_inventory_after_detail_save >= 0
        ),
        detail_save_clicked_at TEXT,
        action_clicked_at TEXT,
        readback_observed_at TEXT,
        operation_result TEXT NOT NULL DEFAULT '' CHECK (
            operation_result IN (
                '', 'NOT_ATTEMPTED', 'VERIFIED', 'NOT_APPLIED', 'PARTIALLY_APPLIED',
                'NEEDS_RECONCILIATION'
            )
        ),
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        FOREIGN KEY(batch_id)
            REFERENCES shadowbot_listing_action_batches(batch_id),
        FOREIGN KEY(source_task_id) REFERENCES tasks(task_id),
        FOREIGN KEY(operation_id)
            REFERENCES shadowbot_operations(operation_id),
        FOREIGN KEY(item_execution_attempt_id)
            REFERENCES shadowbot_execution_attempts(execution_attempt_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_listing_action_batches_status
    ON shadowbot_listing_action_batches(status, created_at)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        ux_shadowbot_listing_action_batch_items_batch_sku
    ON shadowbot_listing_action_batch_items(batch_id, internal_sku)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        ux_shadowbot_listing_action_batch_items_operation_id
    ON shadowbot_listing_action_batch_items(operation_id)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS
        ux_shadowbot_listing_action_batch_items_attempt_id
    ON shadowbot_listing_action_batch_items(item_execution_attempt_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS shadowbot_listing_result_receipts (
        result_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        execution_attempt_id TEXT NOT NULL,
        instruction_hash TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        result_sha256 TEXT NOT NULL,
        source_result_path TEXT NOT NULL DEFAULT '',
        accepted_at TEXT NOT NULL,
        ack_state TEXT NOT NULL CHECK (
            ack_state IN ('PENDING', 'WRITTEN', 'FAILED')
        ),
        ack_updated_at TEXT,
        last_projection_error TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(batch_id)
            REFERENCES shadowbot_listing_action_batches(batch_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_shadowbot_listing_result_receipts_batch_id
    ON shadowbot_listing_result_receipts(batch_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS listing_sync_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        batch_id TEXT NOT NULL,
        platform_name TEXT NOT NULL,
        execution_attempt_id TEXT NOT NULL,
        scan_started_at TEXT NOT NULL,
        scan_completed_at TEXT NOT NULL,
        online_scan_started_at TEXT NOT NULL,
        online_scan_completed_at TEXT NOT NULL,
        waiting_scan_started_at TEXT NOT NULL,
        waiting_scan_completed_at TEXT NOT NULL,
        online_scan_complete INTEGER NOT NULL CHECK (
            online_scan_complete IN (0, 1)
        ),
        waiting_scan_complete INTEGER NOT NULL CHECK (
            waiting_scan_complete IN (0, 1)
        ),
        snapshot_complete INTEGER NOT NULL CHECK (
            snapshot_complete IN (0, 1)
        ),
        online_end_marker_verified INTEGER NOT NULL CHECK (
            online_end_marker_verified IN (0, 1)
        ),
        waiting_end_marker_verified INTEGER NOT NULL CHECK (
            waiting_end_marker_verified IN (0, 1)
        ),
        instruction_hash TEXT NOT NULL,
        result_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('VERIFIED', 'FAILED')),
        error_code TEXT NOT NULL DEFAULT '',
        evidence_manifest_sha256 TEXT NOT NULL,
        CHECK (
            snapshot_complete = CASE
                WHEN online_scan_complete = 1
                 AND waiting_scan_complete = 1
                 AND online_end_marker_verified = 1
                 AND waiting_end_marker_verified = 1
                THEN 1 ELSE 0
            END
        ),
        FOREIGN KEY(batch_id)
            REFERENCES shadowbot_listing_action_batches(batch_id),
        FOREIGN KEY(result_id)
            REFERENCES shadowbot_listing_result_receipts(result_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_listing_sync_snapshots_platform_completed
    ON listing_sync_snapshots(platform_name, scan_completed_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS listing_sync_snapshot_items (
        snapshot_item_id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        internal_sku TEXT,
        product_name TEXT NOT NULL,
        grade TEXT NOT NULL,
        page_identity_key TEXT NOT NULL,
        affected_internal_skus_json TEXT NOT NULL DEFAULT '[]',
        online_occurrences INTEGER NOT NULL CHECK (online_occurrences >= 0),
        waiting_occurrences INTEGER NOT NULL CHECK (waiting_occurrences >= 0),
        listing_location TEXT NOT NULL CHECK (listing_location IN (
            'online_only', 'waiting_only', 'both', 'neither', 'ambiguous'
        )),
        online_row_identities_json TEXT NOT NULL DEFAULT '[]',
        waiting_row_identities_json TEXT NOT NULL DEFAULT '[]',
        online_observed_price TEXT,
        waiting_observed_price TEXT,
        online_observed_inventory INTEGER CHECK (
            online_observed_inventory IS NULL
            OR online_observed_inventory >= 0
        ),
        waiting_observed_inventory INTEGER CHECK (
            waiting_observed_inventory IS NULL
            OR waiting_observed_inventory >= 0
        ),
        diagnostic_code TEXT NOT NULL DEFAULT '',
        online_observed_at TEXT,
        waiting_observed_at TEXT,
        UNIQUE(snapshot_id, page_identity_key),
        FOREIGN KEY(snapshot_id)
            REFERENCES listing_sync_snapshots(snapshot_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_listing_sync_snapshot_items_snapshot
    ON listing_sync_snapshot_items(snapshot_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_listing_sync_snapshot_items_internal_sku
    ON listing_sync_snapshot_items(internal_sku, snapshot_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS listing_anomaly_cases (
        anomaly_case_id TEXT PRIMARY KEY,
        snapshot_id TEXT NOT NULL,
        snapshot_item_id TEXT NOT NULL,
        platform_name TEXT NOT NULL,
        internal_sku TEXT,
        page_identity_key TEXT NOT NULL,
        affected_internal_skus_json TEXT NOT NULL DEFAULT '[]',
        anomaly_subject_key TEXT NOT NULL,
        dedupe_key TEXT NOT NULL,
        reason_code TEXT NOT NULL CHECK (reason_code IN (
            'UNMAPPED_PRODUCT', 'IDENTITY_MAPPING_CONFLICT',
            'ABSENT_FROM_BOTH_LISTS', 'DUPLICATE_PAGE_IDENTITY',
            'PRESENT_IN_BOTH_LISTS'
        )),
        diagnostic_message TEXT NOT NULL,
        resolution_policy TEXT NOT NULL CHECK (resolution_policy IN (
            'AUTO_CLEAR_BY_COMPLETE_SNAPSHOT', 'MANUAL_ONLY'
        )),
        blocked_actions_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        cleared_at TEXT,
        cleared_by_snapshot_id TEXT,
        review_task_id TEXT,
        CHECK (
            (cleared_at IS NULL AND cleared_by_snapshot_id IS NULL)
            OR (cleared_at IS NOT NULL AND cleared_by_snapshot_id IS NOT NULL)
        ),
        FOREIGN KEY(snapshot_id)
            REFERENCES listing_sync_snapshots(snapshot_id),
        FOREIGN KEY(snapshot_item_id)
            REFERENCES listing_sync_snapshot_items(snapshot_item_id),
        FOREIGN KEY(cleared_by_snapshot_id)
            REFERENCES listing_sync_snapshots(snapshot_id),
        FOREIGN KEY(review_task_id) REFERENCES review_tasks(review_task_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_listing_anomaly_cases_open_dedupe
    ON listing_anomaly_cases(dedupe_key)
    WHERE cleared_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_listing_anomaly_cases_snapshot
    ON listing_anomaly_cases(snapshot_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_listing_anomaly_cases_review
    ON listing_anomaly_cases(review_task_id)
    """,
]

SCHEMA_V14_SQL = [
    """
    CREATE TABLE IF NOT EXISTS operational_time_policies (
        policy_version TEXT PRIMARY KEY,
        timezone_name TEXT NOT NULL CHECK (timezone_name = 'Asia/Shanghai'),
        platform_cutoff_local_time TEXT NOT NULL,
        seller_cutoff_local_time TEXT NOT NULL,
        peak_start_local_time TEXT NOT NULL,
        effective_from TEXT NOT NULL,
        effective_to TEXT,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        supersedes_policy_version TEXT,
        CHECK (policy_version <> ''),
        CHECK (
            time(peak_start_local_time) IS NOT NULL
            AND time(platform_cutoff_local_time) IS NOT NULL
            AND time(seller_cutoff_local_time) IS NOT NULL
            AND time(peak_start_local_time)
                < time(platform_cutoff_local_time)
            AND time(platform_cutoff_local_time)
                < time(seller_cutoff_local_time)
        ),
        CHECK (
            julianday(effective_from) IS NOT NULL
            AND (
                substr(effective_from, -6) = '+00:00'
                OR substr(effective_from, -1) = 'Z'
            )
        ),
        CHECK (
            effective_to IS NULL
            OR (
                julianday(effective_to) IS NOT NULL
                AND julianday(effective_to) > julianday(effective_from)
                AND (
                    substr(effective_to, -6) = '+00:00'
                    OR substr(effective_to, -1) = 'Z'
                )
            )
        ),
        FOREIGN KEY(supersedes_policy_version)
            REFERENCES operational_time_policies(policy_version)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_operational_time_policies_current
    ON operational_time_policies(timezone_name)
    WHERE effective_to IS NULL
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_operational_time_policy_no_overlap_insert
    BEFORE INSERT ON operational_time_policies
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1
        FROM operational_time_policies AS existing
        WHERE existing.timezone_name = NEW.timezone_name
          AND julianday(NEW.effective_from)
              < COALESCE(julianday(existing.effective_to), 5373484.499999)
          AND julianday(existing.effective_from)
              < COALESCE(julianday(NEW.effective_to), 5373484.499999)
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'operational time policy effective ranges must not overlap'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_operational_time_policy_no_overlap_update
    BEFORE UPDATE ON operational_time_policies
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1
        FROM operational_time_policies AS existing
        WHERE existing.policy_version <> OLD.policy_version
          AND existing.timezone_name = NEW.timezone_name
          AND julianday(NEW.effective_from)
              < COALESCE(julianday(existing.effective_to), 5373484.499999)
          AND julianday(existing.effective_from)
              < COALESCE(julianday(NEW.effective_to), 5373484.499999)
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'operational time policy effective ranges must not overlap'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_operational_time_policy_immutable_update
    BEFORE UPDATE ON operational_time_policies
    FOR EACH ROW
    WHEN NOT (
        OLD.effective_to IS NULL
        AND NEW.effective_to IS NOT NULL
        AND NEW.policy_version IS OLD.policy_version
        AND NEW.timezone_name IS OLD.timezone_name
        AND NEW.platform_cutoff_local_time
            IS OLD.platform_cutoff_local_time
        AND NEW.seller_cutoff_local_time
            IS OLD.seller_cutoff_local_time
        AND NEW.peak_start_local_time IS OLD.peak_start_local_time
        AND NEW.effective_from IS OLD.effective_from
        AND NEW.created_at IS OLD.created_at
        AND NEW.created_by IS OLD.created_by
        AND NEW.supersedes_policy_version
            IS OLD.supersedes_policy_version
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'operational time policy versions are immutable after creation'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_operational_time_policy_no_delete
    BEFORE DELETE ON operational_time_policies
    FOR EACH ROW
    BEGIN
        SELECT RAISE(
            ABORT,
            'operational time policy versions cannot be deleted'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_operational_time_policy_successor_adjacent
    BEFORE INSERT ON operational_time_policies
    FOR EACH ROW
    WHEN EXISTS (
        SELECT 1 FROM operational_time_policies
    )
    AND NOT EXISTS (
        SELECT 1
        FROM operational_time_policies AS previous
        WHERE previous.policy_version = NEW.supersedes_policy_version
          AND previous.timezone_name = NEW.timezone_name
          AND previous.effective_to IS NOT NULL
          AND julianday(previous.effective_to)
              = julianday(NEW.effective_from)
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'operational time policy successor must be adjacent to its superseded version'
        );
    END
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_jobs (
        job_id TEXT PRIMARY KEY,
        job_type TEXT NOT NULL,
        display_name TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        schedule_kind TEXT NOT NULL,
        schedule_expression TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 100,
        config_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (job_id <> ''),
        CHECK (job_type <> ''),
        CHECK (schedule_kind <> '')
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_automation_jobs_type_enabled
    ON automation_jobs(job_type, enabled)
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_runs (
        run_id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL,
        job_type TEXT NOT NULL,
        logical_run_key TEXT NOT NULL,
        run_status TEXT NOT NULL CHECK (run_status IN (
            'SCHEDULED', 'RUNNING', 'SUCCESS', 'PARTIAL',
            'FAILED', 'MISSED', 'MERGED', 'SKIPPED', 'CANCELLED'
        )),
        platform_name TEXT NOT NULL,
        platform_trade_date TEXT NOT NULL,
        seller_operation_date TEXT NOT NULL,
        seller_phase TEXT NOT NULL CHECK (seller_phase IN (
            'NORMAL_SALES', 'PEAK_SALES', 'DELIVERY_OVERLAP'
        )),
        time_policy_version TEXT NOT NULL,
        scheduled_for TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        lease_owner TEXT NOT NULL DEFAULT '',
        lease_version INTEGER NOT NULL DEFAULT 0 CHECK (lease_version >= 0),
        lease_expires_at TEXT,
        input_manifest_sha256 TEXT NOT NULL DEFAULT '',
        output_manifest_sha256 TEXT NOT NULL DEFAULT '',
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY(job_id) REFERENCES automation_jobs(job_id),
        FOREIGN KEY(time_policy_version)
            REFERENCES operational_time_policies(policy_version)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_automation_runs_logical_key
    ON automation_runs(logical_run_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_automation_runs_status_scheduled
    ON automation_runs(run_status, scheduled_for)
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_run_events (
        event_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        from_status TEXT,
        to_status TEXT,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY(run_id) REFERENCES automation_runs(run_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_automation_run_events_run
    ON automation_run_events(run_id, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS automation_run_links (
        parent_run_id TEXT NOT NULL,
        child_run_id TEXT NOT NULL,
        relation_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(parent_run_id, child_run_id, relation_type),
        CHECK (parent_run_id <> child_run_id),
        FOREIGN KEY(parent_run_id) REFERENCES automation_runs(run_id),
        FOREIGN KEY(child_run_id) REFERENCES automation_runs(run_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_automation_run_links_child
    ON automation_run_links(child_run_id, relation_type)
    """,
    """
    CREATE TABLE IF NOT EXISTS product_observation_batches (
        observation_batch_id TEXT PRIMARY KEY,
        automation_run_id TEXT NOT NULL,
        platform_name TEXT NOT NULL,
        scan_type TEXT NOT NULL,
        batch_status TEXT NOT NULL CHECK (batch_status IN (
            'ACCEPTED', 'PARTIAL', 'UNAVAILABLE', 'FAILED'
        )),
        scan_started_at TEXT NOT NULL,
        scan_completed_at TEXT NOT NULL,
        requested_scope_json TEXT NOT NULL DEFAULT '{}',
        scope_complete INTEGER NOT NULL CHECK (scope_complete IN (0, 1)),
        end_marker_verified INTEGER NOT NULL CHECK (
            end_marker_verified IN (0, 1)
        ),
        content_sha256 TEXT NOT NULL,
        time_policy_version TEXT NOT NULL,
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(automation_run_id) REFERENCES automation_runs(run_id),
        FOREIGN KEY(time_policy_version)
            REFERENCES operational_time_policies(policy_version)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_product_observation_batches_run
    ON product_observation_batches(automation_run_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS product_observation_items (
        observation_item_id TEXT PRIMARY KEY,
        observation_batch_id TEXT NOT NULL,
        internal_sku TEXT,
        platform_product_name TEXT NOT NULL,
        grade TEXT NOT NULL,
        observed_price TEXT,
        observed_inventory INTEGER CHECK (
            observed_inventory IS NULL OR observed_inventory >= 0
        ),
        observed_online INTEGER NOT NULL CHECK (observed_online IN (0, 1)),
        observed_at TEXT NOT NULL,
        platform_trade_date TEXT NOT NULL,
        seller_operation_date TEXT NOT NULL,
        seller_phase TEXT NOT NULL CHECK (seller_phase IN (
            'NORMAL_SALES', 'PEAK_SALES', 'DELIVERY_OVERLAP'
        )),
        page_identity_key TEXT NOT NULL,
        mapping_status TEXT NOT NULL CHECK (mapping_status IN (
            'VERIFIED', 'UNMAPPED', 'AMBIGUOUS', 'DISABLED'
        )),
        mapping_version TEXT NOT NULL DEFAULT '',
        evidence_sha256 TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(observation_batch_id)
            REFERENCES product_observation_batches(observation_batch_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_product_observation_items_batch
    ON product_observation_items(observation_batch_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_product_observation_items_sku_trade_date
    ON product_observation_items(
        internal_sku, platform_trade_date, observed_at
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_observation_batches (
        observation_batch_id TEXT PRIMARY KEY,
        automation_run_id TEXT NOT NULL,
        platform_name TEXT NOT NULL,
        requested_platform_trade_date TEXT NOT NULL,
        trade_day_status TEXT NOT NULL CHECK (trade_day_status IN (
            'OPEN', 'CLOSED'
        )),
        capability_result TEXT NOT NULL CHECK (capability_result IN (
            'SUCCEEDED', 'UNSUPPORTED', 'UNAVAILABLE', 'FAILED'
        )),
        batch_status TEXT NOT NULL CHECK (batch_status IN (
            'ACCEPTED', 'PARTIAL', 'UNAVAILABLE', 'FAILED'
        )),
        scan_started_at TEXT NOT NULL,
        scan_completed_at TEXT NOT NULL,
        requested_range_json TEXT NOT NULL DEFAULT '{}',
        scope_complete INTEGER NOT NULL CHECK (scope_complete IN (0, 1)),
        end_marker_verified INTEGER NOT NULL CHECK (
            end_marker_verified IN (0, 1)
        ),
        content_sha256 TEXT NOT NULL,
        time_policy_version TEXT NOT NULL,
        error_code TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY(automation_run_id) REFERENCES automation_runs(run_id),
        FOREIGN KEY(time_policy_version)
            REFERENCES operational_time_policies(policy_version)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_order_observation_batches_run
    ON order_observation_batches(automation_run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_order_observation_batches_trade_date
    ON order_observation_batches(
        platform_name, requested_platform_trade_date, scan_completed_at
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS order_observation_items (
        observation_item_id TEXT PRIMARY KEY,
        observation_batch_id TEXT NOT NULL,
        platform_name TEXT NOT NULL,
        platform_trade_date TEXT NOT NULL,
        trade_day_status TEXT NOT NULL CHECK (trade_day_status IN (
            'OPEN', 'CLOSED'
        )),
        order_identity_fingerprint TEXT NOT NULL,
        occurrence_no INTEGER NOT NULL CHECK (occurrence_no > 0),
        order_created_at TEXT NOT NULL,
        platform_product_name TEXT NOT NULL,
        grade TEXT NOT NULL,
        internal_sku TEXT,
        mapping_status TEXT NOT NULL CHECK (mapping_status IN (
            'VERIFIED', 'UNMAPPED', 'AMBIGUOUS', 'DISABLED'
        )),
        mapping_version TEXT NOT NULL DEFAULT '',
        order_qty INTEGER NOT NULL CHECK (order_qty > 0),
        order_transaction_amount TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        seller_operation_date TEXT NOT NULL,
        seller_phase TEXT NOT NULL CHECK (seller_phase IN (
            'NORMAL_SALES', 'PEAK_SALES', 'DELIVERY_OVERLAP'
        )),
        raw_observation_sha256 TEXT NOT NULL,
        UNIQUE(
            observation_batch_id,
            order_identity_fingerprint,
            occurrence_no
        ),
        FOREIGN KEY(observation_batch_id)
            REFERENCES order_observation_batches(observation_batch_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_order_observation_items_batch
    ON order_observation_items(observation_batch_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_order_observation_items_trade_date
    ON order_observation_items(
        platform_trade_date, internal_sku, order_created_at
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sales_estimate_segments (
        estimate_segment_id TEXT PRIMARY KEY,
        platform_name TEXT NOT NULL,
        internal_sku TEXT NOT NULL,
        platform_trade_date TEXT NOT NULL,
        interval_started_at TEXT NOT NULL,
        interval_ended_at TEXT NOT NULL,
        inventory_before INTEGER NOT NULL CHECK (inventory_before >= 0),
        inventory_after INTEGER NOT NULL CHECK (inventory_after >= 0),
        known_inventory_adjustment INTEGER NOT NULL DEFAULT 0,
        known_adjustment_source_refs_json TEXT NOT NULL DEFAULT '[]',
        estimated_sold_qty INTEGER CHECK (
            estimated_sold_qty IS NULL OR estimated_sold_qty >= 0
        ),
        estimation_eligible INTEGER NOT NULL CHECK (
            estimation_eligible IN (0, 1)
        ),
        estimation_reason TEXT NOT NULL,
        quality_level TEXT NOT NULL CHECK (quality_level IN (
            'SCAN_ESTIMATED_HIGH', 'SCAN_ESTIMATED_MEDIUM',
            'SCAN_ESTIMATED_LOW'
        )),
        mapping_version TEXT NOT NULL,
        supporting_observation_ids_json TEXT NOT NULL,
        algorithm_version TEXT NOT NULL,
        created_at TEXT NOT NULL,
        CHECK (interval_ended_at > interval_started_at)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_sales_estimate_segments_scope
    ON sales_estimate_segments(
        platform_name, platform_trade_date, internal_sku
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_trade_day_summaries (
        summary_id TEXT PRIMARY KEY,
        summary_series_id TEXT NOT NULL,
        version_no INTEGER NOT NULL CHECK (version_no > 0),
        supersedes_summary_id TEXT,
        is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
        platform_name TEXT NOT NULL,
        platform_trade_date TEXT NOT NULL,
        seller_operation_date TEXT NOT NULL,
        seller_phase TEXT NOT NULL CHECK (seller_phase IN (
            'NORMAL_SALES', 'PEAK_SALES', 'DELIVERY_OVERLAP'
        )),
        scope_type TEXT NOT NULL CHECK (scope_type IN (
            'PLATFORM', 'VARIETY', 'GRADE', 'SKU', 'TIME_BUCKET'
        )),
        scope_key TEXT NOT NULL,
        fact_source TEXT CHECK (
            fact_source IS NULL OR fact_source IN (
                'ORDER_OBSERVED', 'SCAN_ESTIMATED'
            )
        ),
        quality_level TEXT NOT NULL CHECK (quality_level IN (
            'ORDER_COMPLETE', 'ORDER_PARTIAL',
            'SCAN_ESTIMATED_HIGH', 'SCAN_ESTIMATED_MEDIUM',
            'SCAN_ESTIMATED_LOW', 'UNAVAILABLE'
        )),
        summary_status TEXT NOT NULL CHECK (summary_status IN (
            'PROVISIONAL', 'OBSERVED', 'RECONCILED', 'FINAL'
        )),
        sold_qty INTEGER CHECK (sold_qty IS NULL OR sold_qty >= 0),
        order_count INTEGER CHECK (order_count IS NULL OR order_count >= 0),
        transaction_amount_total TEXT,
        quality_reason TEXT NOT NULL DEFAULT '',
        source_proportions_json TEXT NOT NULL DEFAULT '{}',
        input_manifest_sha256 TEXT NOT NULL,
        mapping_version TEXT NOT NULL DEFAULT '',
        algorithm_version TEXT NOT NULL,
        time_policy_version TEXT NOT NULL,
        finalized_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (
            (
                fact_source IS NULL
                AND quality_level = 'UNAVAILABLE'
                AND sold_qty IS NULL
                AND order_count IS NULL
                AND transaction_amount_total IS NULL
            )
            OR (
                fact_source = 'ORDER_OBSERVED'
                AND quality_level IN ('ORDER_COMPLETE', 'ORDER_PARTIAL')
            )
            OR (
                fact_source = 'SCAN_ESTIMATED'
                AND quality_level IN (
                    'SCAN_ESTIMATED_HIGH', 'SCAN_ESTIMATED_MEDIUM',
                    'SCAN_ESTIMATED_LOW'
                )
            )
        ),
        CHECK (
            summary_status <> 'FINAL'
            OR quality_level = 'ORDER_COMPLETE'
        ),
        CHECK (
            (summary_status = 'FINAL' AND finalized_at IS NOT NULL)
            OR (summary_status <> 'FINAL' AND finalized_at IS NULL)
        ),
        UNIQUE(summary_series_id, version_no),
        FOREIGN KEY(supersedes_summary_id)
            REFERENCES platform_trade_day_summaries(summary_id),
        FOREIGN KEY(time_policy_version)
            REFERENCES operational_time_policies(policy_version)
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_trade_day_summary_initial_status
    BEFORE INSERT ON platform_trade_day_summaries
    FOR EACH ROW
    WHEN NOT (
        (
            NEW.version_no = 1
            AND NEW.summary_status = 'PROVISIONAL'
            AND NEW.supersedes_summary_id IS NULL
        )
        OR (
            NEW.version_no > 1
            AND NEW.summary_status = 'OBSERVED'
            AND NEW.supersedes_summary_id IS NOT NULL
        )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'summary must start PROVISIONAL or a revision must start OBSERVED'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_trade_day_summary_status_transition
    BEFORE UPDATE OF summary_status ON platform_trade_day_summaries
    FOR EACH ROW
    WHEN OLD.summary_status <> NEW.summary_status
      AND NOT (
          (
              OLD.summary_status = 'PROVISIONAL'
              AND NEW.summary_status = 'OBSERVED'
          )
          OR (
              OLD.summary_status = 'OBSERVED'
              AND NEW.summary_status = 'RECONCILED'
          )
          OR (
              OLD.summary_status = 'RECONCILED'
              AND NEW.summary_status = 'FINAL'
          )
      )
    BEGIN
        SELECT RAISE(ABORT, 'illegal trade-day summary transition');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_trade_day_summary_final_immutable
    BEFORE UPDATE ON platform_trade_day_summaries
    FOR EACH ROW
    WHEN OLD.summary_status = 'FINAL'
      AND NOT (
          OLD.is_current = 1
          AND NEW.is_current = 0
          AND NEW.summary_id IS OLD.summary_id
          AND NEW.summary_series_id IS OLD.summary_series_id
          AND NEW.version_no IS OLD.version_no
          AND NEW.supersedes_summary_id IS OLD.supersedes_summary_id
          AND NEW.platform_name IS OLD.platform_name
          AND NEW.platform_trade_date IS OLD.platform_trade_date
          AND NEW.seller_operation_date IS OLD.seller_operation_date
          AND NEW.seller_phase IS OLD.seller_phase
          AND NEW.scope_type IS OLD.scope_type
          AND NEW.scope_key IS OLD.scope_key
          AND NEW.fact_source IS OLD.fact_source
          AND NEW.quality_level IS OLD.quality_level
          AND NEW.summary_status IS OLD.summary_status
          AND NEW.sold_qty IS OLD.sold_qty
          AND NEW.order_count IS OLD.order_count
          AND NEW.transaction_amount_total IS OLD.transaction_amount_total
          AND NEW.quality_reason IS OLD.quality_reason
          AND NEW.source_proportions_json IS OLD.source_proportions_json
          AND NEW.input_manifest_sha256 IS OLD.input_manifest_sha256
          AND NEW.mapping_version IS OLD.mapping_version
          AND NEW.algorithm_version IS OLD.algorithm_version
          AND NEW.time_policy_version IS OLD.time_policy_version
          AND NEW.finalized_at IS OLD.finalized_at
          AND NEW.created_at IS OLD.created_at
          AND NEW.updated_at IS OLD.updated_at
      )
    BEGIN
        SELECT RAISE(ABORT, 'FINAL summary content is immutable');
    END
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_day_summaries_current
    ON platform_trade_day_summaries(summary_series_id)
    WHERE is_current = 1
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_trade_day_summaries_scope
    ON platform_trade_day_summaries(
        platform_name, platform_trade_date, scope_type, scope_key
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_trade_day_summary_events (
        event_id TEXT PRIMARY KEY,
        summary_id TEXT NOT NULL,
        from_status TEXT CHECK (
            from_status IS NULL OR from_status IN (
                'PROVISIONAL', 'OBSERVED', 'RECONCILED', 'FINAL'
            )
        ),
        to_status TEXT NOT NULL CHECK (to_status IN (
            'PROVISIONAL', 'OBSERVED', 'RECONCILED', 'FINAL'
        )),
        trigger_type TEXT NOT NULL,
        trigger_ref_id TEXT NOT NULL DEFAULT '',
        fact_source_before TEXT CHECK (
            fact_source_before IS NULL OR fact_source_before IN (
                'ORDER_OBSERVED', 'SCAN_ESTIMATED'
            )
        ),
        fact_source_after TEXT CHECK (
            fact_source_after IS NULL OR fact_source_after IN (
                'ORDER_OBSERVED', 'SCAN_ESTIMATED'
            )
        ),
        quality_level_before TEXT CHECK (
            quality_level_before IS NULL OR quality_level_before IN (
                'ORDER_COMPLETE', 'ORDER_PARTIAL',
                'SCAN_ESTIMATED_HIGH', 'SCAN_ESTIMATED_MEDIUM',
                'SCAN_ESTIMATED_LOW', 'UNAVAILABLE'
            )
        ),
        quality_level_after TEXT NOT NULL CHECK (
            quality_level_after IN (
                'ORDER_COMPLETE', 'ORDER_PARTIAL',
                'SCAN_ESTIMATED_HIGH', 'SCAN_ESTIMATED_MEDIUM',
                'SCAN_ESTIMATED_LOW', 'UNAVAILABLE'
            )
        ),
        input_manifest_sha256 TEXT NOT NULL,
        changed_at TEXT NOT NULL,
        changed_by TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(summary_id)
            REFERENCES platform_trade_day_summaries(summary_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_trade_day_summary_events_summary
    ON platform_trade_day_summary_events(summary_id, changed_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS platform_trade_day_summary_inputs (
        summary_id TEXT NOT NULL,
        input_manifest_sha256 TEXT NOT NULL,
        input_type TEXT NOT NULL,
        input_ref_id TEXT NOT NULL,
        input_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(
            summary_id, input_manifest_sha256, input_type, input_ref_id
        ),
        FOREIGN KEY(summary_id)
            REFERENCES platform_trade_day_summaries(summary_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_trade_day_summary_inputs_ref
    ON platform_trade_day_summary_inputs(input_type, input_ref_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS operational_incidents (
        incident_id TEXT PRIMARY KEY,
        dedupe_key TEXT NOT NULL,
        category TEXT NOT NULL CHECK (category IN (
            'PLATFORM_LOGIN', 'PLATFORM_NETWORK', 'PAGE_STRUCTURE',
            'SCAN_INCOMPLETE', 'WORKER_UNAVAILABLE', 'QUEUE_BACKLOG',
            'PRODUCT_MAPPING', 'PRICE_ANOMALY', 'INVENTORY_ANOMALY',
            'ORDER_PAGE_UNAVAILABLE', 'ORDER_DATA_INCONSISTENT',
            'SALES_ESTIMATE_LOW_CONFIDENCE', 'NOTIFICATION_FAILURE',
            'WRITE_UNKNOWN'
        )),
        source_type TEXT NOT NULL,
        source_ref_id TEXT NOT NULL DEFAULT '',
        severity TEXT NOT NULL CHECK (severity IN (
            'S0', 'S1', 'S2', 'S3', 'S4'
        )),
        incident_status TEXT NOT NULL CHECK (incident_status IN (
            'OPEN', 'RETRYING', 'WAITING_HUMAN', 'ACKNOWLEDGED',
            'AUTO_PROTECTING', 'RESOLVED', 'CLOSED'
        )),
        blocks_finalization INTEGER NOT NULL DEFAULT 0 CHECK (
            blocks_finalization IN (0, 1)
        ),
        platform_name TEXT,
        platform_trade_date TEXT,
        seller_operation_date TEXT,
        subject_type TEXT NOT NULL,
        subject_key TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        first_detected_at TEXT NOT NULL,
        last_detected_at TEXT NOT NULL,
        resolved_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (
            (
                incident_status IN ('RESOLVED', 'CLOSED')
                AND resolved_at IS NOT NULL
            )
            OR (
                incident_status NOT IN ('RESOLVED', 'CLOSED')
                AND resolved_at IS NULL
            )
        )
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_operational_incidents_open_dedupe
    ON operational_incidents(dedupe_key)
    WHERE resolved_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_operational_incidents_status
    ON operational_incidents(incident_status, severity, last_detected_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS incident_notification_state (
        incident_id TEXT NOT NULL,
        channel TEXT NOT NULL,
        notification_count INTEGER NOT NULL DEFAULT 0 CHECK (
            notification_count >= 0
        ),
        last_notified_at TEXT,
        next_notification_at TEXT,
        escalation_state TEXT NOT NULL DEFAULT '',
        payload_sha256 TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        PRIMARY KEY(incident_id, channel),
        FOREIGN KEY(incident_id)
            REFERENCES operational_incidents(incident_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_incident_notification_state_due
    ON incident_notification_state(next_notification_at)
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_tasks_traceable_origin_insert
    BEFORE INSERT ON tasks
    FOR EACH ROW
    WHEN NEW.origin_type IN ('MANUAL', 'AUTOMATION')
      AND trim(COALESCE(NEW.origin_ref_id, '')) = ''
    BEGIN
        SELECT RAISE(
            ABORT,
            'MANUAL and AUTOMATION tasks require an origin_ref_id'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_tasks_traceable_origin_update
    BEFORE UPDATE OF origin_type, origin_ref_id ON tasks
    FOR EACH ROW
    WHEN NEW.origin_type IN ('MANUAL', 'AUTOMATION')
      AND trim(COALESCE(NEW.origin_ref_id, '')) = ''
    BEGIN
        SELECT RAISE(
            ABORT,
            'MANUAL and AUTOMATION tasks require an origin_ref_id'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_tasks_origin_immutable
    BEFORE UPDATE OF origin_type, origin_ref_id ON tasks
    FOR EACH ROW
    WHEN NEW.origin_type IS NOT OLD.origin_type
      OR NEW.origin_ref_id IS NOT OLD.origin_ref_id
    BEGIN
        SELECT RAISE(
            ABORT,
            'task origin identity is immutable after creation'
        );
    END
    """,
]

for _append_only_table in V14_APPEND_ONLY_TABLES:
    SCHEMA_V14_SQL.extend(
        (
            f"""
            CREATE TRIGGER IF NOT EXISTS
                trg_{_append_only_table}_append_only_update
            BEFORE UPDATE ON {_append_only_table}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(
                    ABORT,
                    '{_append_only_table} is append-only'
                );
            END
            """,
            f"""
            CREATE TRIGGER IF NOT EXISTS
                trg_{_append_only_table}_append_only_delete
            BEFORE DELETE ON {_append_only_table}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(
                    ABORT,
                    '{_append_only_table} is append-only'
                );
            END
            """,
        )
    )


CREATE_OPERATIONAL_INCIDENTS_V15_TABLE_SQL = """
CREATE TABLE operational_incidents_v15_new (
    incident_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN (
        'PLATFORM_LOGIN', 'PLATFORM_NETWORK', 'PAGE_STRUCTURE',
        'SCAN_INCOMPLETE', 'WORKER_UNAVAILABLE', 'QUEUE_BACKLOG',
        'PRODUCT_MAPPING', 'PRICE_ANOMALY', 'INVENTORY_ANOMALY',
        'ORDER_PAGE_UNAVAILABLE', 'ORDER_DATA_INCONSISTENT',
        'SALES_ESTIMATE_LOW_CONFIDENCE', 'NOTIFICATION_FAILURE',
        'WRITE_UNKNOWN', 'AUTOMATION_SERVICE', 'RUNTIME_STORAGE',
        'QUEUE_IMPORT', 'TRADE_DAY_TIME', 'LISTING_STATE',
        'MASTER_DATA', 'SETTLEMENT_PROCESSING', 'REVIEW_CHANNEL'
    )),
    source_type TEXT NOT NULL,
    source_ref_id TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL CHECK (severity IN (
        'S0', 'S1', 'S2', 'S3', 'S4'
    )),
    incident_status TEXT NOT NULL CHECK (incident_status IN (
        'OPEN', 'RETRYING', 'WAITING_HUMAN', 'ACKNOWLEDGED',
        'AUTO_PROTECTING', 'RESOLVED', 'CLOSED'
    )),
    blocks_finalization INTEGER NOT NULL DEFAULT 0 CHECK (
        blocks_finalization IN (0, 1)
    ),
    platform_name TEXT,
    platform_trade_date TEXT,
    seller_operation_date TEXT,
    subject_type TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    first_detected_at TEXT NOT NULL,
    last_detected_at TEXT NOT NULL,
    resolved_at TEXT,
    occurrence_count INTEGER NOT NULL DEFAULT 1 CHECK (occurrence_count >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (
            incident_status IN ('RESOLVED', 'CLOSED')
            AND resolved_at IS NOT NULL
        )
        OR (
            incident_status NOT IN ('RESOLVED', 'CLOSED')
            AND resolved_at IS NULL
        )
    )
)
"""


SCHEMA_V15_SQL = [
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_operational_incidents_open_dedupe
    ON operational_incidents(dedupe_key)
    WHERE resolved_at IS NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_operational_incidents_status
    ON operational_incidents(incident_status, severity, last_detected_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS operational_incident_events (
        event_id TEXT PRIMARY KEY,
        event_key TEXT NOT NULL CHECK (event_key <> ''),
        incident_id TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (event_type IN (
            'DETECTED', 'REDETECTED', 'STATUS_CHANGED',
            'SEVERITY_CHANGED', 'ACK', 'RECOVERY_RECORDED',
            'REVIEW_RECORDED', 'TASK_RECORDED'
        )),
        occurred_at TEXT NOT NULL,
        source_type TEXT NOT NULL CHECK (source_type <> ''),
        source_ref_id TEXT NOT NULL DEFAULT '',
        from_status TEXT CHECK (
            from_status IS NULL OR from_status IN (
                'OPEN', 'RETRYING', 'WAITING_HUMAN', 'ACKNOWLEDGED',
                'AUTO_PROTECTING', 'RESOLVED', 'CLOSED'
            )
        ),
        to_status TEXT CHECK (
            to_status IS NULL OR to_status IN (
                'OPEN', 'RETRYING', 'WAITING_HUMAN', 'ACKNOWLEDGED',
                'AUTO_PROTECTING', 'RESOLVED', 'CLOSED'
            )
        ),
        severity TEXT NOT NULL CHECK (severity IN (
            'S0', 'S1', 'S2', 'S3', 'S4'
        )),
        event_payload_json TEXT NOT NULL DEFAULT '{}'
            CHECK (json_valid(event_payload_json)),
        created_at TEXT NOT NULL,
        FOREIGN KEY(incident_id)
            REFERENCES operational_incidents(incident_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_operational_incident_events_key
    ON operational_incident_events(event_key)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_operational_incident_events_incident
    ON operational_incident_events(incident_id, occurred_at, event_id)
    """,
]

for _append_only_table in V15_APPEND_ONLY_TABLES:
    SCHEMA_V15_SQL.extend(
        (
            f"""
            CREATE TRIGGER IF NOT EXISTS
                trg_{_append_only_table}_append_only_update
            BEFORE UPDATE ON {_append_only_table}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(
                    ABORT,
                    '{_append_only_table} is append-only'
                );
            END
            """,
            f"""
            CREATE TRIGGER IF NOT EXISTS
                trg_{_append_only_table}_append_only_delete
            BEFORE DELETE ON {_append_only_table}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(
                    ABORT,
                    '{_append_only_table} is append-only'
                );
            END
            """,
        )
    )


SCHEMA_V16_SQL = [
    """
    CREATE TABLE IF NOT EXISTS emergency_offline_policies (
        policy_version TEXT PRIMARY KEY CHECK (trim(policy_version) <> ''),
        platform_name TEXT NOT NULL CHECK (trim(platform_name) <> ''),
        emergency_ratio TEXT NOT NULL DEFAULT '0.80' CHECK (
            emergency_ratio = '0.80'
        ),
        approved_by TEXT,
        approved_at TEXT,
        created_at TEXT NOT NULL,
        retired_at TEXT,
        CHECK (
            (approved_by IS NULL AND approved_at IS NULL)
            OR (
                approved_by IS NOT NULL
                AND trim(approved_by) <> ''
                AND approved_at IS NOT NULL
            )
        ),
        CHECK (retired_at IS NULL OR approved_at IS NOT NULL)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_emergency_offline_policies_active
    ON emergency_offline_policies(platform_name)
    WHERE approved_at IS NOT NULL AND retired_at IS NULL
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_emergency_offline_policies_lifecycle_update
    BEFORE UPDATE ON emergency_offline_policies
    FOR EACH ROW
    WHEN NOT (
        NEW.policy_version IS OLD.policy_version
        AND NEW.platform_name IS OLD.platform_name
        AND NEW.emergency_ratio IS OLD.emergency_ratio
        AND NEW.created_at IS OLD.created_at
        AND (
            (
                OLD.approved_at IS NULL
                AND OLD.approved_by IS NULL
                AND OLD.retired_at IS NULL
                AND NEW.approved_at IS NOT NULL
                AND NEW.approved_by IS NOT NULL
                AND trim(NEW.approved_by) <> ''
                AND NEW.retired_at IS NULL
            )
            OR (
                OLD.approved_at IS NOT NULL
                AND OLD.approved_by IS NOT NULL
                AND OLD.retired_at IS NULL
                AND NEW.approved_at IS OLD.approved_at
                AND NEW.approved_by IS OLD.approved_by
                AND NEW.retired_at IS NOT NULL
            )
        )
    )
    BEGIN
        SELECT RAISE(
            ABORT,
            'emergency policy versions are immutable outside approval and retirement'
        );
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_emergency_offline_policies_no_delete
    BEFORE DELETE ON emergency_offline_policies
    FOR EACH ROW
    BEGIN
        SELECT RAISE(ABORT, 'emergency policy versions cannot be deleted');
    END
    """,
]


SCHEMA_V17_SQL = [
    """
    CREATE TABLE IF NOT EXISTS inventory_authority_state (
        authority_key TEXT PRIMARY KEY CHECK (
            authority_key = 'REAL_INVENTORY'
        ),
        authority_mode TEXT NOT NULL CHECK (
            authority_mode IN ('PRE_CUTOVER', 'DB_AUTHORITY')
        ),
        bootstrap_snapshot_sha256 TEXT,
        bootstrap_runtime_snapshot_sha256 TEXT,
        bootstrap_sales_watermark_date TEXT,
        bootstrap_idempotency_key TEXT,
        bootstrap_completed_at TEXT,
        bootstrap_completed_by TEXT,
        version INTEGER NOT NULL CHECK (version >= 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (
            (
                authority_mode = 'PRE_CUTOVER'
                AND bootstrap_snapshot_sha256 IS NULL
                AND bootstrap_runtime_snapshot_sha256 IS NULL
                AND bootstrap_sales_watermark_date IS NULL
                AND bootstrap_idempotency_key IS NULL
                AND bootstrap_completed_at IS NULL
                AND bootstrap_completed_by IS NULL
            )
            OR (
                authority_mode = 'DB_AUTHORITY'
                AND bootstrap_snapshot_sha256 IS NOT NULL
                AND bootstrap_runtime_snapshot_sha256 IS NOT NULL
                AND bootstrap_sales_watermark_date IS NOT NULL
                AND bootstrap_idempotency_key IS NOT NULL
                AND bootstrap_completed_at IS NOT NULL
                AND bootstrap_completed_by IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_balances (
        internal_sku TEXT PRIMARY KEY CHECK (trim(internal_sku) <> ''),
        current_qty INTEGER NOT NULL CHECK (current_qty >= 0),
        version INTEGER NOT NULL CHECK (version >= 1),
        last_transaction_id TEXT NOT NULL CHECK (
            trim(last_transaction_id) <> ''
        ),
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_transactions (
        transaction_id TEXT PRIMARY KEY CHECK (trim(transaction_id) <> ''),
        internal_sku TEXT NOT NULL CHECK (trim(internal_sku) <> ''),
        inventory_before INTEGER NOT NULL CHECK (inventory_before >= 0),
        inventory_delta INTEGER NOT NULL,
        inventory_after INTEGER NOT NULL CHECK (inventory_after >= 0),
        transaction_type TEXT NOT NULL CHECK (transaction_type IN (
            'BOOTSTRAP', 'SKU_INITIALIZATION',
            'MANUAL_INBOUND', 'MANUAL_ADJUSTMENT',
            'SALES_DEDUCTION', 'SALES_RESTORE',
            'SALES_BASELINE_SYNC', 'RECONCILIATION'
        )),
        source_type TEXT NOT NULL CHECK (trim(source_type) <> ''),
        source_ref_id TEXT NOT NULL CHECK (trim(source_ref_id) <> ''),
        reason TEXT NOT NULL CHECK (trim(reason) <> ''),
        actor TEXT NOT NULL CHECK (trim(actor) <> ''),
        seller_operation_date TEXT,
        platform_name TEXT,
        platform_trade_date TEXT,
        supporting_refs_json TEXT NOT NULL DEFAULT '[]' CHECK (
            json_valid(supporting_refs_json)
        ),
        idempotency_key TEXT NOT NULL UNIQUE CHECK (
            trim(idempotency_key) <> ''
        ),
        request_sha256 TEXT NOT NULL CHECK (
            request_sha256 GLOB 'sha256:*'
        ),
        balance_version_after INTEGER NOT NULL CHECK (
            balance_version_after >= 1
        ),
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        CHECK (inventory_after = inventory_before + inventory_delta)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_inventory_transactions_sku_recorded
    ON inventory_transactions(internal_sku, recorded_at, transaction_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_inventory_transactions_trade_date
    ON inventory_transactions(platform_name, platform_trade_date, internal_sku)
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_sales_baselines (
        platform_name TEXT NOT NULL CHECK (trim(platform_name) <> ''),
        platform_trade_date TEXT NOT NULL,
        internal_sku TEXT NOT NULL CHECK (trim(internal_sku) <> ''),
        selected_fact_source TEXT NOT NULL CHECK (
            selected_fact_source IN ('ORDER_OBSERVED', 'SCAN_ESTIMATED')
        ),
        quality_level TEXT NOT NULL CHECK (quality_level IN (
            'ORDER_COMPLETE', 'SCAN_ESTIMATED_HIGH'
        )),
        selected_sold_qty INTEGER NOT NULL CHECK (selected_sold_qty >= 0),
        source_ref_id TEXT NOT NULL CHECK (trim(source_ref_id) <> ''),
        source_sha256 TEXT NOT NULL CHECK (source_sha256 GLOB 'sha256:*'),
        mapping_version TEXT NOT NULL,
        supporting_refs_json TEXT NOT NULL DEFAULT '[]' CHECK (
            json_valid(supporting_refs_json)
        ),
        inventory_transaction_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version >= 1),
        updated_at TEXT NOT NULL,
        PRIMARY KEY(platform_name, platform_trade_date, internal_sku),
        FOREIGN KEY(inventory_transaction_id)
            REFERENCES inventory_transactions(transaction_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS inventory_alert_policies (
        policy_key TEXT PRIMARY KEY CHECK (trim(policy_key) <> ''),
        scope_type TEXT NOT NULL CHECK (scope_type IN ('DEFAULT', 'SKU')),
        scope_key TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        threshold_qty INTEGER NOT NULL CHECK (
            threshold_qty BETWEEN 0 AND 9999
        ),
        repeat_interval_minutes INTEGER NOT NULL CHECK (
            repeat_interval_minutes BETWEEN 30 AND 1440
        ),
        version INTEGER NOT NULL CHECK (version >= 1),
        updated_by TEXT NOT NULL CHECK (trim(updated_by) <> ''),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (
            (scope_type = 'DEFAULT' AND scope_key = '*')
            OR (scope_type = 'SKU' AND trim(scope_key) <> '' AND scope_key <> '*')
        )
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_inventory_alert_policies_scope
    ON inventory_alert_policies(scope_type, scope_key)
    """,
]

for _append_only_table in V17_APPEND_ONLY_TABLES:
    SCHEMA_V17_SQL.extend(
        (
            f"""
            CREATE TRIGGER IF NOT EXISTS
                trg_{_append_only_table}_append_only_update
            BEFORE UPDATE ON {_append_only_table}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, '{_append_only_table} is append-only');
            END
            """,
            f"""
            CREATE TRIGGER IF NOT EXISTS
                trg_{_append_only_table}_append_only_delete
            BEFORE DELETE ON {_append_only_table}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, '{_append_only_table} is append-only');
            END
            """,
        )
    )


def _backfill_shadowbot_batch_registry(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO shadowbot_batch_registry(
            batch_id, batch_type, contract_version, platform_name, created_at
        )
        SELECT batch_id, 'update_price', 4, platform_name, created_at
        FROM shadowbot_commit_batches
        """
    )


def _migrate_shadowbot_operations_to_v13(
    connection: sqlite3.Connection,
) -> None:
    columns = {
        str(row[1]): row
        for row in connection.execute(
            "PRAGMA table_info(shadowbot_operations)"
        ).fetchall()
    }
    v13_columns = {
        "action_type",
        "expected_old_status",
        "target_status",
        "target_inventory",
        "approved_payload_json",
        "operation_result",
        "resolution_status",
        "resolved_by",
        "resolved_at",
        "superseded_by_operation_id",
    }
    table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'shadowbot_operations'"
    ).fetchone()
    table_sql = str(table_row[0] or "").upper() if table_row else ""
    if (
        v13_columns.issubset(columns)
        and int(columns["expected_old_price"][3]) == 0
        and int(columns["target_price"][3]) == 0
        and "'NOT_ATTEMPTED'" in table_sql
    ):
        return

    connection.execute("DROP TABLE IF EXISTS shadowbot_operations_v13_new")
    connection.execute(
        """
        CREATE TABLE shadowbot_operations_v13_new (
            operation_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            product_identity_json TEXT NOT NULL DEFAULT '{}',
            action_type TEXT NOT NULL DEFAULT 'update_price' CHECK (
                action_type IN ('update_price', 'set_online', 'set_offline')
            ),
            expected_old_price TEXT,
            target_price TEXT,
            expected_old_status TEXT,
            target_status TEXT,
            target_inventory INTEGER CHECK (
                target_inventory IS NULL OR target_inventory >= 0
            ),
            status TEXT NOT NULL,
            operation_result TEXT NOT NULL DEFAULT '' CHECK (
                operation_result IN (
                    '', 'NOT_ATTEMPTED', 'VERIFIED', 'NOT_APPLIED', 'PARTIALLY_APPLIED',
                    'NEEDS_RECONCILIATION'
                )
            ),
            resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED' CHECK (
                resolution_status IN (
                    'UNRESOLVED', 'MANUAL_HANDLED',
                    'CORRECTIVE_ACTION_AUTHORIZED'
                )
            ),
            resolved_by TEXT NOT NULL DEFAULT '',
            resolved_at TEXT,
            superseded_by_operation_id TEXT,
            lock_owner TEXT NOT NULL DEFAULT '',
            approved_payload_hash TEXT NOT NULL DEFAULT '',
            approved_payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (
                    action_type = 'update_price'
                    AND expected_old_price IS NOT NULL
                    AND target_price IS NOT NULL
                    AND expected_old_status IS NULL
                    AND target_status IS NULL
                    AND target_inventory IS NULL
                )
                OR (
                    action_type = 'set_online'
                    AND expected_old_status = 'offline'
                    AND target_status = 'online'
                    AND target_price IS NOT NULL
                    AND target_inventory IS NOT NULL
                )
                OR (
                    action_type = 'set_offline'
                    AND expected_old_price IS NULL
                    AND target_price IS NULL
                    AND expected_old_status = 'online'
                    AND target_status = 'offline'
                    AND target_inventory IS NULL
                )
            ),
            FOREIGN KEY(task_id) REFERENCES tasks(task_id),
            FOREIGN KEY(superseded_by_operation_id)
                REFERENCES shadowbot_operations_v13_new(operation_id)
        )
        """
    )
    column_names = set(columns)
    expressions = {
        "action_type": (
            "COALESCE(NULLIF(action_type, ''), 'update_price')"
            if "action_type" in column_names
            else "'update_price'"
        ),
        "expected_old_status": (
            "expected_old_status" if "expected_old_status" in column_names else "NULL"
        ),
        "target_status": (
            "target_status" if "target_status" in column_names else "NULL"
        ),
        "target_inventory": (
            "target_inventory" if "target_inventory" in column_names else "NULL"
        ),
        "operation_result": (
            "COALESCE(operation_result, '')"
            if "operation_result" in column_names
            else "''"
        ),
        "resolution_status": (
            "COALESCE(NULLIF(resolution_status, ''), 'UNRESOLVED')"
            if "resolution_status" in column_names
            else "'UNRESOLVED'"
        ),
        "resolved_by": (
            "COALESCE(resolved_by, '')" if "resolved_by" in column_names else "''"
        ),
        "resolved_at": "resolved_at" if "resolved_at" in column_names else "NULL",
        "superseded_by_operation_id": (
            "superseded_by_operation_id"
            if "superseded_by_operation_id" in column_names
            else "NULL"
        ),
        "approved_payload_json": (
            "COALESCE(NULLIF(approved_payload_json, ''), '{}')"
            if "approved_payload_json" in column_names
            else "'{}'"
        ),
    }
    connection.execute(
        f"""
        INSERT INTO shadowbot_operations_v13_new(
            operation_id, task_id, platform, product_identity_json,
            action_type, expected_old_price, target_price,
            expected_old_status, target_status, target_inventory,
            status, operation_result, resolution_status, resolved_by,
            resolved_at, superseded_by_operation_id, lock_owner,
            approved_payload_hash, approved_payload_json, created_at, updated_at
        )
        SELECT operation_id, task_id, platform, product_identity_json,
               {expressions["action_type"]}, expected_old_price, target_price,
               {expressions["expected_old_status"]},
               {expressions["target_status"]},
               {expressions["target_inventory"]},
               status, {expressions["operation_result"]},
               {expressions["resolution_status"]},
               {expressions["resolved_by"]},
               {expressions["resolved_at"]},
               {expressions["superseded_by_operation_id"]},
               lock_owner, approved_payload_hash,
               {expressions["approved_payload_json"]},
               created_at, updated_at
        FROM shadowbot_operations
        """
    )
    connection.execute("DROP TABLE shadowbot_operations")
    connection.execute(
        "ALTER TABLE shadowbot_operations_v13_new RENAME TO shadowbot_operations"
    )


def _migrate_listing_action_batch_items_to_v13(
    connection: sqlite3.Connection,
) -> None:
    """Allow the explicit NOT_ATTEMPTED publish-boundary outcome on old v13 DBs."""

    table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'shadowbot_listing_action_batch_items'"
    ).fetchone()
    table_sql = str(table_row[0] or "").upper() if table_row else ""
    if not table_row or "'NOT_ATTEMPTED'" in table_sql:
        return

    connection.execute(
        "ALTER TABLE shadowbot_listing_action_batch_items "
        "RENAME TO shadowbot_listing_action_batch_items_v13_old"
    )
    connection.execute(
        """
        CREATE TABLE shadowbot_listing_action_batch_items (
            item_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            source_task_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            item_execution_attempt_id TEXT NOT NULL,
            internal_sku TEXT NOT NULL,
            expected_product_name TEXT NOT NULL,
            expected_grade TEXT NOT NULL,
            item_payload_sha256 TEXT NOT NULL,
            write_identity_key TEXT NOT NULL,
            page_identity_key TEXT NOT NULL,
            expected_old_status TEXT NOT NULL,
            target_status TEXT NOT NULL,
            target_price TEXT,
            target_inventory INTEGER CHECK (
                target_inventory IS NULL OR target_inventory >= 0
            ),
            detail_effect_state TEXT NOT NULL DEFAULT 'NOT_STARTED' CHECK (
                detail_effect_state IN (
                    'NOT_STARTED', 'NOT_APPLIED', 'VERIFIED', 'PARTIAL', 'UNKNOWN'
                )
            ),
            listing_effect_state TEXT NOT NULL DEFAULT 'NOT_STARTED' CHECK (
                listing_effect_state IN (
                    'NOT_STARTED', 'NOT_APPLIED', 'VERIFIED', 'PARTIAL', 'UNKNOWN'
                )
            ),
            observed_price_before_action TEXT,
            observed_inventory_before_action INTEGER CHECK (
                observed_inventory_before_action IS NULL
                OR observed_inventory_before_action >= 0
            ),
            observed_price_after_detail_save TEXT,
            observed_inventory_after_detail_save INTEGER CHECK (
                observed_inventory_after_detail_save IS NULL
                OR observed_inventory_after_detail_save >= 0
            ),
            detail_save_clicked_at TEXT,
            action_clicked_at TEXT,
            readback_observed_at TEXT,
            operation_result TEXT NOT NULL DEFAULT '' CHECK (
                operation_result IN (
                    '', 'NOT_ATTEMPTED', 'VERIFIED', 'NOT_APPLIED',
                    'PARTIALLY_APPLIED', 'NEEDS_RECONCILIATION'
                )
            ),
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            FOREIGN KEY(batch_id)
                REFERENCES shadowbot_listing_action_batches(batch_id),
            FOREIGN KEY(source_task_id) REFERENCES tasks(task_id),
            FOREIGN KEY(operation_id) REFERENCES shadowbot_operations(operation_id),
            FOREIGN KEY(item_execution_attempt_id)
                REFERENCES shadowbot_execution_attempts(execution_attempt_id)
        )
        """
    )
    columns = [
        "item_id",
        "batch_id",
        "source_task_id",
        "operation_id",
        "item_execution_attempt_id",
        "internal_sku",
        "expected_product_name",
        "expected_grade",
        "item_payload_sha256",
        "write_identity_key",
        "page_identity_key",
        "expected_old_status",
        "target_status",
        "target_price",
        "target_inventory",
        "detail_effect_state",
        "listing_effect_state",
        "observed_price_before_action",
        "observed_inventory_before_action",
        "observed_price_after_detail_save",
        "observed_inventory_after_detail_save",
        "detail_save_clicked_at",
        "action_clicked_at",
        "readback_observed_at",
        "operation_result",
        "error_code",
        "error_message",
        "updated_at",
    ]
    names = ", ".join(columns)
    connection.execute(
        f"INSERT INTO shadowbot_listing_action_batch_items({names}) "
        f"SELECT {names} FROM shadowbot_listing_action_batch_items_v13_old"
    )
    connection.execute("DROP TABLE shadowbot_listing_action_batch_items_v13_old")


def _migrate_shadowbot_write_locks_to_v13(
    connection: sqlite3.Connection,
) -> None:
    table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'shadowbot_write_locks'"
    ).fetchone()
    table_sql = str(table_row[0] or "") if table_row else ""
    foreign_keys = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute(
            "PRAGMA foreign_key_list(shadowbot_write_locks)"
        ).fetchall()
    }
    if (
        "REVIEW_BLOCKED" in table_sql
        and (
            "batch_id",
            "shadowbot_batch_registry",
            "batch_id",
        )
        in foreign_keys
    ):
        return

    connection.execute("DROP TABLE IF EXISTS shadowbot_write_locks_v13_new")
    connection.execute(
        """
        CREATE TABLE shadowbot_write_locks_v13_new (
            write_identity_key TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL UNIQUE,
            item_execution_attempt_id TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'ACTIVE', 'UNKNOWN', 'REVIEW_BLOCKED', 'RELEASED'
            )),
            acquired_at TEXT NOT NULL,
            released_at TEXT,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(operation_id)
                REFERENCES shadowbot_operations(operation_id),
            FOREIGN KEY(item_execution_attempt_id)
                REFERENCES shadowbot_execution_attempts(execution_attempt_id),
            FOREIGN KEY(batch_id)
                REFERENCES shadowbot_batch_registry(batch_id)
        )
        """
    )
    if table_row is not None:
        connection.execute(
            """
            INSERT INTO shadowbot_write_locks_v13_new(
                write_identity_key, operation_id, item_execution_attempt_id,
                batch_id, status, acquired_at, released_at, updated_at
            )
            SELECT write_identity_key, operation_id, item_execution_attempt_id,
                   batch_id, status, acquired_at, released_at, updated_at
            FROM shadowbot_write_locks
            """
        )
        connection.execute("DROP TABLE shadowbot_write_locks")
    connection.execute(
        "ALTER TABLE shadowbot_write_locks_v13_new RENAME TO shadowbot_write_locks"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "ux_shadowbot_write_locks_operation_id "
        "ON shadowbot_write_locks(operation_id)"
    )


def _ensure_listing_status_v13_columns(
    connection: sqlite3.Connection,
) -> None:
    for column, declaration in (
        ("price_source", "TEXT NOT NULL DEFAULT 'default'"),
        ("price_observed_at", "TEXT"),
        ("price_source_attempt_id", "TEXT NOT NULL DEFAULT ''"),
        ("last_listing_change_at", "TEXT"),
        ("last_listing_operation_id", "TEXT"),
        ("online_status_observed_at", "TEXT"),
        ("online_status_source_type", "TEXT NOT NULL DEFAULT ''"),
        ("online_status_source_id", "TEXT NOT NULL DEFAULT ''"),
    ):
        _ensure_column(connection, "listing_status", column, declaration)


def _backfill_listing_status_latest_scan_observations(
    connection: sqlite3.Connection,
) -> None:
    """Project the newest complete v5 page observation after adding columns."""

    rows = connection.execute(
        """
        SELECT snapshot.platform_name, snapshot.execution_attempt_id,
               snapshot.scan_completed_at,
               item.internal_sku, item.listing_location,
               item.online_observed_price, item.waiting_observed_price,
               item.online_observed_inventory, item.waiting_observed_inventory,
               item.online_observed_at, item.waiting_observed_at
        FROM listing_sync_snapshot_items AS item
        JOIN listing_sync_snapshots AS snapshot
          ON snapshot.snapshot_id = item.snapshot_id
        WHERE snapshot.snapshot_complete = 1
          AND snapshot.status = 'VERIFIED'
          AND item.internal_sku IS NOT NULL
          AND item.internal_sku <> ''
          AND item.listing_location IN ('online_only', 'waiting_only', 'both')
          AND NOT EXISTS (
              SELECT 1
              FROM listing_sync_snapshot_items AS newer_item
              JOIN listing_sync_snapshots AS newer_snapshot
                ON newer_snapshot.snapshot_id = newer_item.snapshot_id
              WHERE newer_item.internal_sku = item.internal_sku
                AND newer_snapshot.platform_name = snapshot.platform_name
                AND newer_snapshot.snapshot_complete = 1
                AND newer_snapshot.status = 'VERIFIED'
                AND newer_snapshot.scan_completed_at
                    > snapshot.scan_completed_at
          )
        """
    ).fetchall()
    for row in rows:
        online_preferred = str(row["listing_location"]) in {
            "online_only",
            "both",
        }
        observed_price = row[
            "online_observed_price" if online_preferred else "waiting_observed_price"
        ]
        observed_inventory = row[
            "online_observed_inventory"
            if online_preferred
            else "waiting_observed_inventory"
        ]
        observed_at = row[
            "online_observed_at" if online_preferred else "waiting_observed_at"
        ]
        if observed_price is None or observed_inventory is None or observed_at is None:
            continue
        connection.execute(
            """
            UPDATE listing_status
            SET current_price = ?, platform_stock_qty = ?,
                source = 'shadowbot_sync_status',
                updated_at = ?,
                inventory_source = 'shadowbot_sync_status',
                inventory_observed_at = ?,
                inventory_source_attempt_id = ?,
                price_source = 'shadowbot_sync_status',
                price_observed_at = ?,
                price_source_attempt_id = ?
            WHERE platform_name = ?
              AND internal_sku = ?
              AND (
                  last_listing_change_at IS NULL
                  OR last_listing_change_at <= ?
              )
              AND (
                  price_observed_at IS NULL
                  OR price_observed_at <= ?
              )
              AND (
                  inventory_observed_at IS NULL
                  OR inventory_observed_at <= ?
              )
            """,
            (
                str(observed_price),
                int(observed_inventory),
                str(row["scan_completed_at"]),
                str(observed_at),
                str(row["execution_attempt_id"]),
                str(observed_at),
                str(row["execution_attempt_id"]),
                str(row["platform_name"]),
                str(row["internal_sku"]),
                str(observed_at),
                str(observed_at),
                str(observed_at),
            ),
        )


def _requires_runtime_schema_v13_migration(
    connection: sqlite3.Connection,
) -> bool:
    required_tables = {
        "shadowbot_batch_registry",
        "shadowbot_listing_action_batches",
        "shadowbot_listing_action_batch_items",
        "shadowbot_listing_result_receipts",
        "listing_sync_snapshots",
        "listing_sync_snapshot_items",
        "listing_anomaly_cases",
    }
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "runtime_schema_migrations" not in tables:
        return True
    version_row = connection.execute(
        "SELECT 1 FROM runtime_schema_migrations WHERE schema_version = 13"
    ).fetchone()
    if version_row is None or not required_tables.issubset(tables):
        return True
    operation_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(shadowbot_operations)"
        ).fetchall()
    }
    if "action_type" not in operation_columns:
        return True
    operation_table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'shadowbot_operations'"
    ).fetchone()
    operation_table_sql = (
        str(operation_table_row[0] or "").upper()
        if operation_table_row is not None
        else ""
    )
    if "'NOT_ATTEMPTED'" not in operation_table_sql:
        return True
    action_table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'shadowbot_listing_action_batch_items'"
    ).fetchone()
    action_table_sql = (
        str(action_table_row[0] or "").upper() if action_table_row is not None else ""
    )
    if "'NOT_ATTEMPTED'" not in action_table_sql:
        return True
    lock_foreign_keys = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute(
            "PRAGMA foreign_key_list(shadowbot_write_locks)"
        ).fetchall()
    }
    return (
        "batch_id",
        "shadowbot_batch_registry",
        "batch_id",
    ) not in lock_foreign_keys


def _requires_runtime_schema_v14_migration(
    connection: sqlite3.Connection,
) -> bool:
    required_tables = {
        "operational_time_policies",
        "automation_jobs",
        "automation_runs",
        "automation_run_events",
        "automation_run_links",
        "product_observation_batches",
        "product_observation_items",
        "order_observation_batches",
        "order_observation_items",
        "sales_estimate_segments",
        "platform_trade_day_summaries",
        "platform_trade_day_summary_events",
        "platform_trade_day_summary_inputs",
        "operational_incidents",
        "incident_notification_state",
    }
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "runtime_schema_migrations" not in tables:
        return True
    version_row = connection.execute(
        "SELECT 1 FROM runtime_schema_migrations WHERE schema_version = 14"
    ).fetchone()
    if version_row is None or not required_tables.issubset(tables):
        return True
    task_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if not {
        "origin_type",
        "origin_ref_id",
        "approval_policy",
        "policy_version",
        "platform_trade_date",
        "seller_operation_date",
        "seller_phase",
        "time_policy_version",
    }.issubset(task_columns):
        return True
    order_batch_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(order_observation_batches)"
        ).fetchall()
    }
    if "trade_day_status" not in order_batch_columns:
        return True
    order_item_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(order_observation_items)"
        ).fetchall()
    }
    required_order_item_columns = {
        "platform_name",
        "platform_trade_date",
        "trade_day_status",
        "order_identity_fingerprint",
        "occurrence_no",
        "order_created_at",
        "platform_product_name",
        "grade",
        "internal_sku",
        "mapping_status",
        "mapping_version",
        "order_qty",
        "order_transaction_amount",
        "observed_at",
        "seller_operation_date",
        "seller_phase",
        "raw_observation_sha256",
    }
    retired_order_item_columns = {
        "ordered_qty",
        "effective_qty",
        "cancelled_qty",
        "cancellation_derivation_method",
        "seller_received_amount",
        "purchase_sequence",
        "source_row_fingerprint",
    }
    if not required_order_item_columns.issubset(
        order_item_columns
    ) or retired_order_item_columns.intersection(order_item_columns):
        return True
    summary_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(platform_trade_day_summaries)"
        ).fetchall()
    }
    if (
        "transaction_amount_total" not in summary_columns
        or "seller_received_amount" in summary_columns
    ):
        return True
    summary_input_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(platform_trade_day_summary_inputs)"
        ).fetchall()
    }
    return "input_manifest_sha256" not in summary_input_columns


def _requires_runtime_schema_v15_migration(
    connection: sqlite3.Connection,
) -> bool:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "runtime_schema_migrations" not in tables:
        return True
    version_row = connection.execute(
        "SELECT 1 FROM runtime_schema_migrations WHERE schema_version = 15"
    ).fetchone()
    if version_row is None:
        return True
    if not {
        "operational_incidents",
        "operational_incident_events",
    }.issubset(tables):
        return True
    incident_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(operational_incidents)"
        ).fetchall()
    }
    if "occurrence_count" not in incident_columns:
        return True
    incident_table_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'operational_incidents'"
    ).fetchone()
    incident_table_sql = (
        str(incident_table_row[0] or "").upper()
        if incident_table_row is not None
        else ""
    )
    return any(
        value not in incident_table_sql
        for value in (
            "'AUTOMATION_SERVICE'",
            "'RUNTIME_STORAGE'",
            "'QUEUE_IMPORT'",
            "'TRADE_DAY_TIME'",
            "'LISTING_STATE'",
            "'MASTER_DATA'",
            "'SETTLEMENT_PROCESSING'",
            "'REVIEW_CHANNEL'",
        )
    )


def _requires_runtime_schema_v16_migration(
    connection: sqlite3.Connection,
) -> bool:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "runtime_schema_migrations" not in tables:
        return True
    version_row = connection.execute(
        "SELECT 1 FROM runtime_schema_migrations WHERE schema_version = 16"
    ).fetchone()
    return (
        version_row is None
        or "emergency_offline_policies" not in tables
    )


def _requires_runtime_schema_v17_migration(
    connection: sqlite3.Connection,
) -> bool:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "runtime_schema_migrations" not in tables:
        return True
    version_row = connection.execute(
        "SELECT 1 FROM runtime_schema_migrations WHERE schema_version = 17"
    ).fetchone()
    return version_row is None or any(
        table_name not in tables
        for table_name in (
            "inventory_authority_state",
            "inventory_balances",
            "inventory_transactions",
            "inventory_sales_baselines",
            "inventory_alert_policies",
        )
    )


def _migrate_operational_incidents_to_v15(
    connection: sqlite3.Connection,
) -> None:
    table_row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'operational_incidents'"
    ).fetchone()
    if table_row is None:
        return
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(operational_incidents)"
        ).fetchall()
    }
    table_sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' "
        "AND name = 'operational_incidents'"
    ).fetchone()
    table_sql = str(table_sql_row[0] or "").upper() if table_sql_row else ""
    expanded_categories = all(
        value in table_sql
        for value in (
            "'AUTOMATION_SERVICE'",
            "'RUNTIME_STORAGE'",
            "'QUEUE_IMPORT'",
            "'TRADE_DAY_TIME'",
            "'LISTING_STATE'",
            "'MASTER_DATA'",
            "'SETTLEMENT_PROCESSING'",
            "'REVIEW_CHANNEL'",
        )
    )
    if "occurrence_count" in columns and expanded_categories:
        return

    occurrence_expression = "occurrence_count" if "occurrence_count" in columns else "1"
    connection.execute("DROP TABLE IF EXISTS operational_incidents_v15_new")
    connection.execute(CREATE_OPERATIONAL_INCIDENTS_V15_TABLE_SQL)
    connection.execute(
        f"""
        INSERT INTO operational_incidents_v15_new(
            incident_id, dedupe_key, category,
            source_type, source_ref_id, severity, incident_status,
            blocks_finalization, platform_name, platform_trade_date,
            seller_operation_date, subject_type, subject_key,
            title, description, first_detected_at, last_detected_at,
            resolved_at, occurrence_count, created_at, updated_at
        )
        SELECT incident_id, dedupe_key, category,
               source_type, source_ref_id, severity, incident_status,
               blocks_finalization, platform_name, platform_trade_date,
               seller_operation_date, subject_type, subject_key,
               title, description, first_detected_at, last_detected_at,
               resolved_at, {occurrence_expression}, created_at, updated_at
        FROM operational_incidents
        """
    )
    connection.execute("DROP TABLE operational_incidents")
    connection.execute(
        "ALTER TABLE operational_incidents_v15_new RENAME TO operational_incidents"
    )


def _migrate_order_observation_v14_contract(
    connection: sqlite3.Connection,
) -> None:
    """Replace the unused provisional v14 order shape with the frozen one."""

    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    required_tables = {
        "order_observation_batches",
        "order_observation_items",
    }
    if not required_tables.issubset(tables):
        return
    batch_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(order_observation_batches)"
        ).fetchall()
    }
    item_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(order_observation_items)"
        ).fetchall()
    }
    already_frozen = (
        "trade_day_status" in batch_columns
        and {
            "platform_name",
            "trade_day_status",
            "order_identity_fingerprint",
            "order_qty",
            "order_transaction_amount",
        }.issubset(item_columns)
        and not {
            "ordered_qty",
            "effective_qty",
            "cancelled_qty",
            "cancellation_derivation_method",
            "seller_received_amount",
            "purchase_sequence",
            "source_row_fingerprint",
        }.intersection(item_columns)
    )
    if already_frozen:
        return
    batch_count = int(
        connection.execute("SELECT COUNT(*) FROM order_observation_batches").fetchone()[
            0
        ]
    )
    item_count = int(
        connection.execute("SELECT COUNT(*) FROM order_observation_items").fetchone()[0]
    )
    if batch_count or item_count:
        raise RuntimeError(
            "The provisional v14 order-observation tables contain rows; "
            "their old amount and quantity semantics cannot be guessed"
        )
    connection.execute("DROP TABLE order_observation_items")
    connection.execute("DROP TABLE order_observation_batches")


def _migrate_trade_day_summary_transaction_amount(
    connection: sqlite3.Connection,
) -> None:
    table_row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'platform_trade_day_summaries'
        """
    ).fetchone()
    if table_row is None:
        return
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(platform_trade_day_summaries)"
        ).fetchall()
    }
    if "seller_received_amount" in columns and "transaction_amount_total" in columns:
        raise RuntimeError(
            "Runtime v14 must not retain two equivalent transaction amount columns"
        )
    if "seller_received_amount" in columns:
        connection.execute(
            """
            ALTER TABLE platform_trade_day_summaries
            RENAME COLUMN seller_received_amount
            TO transaction_amount_total
            """
        )


def _migrate_trade_day_summary_inputs_manifest_dimension(
    connection: sqlite3.Connection,
) -> None:
    table_row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'platform_trade_day_summary_inputs'
        """
    ).fetchone()
    if table_row is None:
        return
    columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(platform_trade_day_summary_inputs)"
        ).fetchall()
    }
    if "input_manifest_sha256" in columns:
        return
    connection.execute(
        """
        ALTER TABLE platform_trade_day_summary_inputs
        RENAME TO platform_trade_day_summary_inputs_v14_legacy
        """
    )
    connection.execute("DROP INDEX IF EXISTS ix_trade_day_summary_inputs_ref")
    connection.execute(
        """
        CREATE TABLE platform_trade_day_summary_inputs (
            summary_id TEXT NOT NULL,
            input_manifest_sha256 TEXT NOT NULL,
            input_type TEXT NOT NULL,
            input_ref_id TEXT NOT NULL,
            input_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(
                summary_id, input_manifest_sha256,
                input_type, input_ref_id
            ),
            FOREIGN KEY(summary_id)
                REFERENCES platform_trade_day_summaries(summary_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO platform_trade_day_summary_inputs(
            summary_id, input_manifest_sha256,
            input_type, input_ref_id, input_sha256, created_at
        )
        SELECT legacy.summary_id,
               summary.input_manifest_sha256,
               legacy.input_type,
               legacy.input_ref_id,
               legacy.input_sha256,
               legacy.created_at
        FROM platform_trade_day_summary_inputs_v14_legacy AS legacy
        INNER JOIN platform_trade_day_summaries AS summary
            ON summary.summary_id = legacy.summary_id
        """
    )
    connection.execute("DROP TABLE platform_trade_day_summary_inputs_v14_legacy")


def _ensure_open_task_dedupe_index(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'index' AND name = 'ux_tasks_open_dedupe'
        """
    ).fetchone()
    index_sql = str(row[0] or "").lower() if row is not None else ""
    if "'failed'" in index_sql:
        return
    connection.execute("DROP INDEX IF EXISTS ux_tasks_open_dedupe")
    connection.execute(OPEN_TASK_DEDUPE_INDEX_SQL)


class SQLiteRuntimeRepository:
    def __init__(
        self,
        db_path: Path,
        *,
        connection_config: SQLiteConnectionConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        retry_sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.connection_factory = SQLiteConnectionFactory(
            self.db_path,
            config=connection_config,
        )
        self._clock = clock or utc_now
        self._retry_sleep = retry_sleep or time.sleep

    def connect(self) -> sqlite3.Connection:
        """Backward-compatible writable connection entrypoint."""

        return self.connect_write()

    def connect_read(self) -> sqlite3.Connection:
        """Open an existing runtime database without initialization side effects."""

        return self.connection_factory.connect_read()

    def connect_write(self) -> sqlite3.Connection:
        """Open a configured writable runtime connection."""

        return self.connection_factory.connect_write()

    def _run_sqlite_retry(
        self,
        operation: Callable[[], Any],
        *,
        operation_name: str = "SQLite runtime operation",
    ) -> Any:
        """Run a database-only operation under the configured bounded retry policy."""

        config = self.connection_factory.config
        return _execute_with_sqlite_retry(
            operation,
            max_attempts=config.retry_max_attempts,
            max_elapsed_ms=config.retry_max_elapsed_ms or 0,
            base_delay_ms=config.retry_base_delay_ms,
            operation_name=operation_name,
            sleep=self._retry_sleep,
        )

    def init_schema(self) -> None:
        def initialize_schema(connection: sqlite3.Connection) -> None:
            connection.commit()
            requires_v13_migration = _requires_runtime_schema_v13_migration(connection)
            requires_v14_migration = _requires_runtime_schema_v14_migration(connection)
            requires_v15_migration = _requires_runtime_schema_v15_migration(connection)
            requires_v16_migration = _requires_runtime_schema_v16_migration(connection)
            requires_v17_migration = _requires_runtime_schema_v17_migration(connection)
            requires_runtime_migration = (
                requires_v13_migration
                or requires_v14_migration
                or requires_v15_migration
                or requires_v16_migration
                or requires_v17_migration
            )
            if requires_v13_migration or requires_v15_migration:
                connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in SCHEMA_SQL:
                    connection.execute(statement)
                _ensure_open_task_dedupe_index(connection)
                _ensure_column(
                    connection,
                    "shadowbot_execution_attempts",
                    "instruction_hash",
                    "TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(
                    connection,
                    "shadowbot_execution_attempts",
                    "request_file_sha256",
                    "TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(
                    connection,
                    "shadowbot_execution_attempts",
                    "queue_request_path",
                    "TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(connection, "tasks", "expected_old_price", "TEXT")
                _ensure_column(
                    connection,
                    "tasks",
                    "target_inventory",
                    "INTEGER CHECK (target_inventory IS NULL OR target_inventory >= 0)",
                )
                _ensure_column(
                    connection,
                    "tasks",
                    "origin_type",
                    "TEXT NOT NULL DEFAULT 'LEGACY' CHECK (origin_type IN "
                    "('MANUAL', 'AUTOMATION', 'SYSTEM_EMERGENCY', 'LEGACY'))",
                )
                _ensure_column(connection, "tasks", "origin_ref_id", "TEXT")
                _ensure_column(
                    connection,
                    "tasks",
                    "approval_policy",
                    "TEXT NOT NULL DEFAULT 'UNSPECIFIED'",
                )
                _ensure_column(connection, "tasks", "policy_version", "TEXT")
                _ensure_column(
                    connection,
                    "tasks",
                    "platform_trade_date",
                    "TEXT",
                )
                _ensure_column(
                    connection,
                    "tasks",
                    "seller_operation_date",
                    "TEXT",
                )
                _ensure_column(
                    connection,
                    "tasks",
                    "seller_phase",
                    "TEXT CHECK (seller_phase IS NULL OR seller_phase IN "
                    "('NORMAL_SALES', 'PEAK_SALES', 'DELIVERY_OVERLAP'))",
                )
                _ensure_column(
                    connection,
                    "tasks",
                    "time_policy_version",
                    "TEXT",
                )
                _backfill_task_expected_old_price(connection)
                migration_notes = {
                    1: "initial runtime schema",
                    2: "review token runtime schema",
                    3: "business rule evaluation runtime schema",
                    4: "shadowbot executor runtime schema",
                    5: "retry authorization persistence and shadowbot file queue audit fields",
                    6: "durable notification outbox and delivery attempt persistence",
                    7: "current platform listing status and price persistence",
                    8: "ShadowBot inventory observations with default stock and freshness fencing",
                    9: "platform listing identity uses platform, variety, and grade instead of SKU",
                    10: "task expected old price persisted as a first-class structured field",
                    11: "single-request ShadowBot commit batch ledger",
                    12: "per-item commit identity, write locks, observation times, and durable result receipts",
                    13: "listing action batches, status snapshots, anomalies, and shared write locks",
                    14: "operational time, automation, immutable observations, sales summaries, and incidents",
                    15: "incident occurrence counts and append-only incident events",
                    16: "versioned emergency offline policies for shadow evaluation",
                    17: "authoritative real inventory balances, immutable ledger, sales baselines, and alert policies",
                }
                for statement in SCHEMA_V6_SQL:
                    connection.execute(statement)
                for statement in SCHEMA_V7_SQL:
                    connection.execute(statement)
                _migrate_listing_status_to_v9(connection)
                for statement in SCHEMA_V11_SQL:
                    connection.execute(statement)
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "item_id",
                    "TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "operation_id",
                    "TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "item_execution_attempt_id",
                    "TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "write_identity_key",
                    "TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "page_identity_key",
                    "TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "side_effect_state",
                    "TEXT NOT NULL DEFAULT 'NOT_STARTED'",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "preflight_observed_at",
                    "TEXT",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "submit_intent_at",
                    "TEXT",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "submit_clicked_at",
                    "TEXT",
                )
                _ensure_column(
                    connection,
                    "shadowbot_commit_batch_items",
                    "readback_observed_at",
                    "TEXT",
                )
                _backfill_commit_item_identities(connection)
                connection.execute(
                    "DROP INDEX IF EXISTS ux_shadowbot_commit_batch_items_operation_id"
                )
                for statement in SCHEMA_V12_SQL:
                    connection.execute(statement)

                connection.execute(SCHEMA_V13_REGISTRY_SQL)
                _backfill_shadowbot_batch_registry(connection)
                _migrate_shadowbot_operations_to_v13(connection)
                _migrate_shadowbot_write_locks_to_v13(connection)
                _migrate_listing_action_batch_items_to_v13(connection)
                _ensure_listing_status_v13_columns(connection)
                for statement in SCHEMA_V13_SQL:
                    connection.execute(statement)
                _backfill_listing_status_latest_scan_observations(connection)
                _migrate_trade_day_summary_inputs_manifest_dimension(connection)
                _migrate_order_observation_v14_contract(connection)
                _migrate_trade_day_summary_transaction_amount(connection)
                connection.execute(
                    """
                    UPDATE tasks
                    SET origin_type = 'LEGACY'
                    WHERE origin_type IS NULL OR origin_type = ''
                    """
                )
                for statement in SCHEMA_V14_SQL:
                    connection.execute(statement)
                _migrate_operational_incidents_to_v15(connection)
                for statement in SCHEMA_V15_SQL:
                    connection.execute(statement)
                for statement in SCHEMA_V16_SQL:
                    connection.execute(statement)
                for statement in SCHEMA_V17_SQL:
                    connection.execute(statement)
                now_text = _datetime_to_text(datetime.now())
                connection.execute(
                    """
                    INSERT INTO inventory_authority_state(
                        authority_key, authority_mode,
                        bootstrap_snapshot_sha256,
                        bootstrap_runtime_snapshot_sha256,
                        bootstrap_sales_watermark_date,
                        bootstrap_idempotency_key,
                        bootstrap_completed_at, bootstrap_completed_by,
                        version, created_at, updated_at
                    )
                    SELECT 'REAL_INVENTORY', 'PRE_CUTOVER',
                           NULL, NULL, NULL, NULL, NULL, NULL,
                           0, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM inventory_authority_state
                        WHERE authority_key = 'REAL_INVENTORY'
                    )
                    """,
                    (now_text, now_text),
                )
                connection.execute(
                    """
                    INSERT INTO inventory_alert_policies(
                        policy_key, scope_type, scope_key,
                        enabled, threshold_qty, repeat_interval_minutes,
                        version, updated_by, created_at, updated_at
                    )
                    SELECT 'INVENTORY-ALERT-DEFAULT', 'DEFAULT', '*',
                           0, 0, 60, 1,
                           'runtime_schema_v17', ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM inventory_alert_policies
                        WHERE scope_type = 'DEFAULT' AND scope_key = '*'
                    )
                    """,
                    (now_text, now_text),
                )
                connection.execute(
                    """
                INSERT INTO operational_time_policies(
                    policy_version, timezone_name,
                    platform_cutoff_local_time,
                    seller_cutoff_local_time,
                        peak_start_local_time,
                        effective_from, effective_to,
                    created_at, created_by,
                    supersedes_policy_version
                )
                SELECT ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM operational_time_policies
                    WHERE policy_version = ?
                )
                """,
                    (
                        "CN_SINGLE_PLATFORM_2026_V1",
                        "Asia/Shanghai",
                        "18:00:00",
                        "20:00:00",
                        "16:00:00",
                        "2025-12-31T16:00:00+00:00",
                        _datetime_to_text(datetime.now()),
                        "runtime_schema_v14",
                        "CN_SINGLE_PLATFORM_2026_V1",
                    ),
                )
                for version in range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1):
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO runtime_schema_migrations(
                            schema_version, applied_at, note
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            version,
                            _datetime_to_text(datetime.now()),
                            migration_notes[version],
                        ),
                    )
                # Older builds recorded v5 for queue audit columns only.  Keep
                # the applied timestamp stable while correcting the
                # descriptive record.
                connection.execute(
                    "UPDATE runtime_schema_migrations SET note = ? "
                    "WHERE schema_version = 5",
                    (migration_notes[5],),
                )
                if requires_runtime_migration:
                    foreign_key_rows = connection.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                    if foreign_key_rows:
                        raise RuntimeError(
                            "Runtime Schema migration produced foreign "
                            f"key violations: {len(foreign_key_rows)}"
                        )
                    integrity_rows = [
                        str(row[0])
                        for row in connection.execute("PRAGMA integrity_check")
                    ]
                    if integrity_rows != ["ok"]:
                        raise RuntimeError(
                            "Runtime Schema migration failed integrity_check"
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                if requires_v13_migration or requires_v15_migration:
                    connection.execute("PRAGMA foreign_keys = ON")
                enabled = connection.execute("PRAGMA foreign_keys").fetchone()
                if enabled is None or int(enabled[0]) != 1:
                    raise RuntimeError(
                        "SQLite foreign key enforcement was not restored "
                        "after Runtime Schema migration"
                    )

        self.connection_factory.initialize_database(initialize_schema)

    def schema_versions(self) -> list[int]:
        if not self.db_path.exists():
            return []
        with closing(self.connection_factory.connect_read()) as connection:
            rows = connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations ORDER BY schema_version"
            ).fetchall()
        return [int(row["schema_version"]) for row in rows]

    def upsert_listing_status(self, status: ListingStatus) -> None:
        """Persist manual listing data; stock defaults to 100 and existing observations are preserved."""

        normalized_price = Decimal(str(status.current_price))
        if not normalized_price.is_finite() or normalized_price < 0:
            raise ValueError(
                "listing status current_price must be a finite non-negative decimal"
            )
        platform_name, variety, grade = require_listing_identity(
            status.platform_name, status.variety, status.grade
        )

        def operation() -> None:
            with closing(self.connection_factory.connect_write()) as connection:
                connection.execute(
                    """
                    INSERT INTO listing_status(
                        listing_status_id, platform_name, internal_sku, variety, grade,
                        current_price, platform_stock_qty, sold_qty, online_status,
                        source, updated_at, inventory_source,
                        inventory_observed_at, inventory_source_attempt_id,
                        price_source, price_observed_at,
                        price_source_attempt_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(platform_name, variety, grade) DO UPDATE SET
                        internal_sku = CASE
                            WHEN excluded.internal_sku <> '' THEN excluded.internal_sku
                            ELSE listing_status.internal_sku
                        END,
                        current_price = excluded.current_price,
                        sold_qty = excluded.sold_qty,
                        online_status = excluded.online_status,
                        source = excluded.source,
                        updated_at = excluded.updated_at,
                        price_source = excluded.source,
                        price_observed_at = NULL,
                        price_source_attempt_id = ''
                    """,
                    (
                        status.listing_status_id,
                        platform_name,
                        status.internal_sku,
                        variety,
                        grade,
                        serialize_decimal(normalized_price),
                        max(int(status.platform_stock_qty), 0),
                        int(status.sold_qty),
                        status.online_status,
                        status.source,
                        _datetime_to_text(status.updated_at or utc_now()),
                        status.inventory_source,
                        _datetime_to_text(status.inventory_observed_at),
                        status.inventory_source_attempt_id,
                        status.price_source,
                        _datetime_to_text(status.price_observed_at),
                        status.price_source_attempt_id,
                    ),
                )
                connection.commit()

        self._run_sqlite_retry(operation, operation_name="upsert listing status")

    def update_listing_price(
        self,
        *,
        platform_name: str,
        variety: str,
        grade: str,
        current_price: Decimal,
        source: str,
        updated_at: datetime,
    ) -> bool:
        normalized_price = Decimal(str(current_price))
        if not normalized_price.is_finite() or normalized_price < 0:
            raise ValueError(
                "listing status current_price must be a finite non-negative decimal"
            )
        platform_name, variety, grade = require_listing_identity(
            platform_name, variety, grade
        )

        def operation() -> bool:
            with closing(self.connection_factory.connect_write()) as connection:
                cursor = connection.execute(
                    """
                    UPDATE listing_status
                    SET current_price = ?, source = ?, updated_at = ?,
                        price_source = ?, price_observed_at = NULL,
                        price_source_attempt_id = ''
                    WHERE platform_name = ? AND variety = ? AND grade = ?
                    """,
                    (
                        serialize_decimal(normalized_price),
                        source,
                        _datetime_to_text(updated_at),
                        source,
                        platform_name,
                        variety,
                        grade,
                    ),
                )
                connection.commit()
                return cursor.rowcount == 1

        return bool(
            self._run_sqlite_retry(operation, operation_name="update listing price")
        )

    def apply_shadowbot_inventory_observation(
        self,
        *,
        platform_name: str,
        variety: str,
        grade: str,
        internal_sku: str = "",
        observed_price: Decimal,
        platform_stock_qty: int,
        online_status: str,
        observed_at: datetime,
        execution_attempt_id: str,
        source: str = "shadowbot_read",
    ) -> str:
        inventory = int(platform_stock_qty)
        if inventory < 0:
            raise ValueError("platform_stock_qty must be non-negative")
        normalized_price = Decimal(str(observed_price))
        if not normalized_price.is_finite() or normalized_price < 0:
            raise ValueError("observed_price must be a finite non-negative decimal")
        platform_name, variety, grade = require_listing_identity(
            platform_name, variety, grade
        )
        normalized_source = str(source or "shadowbot_read").strip()
        normalized_internal_sku = str(internal_sku or "").strip()
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        else:
            observed_at = observed_at.astimezone(timezone.utc)
        observed_text = _datetime_to_text(observed_at)

        def operation() -> str:
            with closing(self.connection_factory.connect_write()) as connection:
                existing = connection.execute(
                    """
                    SELECT inventory_observed_at, inventory_source_attempt_id
                    FROM listing_status WHERE platform_name = ? AND variety = ? AND grade = ?
                    """,
                    (platform_name, variety, grade),
                ).fetchone()
                if existing is not None:
                    existing_at = _text_to_datetime(existing["inventory_observed_at"])
                    if existing_at is not None and existing_at.tzinfo is None:
                        existing_at = existing_at.replace(tzinfo=timezone.utc)
                    existing_attempt = str(
                        existing["inventory_source_attempt_id"] or ""
                    )
                    if existing_at is not None and existing_at > observed_at:
                        return "STALE_IGNORED"
                    if existing_at == observed_at and existing_attempt not in {
                        "",
                        execution_attempt_id,
                    }:
                        return "STALE_IGNORED"
                cursor = connection.execute(
                    """
                    INSERT INTO listing_status(
                        listing_status_id, platform_name, internal_sku, variety, grade,
                        current_price, platform_stock_qty, sold_qty, online_status,
                        source, updated_at, inventory_source, inventory_observed_at,
                        inventory_source_attempt_id, price_source,
                        price_observed_at, price_source_attempt_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'shadowbot', ?, ?,
                              'shadowbot', ?, ?)
                    ON CONFLICT(platform_name, variety, grade) DO UPDATE SET
                        internal_sku = CASE
                            WHEN excluded.internal_sku <> '' THEN excluded.internal_sku
                            ELSE listing_status.internal_sku
                        END,
                        current_price = excluded.current_price,
                        platform_stock_qty = excluded.platform_stock_qty,
                        online_status = excluded.online_status,
                        source = excluded.source,
                        inventory_source = excluded.inventory_source,
                        inventory_observed_at = excluded.inventory_observed_at,
                        inventory_source_attempt_id = excluded.inventory_source_attempt_id,
                        price_source = excluded.price_source,
                        price_observed_at = excluded.price_observed_at,
                        price_source_attempt_id = excluded.price_source_attempt_id,
                        updated_at = excluded.updated_at
                    WHERE listing_status.inventory_observed_at IS NULL
                       OR listing_status.inventory_observed_at < excluded.inventory_observed_at
                       OR (
                            listing_status.inventory_observed_at = excluded.inventory_observed_at
                            AND listing_status.inventory_source_attempt_id = excluded.inventory_source_attempt_id
                       )
                    """,
                    (
                        f"LISTING-{uuid4().hex[:16]}",
                        platform_name,
                        normalized_internal_sku,
                        variety,
                        grade,
                        serialize_decimal(normalized_price),
                        inventory,
                        online_status,
                        normalized_source,
                        observed_text,
                        observed_text,
                        execution_attempt_id,
                        observed_text,
                        execution_attempt_id,
                    ),
                )
                connection.commit()
                if cursor.rowcount == 0:
                    return "STALE_IGNORED"
                return "UPDATED" if existing is not None else "CREATED"

        return str(
            self._run_sqlite_retry(
                operation, operation_name="apply ShadowBot inventory observation"
            )
        )

    def get_listing_status(
        self, platform_name: str, variety: str, grade: str
    ) -> ListingStatus | None:
        platform_name, variety, grade = require_listing_identity(
            platform_name, variety, grade
        )
        with closing(self.connection_factory.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM listing_status WHERE platform_name = ? AND variety = ? AND grade = ?",
                (platform_name, variety, grade),
            ).fetchone()
        return _listing_status_from_row(row) if row is not None else None

    def list_listing_statuses(
        self,
        *,
        platform_name: str | None = None,
        internal_sku: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ListingStatus]:
        query = "SELECT * FROM listing_status"
        clauses: list[str] = []
        params: list[object] = []
        if platform_name:
            clauses.append("platform_name = ?")
            params.append(normalize_listing_text(platform_name))
        if internal_sku:
            clauses.append("internal_sku = ?")
            params.append(str(internal_sku).strip())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY platform_name, variety, grade"
        query, params = _with_limit_offset(query, params, limit=limit, offset=offset)
        with closing(self.connection_factory.connect_read()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_listing_status_from_row(row) for row in rows]

    def check_schema_health(self) -> RuntimeSchemaHealth:
        """Return a non-mutating exact-latest-schema health report."""

        if not self.db_path.exists():
            memory_factory = SQLiteConnectionFactory(":memory:")
            with closing(memory_factory.connect_write()) as connection:
                return inspect_runtime_schema(connection)
        with closing(self.connection_factory.connect_read()) as connection:
            return inspect_runtime_schema(connection)

    def check_operational_health(self) -> SQLiteOperationalHealth:
        """Return non-mutating WAL, PRAGMA, and local-storage health facts."""

        config = self.connection_factory.config
        database_exists = self.db_path.exists()
        local_storage = False
        journal_mode: str | None = None
        synchronous: str | None = None
        foreign_keys: int | None = None
        busy_timeout_ms: int | None = None
        try:
            self.connection_factory.validate_storage_location()
            local_storage = True
            if not database_exists:
                return SQLiteOperationalHealth(
                    ok=False,
                    database_exists=False,
                    local_storage=True,
                    journal_mode=None,
                    synchronous=None,
                    foreign_keys=None,
                    busy_timeout_ms=None,
                    configured_busy_timeout_ms=config.busy_timeout_ms,
                    error="SQLite database file does not exist",
                )
            with closing(self.connection_factory.connect_read()) as connection:
                journal_row = connection.execute("PRAGMA journal_mode").fetchone()
                synchronous_row = connection.execute("PRAGMA synchronous").fetchone()
                foreign_keys_row = connection.execute("PRAGMA foreign_keys").fetchone()
                busy_timeout_row = connection.execute("PRAGMA busy_timeout").fetchone()
            journal_mode = str(journal_row[0]).lower() if journal_row else None
            synchronous = (
                {
                    0: "OFF",
                    1: "NORMAL",
                    2: "FULL",
                    3: "EXTRA",
                }.get(int(synchronous_row[0]), "UNKNOWN")
                if synchronous_row
                else None
            )
            foreign_keys = int(foreign_keys_row[0]) if foreign_keys_row else None
            busy_timeout_ms = int(busy_timeout_row[0]) if busy_timeout_row else None
            ok = (
                journal_mode == "wal"
                and synchronous == "NORMAL"
                and foreign_keys == 1
                and busy_timeout_ms == config.busy_timeout_ms
            )
            return SQLiteOperationalHealth(
                ok=ok,
                database_exists=True,
                local_storage=True,
                journal_mode=journal_mode,
                synchronous=synchronous,
                foreign_keys=foreign_keys,
                busy_timeout_ms=busy_timeout_ms,
                configured_busy_timeout_ms=config.busy_timeout_ms,
            )
        except (sqlite3.Error, SQLiteConnectionError) as exc:
            return SQLiteOperationalHealth(
                ok=False,
                database_exists=database_exists,
                local_storage=local_storage,
                journal_mode=journal_mode,
                synchronous=synchronous,
                foreign_keys=foreign_keys,
                busy_timeout_ms=busy_timeout_ms,
                configured_busy_timeout_ms=config.busy_timeout_ms,
                error=f"SQLite operational check failed: {type(exc).__name__}",
            )

    def runtime_schema_health(self) -> RuntimeSchemaHealth:
        """Alias used by operational callers that name the report directly."""

        return self.check_schema_health()

    def health_check(self) -> RuntimeSchemaHealth:
        """Alias used by HTTP/CLI health-check adapters."""

        return self.check_schema_health()

    def replace_current_operational_time_policy(
        self,
        *,
        expected_current_policy_version: str,
        successor_policy_version: str,
        effective_from: datetime,
        platform_cutoff_local_time: str,
        seller_cutoff_local_time: str,
        peak_start_local_time: str,
        created_by: str,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        """Atomically close the current policy and install its successor."""

        expected_version = str(expected_current_policy_version).strip()
        successor_version = str(successor_policy_version).strip()
        normalized_created_by = str(created_by).strip()
        normalized_timezone = str(timezone_name).strip()
        cutoff_values = tuple(
            str(value).strip()
            for value in (
                platform_cutoff_local_time,
                seller_cutoff_local_time,
                peak_start_local_time,
            )
        )
        if not expected_version:
            raise ValueError("expected_current_policy_version must not be blank")
        if not successor_version:
            raise ValueError("successor_policy_version must not be blank")
        if successor_version == expected_version:
            raise ValueError(
                "successor_policy_version must differ from the current policy"
            )
        if not normalized_created_by:
            raise ValueError("created_by must not be blank")
        if any(not value for value in cutoff_values):
            raise ValueError("operational cutoff times must not be blank")
        if effective_from.tzinfo is None or effective_from.utcoffset() is None:
            raise ValueError("effective_from must be timezone-aware")
        effective_from_utc = effective_from.astimezone(timezone.utc)
        effective_from_text = effective_from_utc.isoformat()

        def replace_policy() -> None:
            connection = self.connect_write()
            try:
                connection.execute("BEGIN IMMEDIATE")
                current_rows = connection.execute(
                    """
                    SELECT policy_version, timezone_name
                    FROM operational_time_policies
                    WHERE effective_to IS NULL
                    ORDER BY policy_version
                    """
                ).fetchall()
                if len(current_rows) != 1:
                    raise RuntimeError(
                        "operational time policy replacement requires "
                        "exactly one current policy"
                    )
                current_version = str(current_rows[0]["policy_version"])
                current_timezone = str(current_rows[0]["timezone_name"])
                if current_version != expected_version:
                    raise ValueError(
                        "current operational time policy changed: "
                        f"expected {expected_version}, found {current_version}"
                    )
                if current_timezone != normalized_timezone:
                    raise ValueError("successor timezone must match the current policy")

                updated = connection.execute(
                    """
                    UPDATE operational_time_policies
                    SET effective_to = ?
                    WHERE policy_version = ?
                      AND effective_to IS NULL
                    """,
                    (effective_from_text, expected_version),
                )
                if updated.rowcount != 1:
                    raise RuntimeError(
                        "current operational time policy could not be closed"
                    )

                connection.execute(
                    """
                    INSERT INTO operational_time_policies(
                        policy_version, timezone_name,
                        platform_cutoff_local_time,
                        seller_cutoff_local_time,
                        peak_start_local_time,
                        effective_from, effective_to,
                        created_at, created_by,
                        supersedes_policy_version
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (
                        successor_version,
                        normalized_timezone,
                        cutoff_values[0],
                        cutoff_values[1],
                        cutoff_values[2],
                        effective_from_text,
                        _datetime_to_text(self._clock()),
                        normalized_created_by,
                        expected_version,
                    ),
                )
                current_after = connection.execute(
                    """
                    SELECT policy_version
                    FROM operational_time_policies
                    WHERE effective_to IS NULL
                    """
                ).fetchall()
                if (
                    len(current_after) != 1
                    or str(current_after[0]["policy_version"]) != successor_version
                ):
                    raise RuntimeError(
                        "operational time policy replacement did not leave "
                        "exactly one expected current policy"
                    )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()

        self._run_sqlite_retry(
            replace_policy,
            operation_name="operational time policy replacement",
        )

    def insert_tasks(self, tasks: Iterable[Task]) -> int:
        task_rows = list(tasks)
        self._validate_tasks_for_insert(task_rows)
        if not task_rows:
            return 0
        with closing(self.connect()) as connection, connection:
            return self._insert_tasks_on_connection(connection, task_rows)

    @staticmethod
    def _validate_tasks_for_insert(task_rows: list[Task]) -> None:
        for task in task_rows:
            if task.origin_type is TaskOriginType.LEGACY:
                raise ValueError(
                    "LEGACY is migration-only and cannot be created "
                    "through the task repository"
                )
            if task.origin_type is TaskOriginType.SYSTEM_EMERGENCY:
                raise ValueError(
                    "SYSTEM_EMERGENCY creation remains disabled until "
                    "the dedicated 13.5-6 authorization service"
                )
            if (
                task.origin_type in {TaskOriginType.MANUAL, TaskOriginType.AUTOMATION}
                and not str(task.origin_ref_id or "").strip()
            ):
                raise ValueError("MANUAL and AUTOMATION tasks require an origin_ref_id")

    @staticmethod
    def _insert_tasks_on_connection(
        connection: sqlite3.Connection,
        task_rows: list[Task],
    ) -> int:
        rows = [_task_to_row(task) for task in task_rows]
        if not rows:
            return 0
        before = connection.total_changes
        connection.executemany(
            """
            INSERT OR IGNORE INTO tasks(
                task_id, trade_date, scope_type, scope_key, dedupe_key, internal_sku,
                platform_name, action_type, priority, task_status, created_at, scheduled_at,
                expires_at, expected_old_price, target_price, target_inventory,
                target_status, pricing_source, decision_trace_json,
                result_message, required_by, updated_at,
                origin_type, origin_ref_id, approval_policy, policy_version,
                platform_trade_date, seller_operation_date, seller_phase,
                time_policy_version
            )
            VALUES(
                :task_id, :trade_date, :scope_type, :scope_key, :dedupe_key, :internal_sku,
                :platform_name, :action_type, :priority, :task_status, :created_at, :scheduled_at,
                :expires_at, :expected_old_price, :target_price, :target_inventory,
                :target_status, :pricing_source, :decision_trace_json,
                :result_message, :required_by, :updated_at,
                :origin_type, :origin_ref_id, :approval_policy, :policy_version,
                :platform_trade_date, :seller_operation_date, :seller_phase,
                :time_policy_version
            )
            """,
            rows,
        )
        return connection.total_changes - before

    def insert_task(self, task: Task) -> int:
        return self.insert_tasks([task])

    def list_tasks(
        self,
        *,
        trade_date: date | None = None,
        status: TaskStatus | None = None,
        action_type: TaskActionType | None = None,
        scope_type: str | None = None,
        scope_key: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Task]:
        query = "SELECT * FROM tasks"
        clauses: list[str] = []
        params: list[str] = []
        if trade_date is not None:
            clauses.append("trade_date = ?")
            params.append(trade_date.isoformat())
        if status is not None:
            clauses.append("task_status = ?")
            params.append(status.value)
        if action_type is not None:
            clauses.append("action_type = ?")
            params.append(action_type.value)
        if scope_type:
            clauses.append("scope_type = ?")
            params.append(scope_type)
        if scope_key:
            clauses.append("scope_key = ?")
            params.append(scope_key)
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"""{query} ORDER BY
            CASE
                WHEN origin_type = 'MANUAL'
                 AND origin_ref_id LIKE 'incident-review:%' THEN 0
                WHEN origin_type = 'SYSTEM_EMERGENCY' THEN 1
                ELSE 2
            END ASC,
            priority ASC, created_at ASC, task_id ASC
        """
        query, params = _with_limit_offset(query, params, limit=limit, offset=offset)
        with closing(self.connect_read()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_task(row) for row in rows]

    def get_task(self, task_id: str) -> Task | None:
        with closing(self.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def list_task_history_page(
        self,
        *,
        trade_date: date | None = None,
        status: TaskStatus | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[Task]:
        clauses: list[str] = []
        params: list[object] = []
        if trade_date is not None:
            clauses.append("trade_date = ?")
            params.append(trade_date.isoformat())
        if status is not None:
            clauses.append("task_status = ?")
            params.append(status.value)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        query = f"""
            SELECT * FROM tasks {where}
            ORDER BY created_at DESC, task_id DESC
        """
        query, params = _with_limit_offset(query, params, limit=limit, offset=offset)
        with closing(self.connect_read()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_task(row) for row in rows]

    def get_open_task_by_dedupe_key(self, dedupe_key: str) -> Task | None:
        if not dedupe_key:
            return None
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM tasks
                WHERE dedupe_key = ?
                  AND task_status NOT IN ('success', 'failed', 'skipped', 'cancelled', 'expired')
                ORDER BY created_at DESC, task_id ASC
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
        return _row_to_task(row) if row is not None else None

    def update_task_status(
        self, task_id: str, status: TaskStatus, *, result_message: str = ""
    ) -> None:
        updated_at = _datetime_to_text(datetime.now())
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE tasks
                SET task_status = ?, result_message = COALESCE(NULLIF(?, ''), result_message), updated_at = ?
                WHERE task_id = ?
                """,
                (status.value, result_message, updated_at, task_id),
            )

    def update_task_status_with_history(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        history: TaskStatusHistory,
        result_message: str = "",
    ) -> None:
        updated_at = _datetime_to_text(datetime.now())
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE tasks
                SET task_status = ?, result_message = COALESCE(NULLIF(?, ''), result_message), updated_at = ?
                WHERE task_id = ?
                """,
                (status.value, result_message, updated_at, task_id),
            )
            connection.execute(
                """
                INSERT INTO task_status_history(
                    history_id, task_id, from_status, to_status, changed_by, changed_at, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history.history_id,
                    history.task_id,
                    history.from_status.value
                    if history.from_status is not None
                    else None,
                    history.to_status.value,
                    history.changed_by,
                    _datetime_to_text(history.changed_at),
                    history.reason,
                    _json_dump(history.metadata),
                ),
            )

    def insert_status_history(self, history: TaskStatusHistory) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO task_status_history(
                    history_id, task_id, from_status, to_status, changed_by, changed_at, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history.history_id,
                    history.task_id,
                    history.from_status.value if history.from_status else None,
                    history.to_status.value,
                    history.changed_by,
                    _datetime_to_text(history.changed_at),
                    history.reason,
                    _json_dump(history.metadata),
                ),
            )

    def list_task_status_history(self, task_id: str) -> list[TaskStatusHistory]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM task_status_history
                WHERE task_id = ?
                ORDER BY changed_at ASC, history_id ASC
                """,
                (task_id,),
            ).fetchall()
        return [_row_to_status_history(row) for row in rows]

    def insert_review_tasks(self, review_tasks: Iterable[ReviewTask]) -> int:
        rows = [_review_task_to_row(review_task) for review_task in review_tasks]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO review_tasks(
                    review_task_id, trade_date, scope_type, scope_key, dedupe_key, source_task_id,
                    review_type, review_status, internal_sku, platform_name, reason, review_payload_json,
                    resolution_payload_json, required_by, created_at, updated_at, resolved_by,
                    resolved_at, resolution_note
                )
                VALUES(
                    :review_task_id, :trade_date, :scope_type, :scope_key, :dedupe_key, :source_task_id,
                    :review_type, :review_status, :internal_sku, :platform_name, :reason, :review_payload_json,
                    :resolution_payload_json, :required_by, :created_at, :updated_at, :resolved_by,
                    :resolved_at, :resolution_note
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def list_review_tasks(
        self,
        *,
        trade_date: date | None = None,
        status: ReviewTaskStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReviewTask]:
        query = "SELECT * FROM review_tasks"
        clauses: list[str] = []
        params: list[str] = []
        if trade_date is not None:
            clauses.append("trade_date = ?")
            params.append(trade_date.isoformat())
        if status is not None:
            clauses.append("review_status = ?")
            params.append(status.value)
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"{query} ORDER BY required_by IS NULL, required_by ASC, created_at ASC"
        query, params = _with_limit_offset(query, params, limit=limit, offset=offset)
        with closing(self.connect_read()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_review_task(row) for row in rows]

    def get_review_task(self, review_task_id: str) -> ReviewTask | None:
        with closing(self.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM review_tasks WHERE review_task_id = ?",
                (review_task_id,),
            ).fetchone()
        return _row_to_review_task(row) if row is not None else None

    def list_review_history_page(
        self,
        *,
        trade_date: date | None = None,
        status: ReviewTaskStatus | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[ReviewTask]:
        clauses: list[str] = []
        params: list[object] = []
        if trade_date is not None:
            clauses.append("trade_date = ?")
            params.append(trade_date.isoformat())
        if status is not None:
            clauses.append("review_status = ?")
            params.append(status.value)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        query = f"""
            SELECT * FROM review_tasks {where}
            ORDER BY created_at DESC, review_task_id DESC
        """
        query, params = _with_limit_offset(query, params, limit=limit, offset=offset)
        with closing(self.connect_read()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_review_task(row) for row in rows]

    def count_review_tasks(self, *, status: ReviewTaskStatus | None = None) -> int:
        query = "SELECT COUNT(*) AS item_count FROM review_tasks"
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE review_status = ?"
            params = (status.value,)
        with closing(self.connect_read()) as connection:
            row = connection.execute(query, params).fetchone()
        return int(row["item_count"] if row is not None else 0)

    def get_pending_review_task_by_dedupe_key(
        self, dedupe_key: str
    ) -> ReviewTask | None:
        if not dedupe_key:
            return None
        with closing(self.connect_read()) as connection:
            row = connection.execute(
                """
                SELECT * FROM review_tasks
                WHERE dedupe_key = ? AND review_status = 'pending'
                ORDER BY created_at DESC, review_task_id ASC
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
        return _row_to_review_task(row) if row is not None else None

    def list_pending_review_tasks_due_before(
        self, cutoff: datetime
    ) -> list[ReviewTask]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM review_tasks
                WHERE review_status = 'pending'
                  AND required_by IS NOT NULL
                  AND required_by < ?
                ORDER BY required_by ASC, created_at ASC
                """,
                (_datetime_to_text(cutoff),),
            ).fetchall()
        return [_row_to_review_task(row) for row in rows]

    def update_review_task(self, review_task: ReviewTask) -> None:
        row = _review_task_to_row(review_task)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE review_tasks
                SET review_status = :review_status,
                    resolution_payload_json = :resolution_payload_json,
                    updated_at = :updated_at,
                    resolved_by = :resolved_by,
                    resolved_at = :resolved_at,
                    resolution_note = :resolution_note
                WHERE review_task_id = :review_task_id
                """,
                row,
            )
            if review_task.review_status != ReviewTaskStatus.PENDING:
                self._cancel_review_outbox_on_connection(
                    connection,
                    review_task.review_task_id,
                    changed_at=review_task.updated_at or self._clock(),
                )

    def update_review_task_with_optional_task_status(
        self,
        review_task: ReviewTask,
        *,
        task_id: str | None = None,
        task_status: TaskStatus | None = None,
        history: TaskStatusHistory | None = None,
        result_message: str = "",
        retry_required_by: datetime | None = None,
    ) -> None:
        review_row = _review_task_to_row(review_task)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE review_tasks
                SET review_status = :review_status,
                    resolution_payload_json = :resolution_payload_json,
                    updated_at = :updated_at,
                    resolved_by = :resolved_by,
                    resolved_at = :resolved_at,
                    resolution_note = :resolution_note
                WHERE review_task_id = :review_task_id
                """,
                review_row,
            )
            if review_task.review_status != ReviewTaskStatus.PENDING:
                self._cancel_review_outbox_on_connection(
                    connection,
                    review_task.review_task_id,
                    changed_at=review_task.updated_at or self._clock(),
                )
            if task_id is None or task_status is None or history is None:
                return
            updated_at = _datetime_to_text(datetime.now())
            connection.execute(
                """
                UPDATE tasks
                SET task_status = ?,
                    required_by = COALESCE(?, required_by),
                    expires_at = COALESCE(?, expires_at),
                    result_message = COALESCE(NULLIF(?, ''), result_message),
                    updated_at = ?
                WHERE task_id = ?
                """,
                (
                    task_status.value,
                    _datetime_to_text(retry_required_by),
                    _datetime_to_text(retry_required_by),
                    result_message,
                    updated_at,
                    task_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO task_status_history(
                    history_id, task_id, from_status, to_status, changed_by, changed_at, reason, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    history.history_id,
                    history.task_id,
                    history.from_status.value
                    if history.from_status is not None
                    else None,
                    history.to_status.value,
                    history.changed_by,
                    _datetime_to_text(history.changed_at),
                    history.reason,
                    _json_dump(history.metadata),
                ),
            )

    def update_review_task_with_task_statuses(
        self,
        review_task: ReviewTask,
        *,
        task_updates: list[tuple[str, TaskStatus, TaskStatus, TaskStatusHistory]],
        result_message: str = "",
        retry_required_by: datetime | None = None,
    ) -> None:
        """Resolve one review and update every affected task in one transaction."""

        review_row = _review_task_to_row(review_task)
        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            review_updated = connection.execute(
                """
                UPDATE review_tasks
                SET review_status = :review_status,
                    resolution_payload_json = :resolution_payload_json,
                    updated_at = :updated_at,
                    resolved_by = :resolved_by,
                    resolved_at = :resolved_at,
                    resolution_note = :resolution_note
                WHERE review_task_id = :review_task_id
                  AND review_status = 'pending'
                """,
                review_row,
            ).rowcount
            if review_updated != 1:
                raise ValueError("review task was not pending during group resolution")
            self._cancel_review_outbox_on_connection(
                connection,
                review_task.review_task_id,
                changed_at=review_task.updated_at or self._clock(),
            )
            changed_at = _datetime_to_text(review_task.updated_at or self._clock())
            for task_id, from_status, to_status, history in task_updates:
                task_updated = connection.execute(
                    """
                    UPDATE tasks
                    SET task_status = ?,
                        required_by = COALESCE(?, required_by),
                        expires_at = COALESCE(?, expires_at),
                        result_message = COALESCE(NULLIF(?, ''), result_message),
                        updated_at = ?
                    WHERE task_id = ? AND task_status = ?
                    """,
                    (
                        to_status.value,
                        _datetime_to_text(retry_required_by),
                        _datetime_to_text(retry_required_by),
                        result_message,
                        changed_at,
                        task_id,
                        from_status.value,
                    ),
                ).rowcount
                if task_updated != 1:
                    raise ValueError(
                        f"task changed during group review resolution: {task_id}"
                    )
                connection.execute(
                    """
                    INSERT INTO task_status_history(
                        history_id, task_id, from_status, to_status, changed_by,
                        changed_at, reason, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        history.history_id,
                        history.task_id,
                        history.from_status.value
                        if history.from_status is not None
                        else None,
                        history.to_status.value,
                        history.changed_by,
                        _datetime_to_text(history.changed_at),
                        history.reason,
                        _json_dump(history.metadata),
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def expire_review_task_with_notification_outbox(
        self,
        review_task: ReviewTask,
        notification: NotificationOutbox,
        compatibility_log: NotificationLog,
        *,
        task_id: str | None = None,
        task_status: TaskStatus | None = None,
        history: TaskStatusHistory | None = None,
        result_message: str = "",
        failure_injector: Callable[[str], None] | None = None,
    ) -> tuple[int, int]:
        """Atomically expire review/business state and persist the notification intent."""

        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            review_row = _review_task_to_row(review_task)
            updated_review = connection.execute(
                """
                UPDATE review_tasks
                SET review_status = :review_status,
                    resolution_payload_json = :resolution_payload_json,
                    updated_at = :updated_at,
                    resolved_by = :resolved_by,
                    resolved_at = :resolved_at,
                    resolution_note = :resolution_note
                WHERE review_task_id = :review_task_id AND review_status = 'pending'
                """,
                review_row,
            ).rowcount
            if updated_review != 1:
                connection.rollback()
                return 0, 0
            self._cancel_review_outbox_on_connection(
                connection,
                review_task.review_task_id,
                changed_at=review_task.updated_at or self._clock(),
            )
            if task_id is not None and task_status is not None and history is not None:
                changed_task = connection.execute(
                    """
                    UPDATE tasks
                    SET task_status = ?, result_message = COALESCE(NULLIF(?, ''), result_message), updated_at = ?
                    WHERE task_id = ? AND task_status = ?
                    """,
                    (
                        task_status.value,
                        result_message,
                        _datetime_to_text(review_task.updated_at or self._clock()),
                        task_id,
                        history.from_status.value
                        if history.from_status is not None
                        else "",
                    ),
                ).rowcount
                if changed_task != 1:
                    raise ValueError(
                        "source task was not updated while expiring review"
                    )
                connection.execute(
                    """
                    INSERT INTO task_status_history(
                        history_id, task_id, from_status, to_status, changed_by,
                        changed_at, reason, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        history.history_id,
                        history.task_id,
                        history.from_status.value
                        if history.from_status is not None
                        else None,
                        history.to_status.value,
                        history.changed_by,
                        _datetime_to_text(history.changed_at),
                        history.reason,
                        _json_dump(history.metadata),
                    ),
                )
            if failure_injector is not None:
                failure_injector("after_business_update")
            outbox_inserted = self._insert_notification_outbox_on_connection(
                connection, notification
            )
            if outbox_inserted != 1:
                raise ValueError("expired review notification_key already exists")
            if failure_injector is not None:
                failure_injector("after_outbox_insert")
            log_row = _notification_log_to_row(compatibility_log)
            inserted_log = connection.execute(
                """
                INSERT INTO notification_logs(
                    notification_id, related_task_id, related_review_task_id, recipient_type,
                    recipient, channel, sent_at, send_status, dedupe_key, message,
                    error_message, created_at
                ) VALUES(
                    :notification_id, :related_task_id, :related_review_task_id, :recipient_type,
                    :recipient, :channel, :sent_at, :send_status, :dedupe_key, :message,
                    :error_message, :created_at
                )
                """,
                log_row,
            ).rowcount
            if inserted_log != 1:
                raise ValueError("expired review compatibility log was not inserted")
            if failure_injector is not None:
                failure_injector("after_compatibility_log_insert")
            connection.commit()
            return updated_review, outbox_inserted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew_review_task_with_notification_outbox(
        self,
        review_task: ReviewTask,
        notification: NotificationOutbox,
        compatibility_log: NotificationLog,
        *,
        expected_required_by: datetime | None,
        review_token: ReviewToken | None = None,
    ) -> tuple[int, int]:
        """Atomically extend one pending review and persist its reminder."""

        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated_review = connection.execute(
                """
                UPDATE review_tasks
                SET required_by = ?, updated_at = ?
                WHERE review_task_id = ?
                  AND review_status = 'pending'
                  AND required_by = ?
                """,
                (
                    _datetime_to_text(review_task.required_by),
                    _datetime_to_text(review_task.updated_at),
                    review_task.review_task_id,
                    _datetime_to_text(expected_required_by),
                ),
            ).rowcount
            if updated_review != 1:
                connection.rollback()
                return 0, 0
            self._cancel_review_outbox_on_connection(
                connection,
                review_task.review_task_id,
                changed_at=review_task.updated_at or self._clock(),
            )
            if review_token is not None:
                connection.execute(
                    """
                    UPDATE review_tokens
                    SET revoked_at = ?
                    WHERE review_task_id = ? AND used_at IS NULL
                      AND revoked_at IS NULL
                    """,
                    (
                        _datetime_to_text(review_task.updated_at),
                        review_task.review_task_id,
                    ),
                )
                token_row = _review_token_to_row(review_token)
                if connection.execute(
                    """
                    INSERT INTO review_tokens(
                        token_id, review_task_id, token_hash, token_subject,
                        allowed_actions, expires_at, used_at, revoked_at,
                        created_at, created_by, last_used_at, note
                    ) VALUES(
                        :token_id, :review_task_id, :token_hash, :token_subject,
                        :allowed_actions, :expires_at, :used_at, :revoked_at,
                        :created_at, :created_by, :last_used_at, :note
                    )
                    """,
                    token_row,
                ).rowcount != 1:
                    raise ValueError("reissued review token was not inserted")
            outbox_inserted = self._insert_notification_outbox_on_connection(
                connection,
                notification,
            )
            if outbox_inserted != 1:
                raise ValueError("renewed review notification was not inserted")
            log_row = _notification_log_to_row(compatibility_log)
            inserted_log = connection.execute(
                """
                INSERT INTO notification_logs(
                    notification_id, related_task_id, related_review_task_id,
                    recipient_type, recipient, channel, sent_at, send_status,
                    dedupe_key, message, error_message, created_at
                ) VALUES(
                    :notification_id, :related_task_id, :related_review_task_id,
                    :recipient_type, :recipient, :channel, :sent_at, :send_status,
                    :dedupe_key, :message, :error_message, :created_at
                )
                """,
                log_row,
            ).rowcount
            if inserted_log != 1:
                raise ValueError("renewed review compatibility log was not inserted")
            connection.commit()
            return updated_review, outbox_inserted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @classmethod
    def _cancel_review_outbox_on_connection(
        cls,
        connection: sqlite3.Connection,
        review_task_id: str,
        *,
        changed_at: datetime,
    ) -> int:
        rows = connection.execute(
            """
            SELECT notification_id FROM notification_outbox
            WHERE related_review_task_id = ?
              AND status IN ('PENDING', 'RETRY_WAIT', 'LEASED')
            """,
            (review_task_id,),
        ).fetchall()
        changed = 0
        for row in rows:
            notification_id = str(row["notification_id"])
            updated = connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'CANCELLED', lease_owner_token = '', lease_expires_at = NULL,
                    last_error_code = 'BUSINESS_EVENT_RESOLVED',
                    last_error_message = 'notification cancelled because review was resolved',
                    updated_at = ?
                WHERE notification_id = ?
                  AND status IN ('PENDING', 'RETRY_WAIT', 'LEASED')
                """,
                (_datetime_to_text(changed_at), notification_id),
            ).rowcount
            if updated == 1:
                changed += 1
                cls._update_notification_log_delivery_on_connection(
                    connection,
                    notification_id,
                    send_status="failed",
                    error_message="notification cancelled because review was resolved",
                )
        return changed

    def resolve_mobile_review_atomic(
        self,
        *,
        review_task_id: str,
        token_hash: str,
        status: ReviewTaskStatus,
        actor_source: str,
        actor: str | None = None,
        note: str = "",
        resolution_payload: dict[str, object] | None = None,
        emergency_base_cost: Decimal | None = None,
        emergency_base_cost_source_ref: str = "",
        emergency_product_snapshot_verifier: (
            Callable[[], tuple[Decimal, str]] | None
        ) = None,
        now: datetime | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> MobileReviewAtomicResult:
        """Resolve Mobile Review state in one SQLite transaction.

        Parsing and payload-shape validation belong before this method. Every
        state decision here is made from rows read through the same connection
        after ``BEGIN IMMEDIATE``.
        """

        payload = normalize_mobile_review_resolution_payload(status, resolution_payload)

        def inject(point: str) -> None:
            if failure_injector is not None:
                failure_injector(point)

        with closing(self.connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                token_row = connection.execute(
                    "SELECT * FROM review_tokens WHERE token_hash = ?",
                    (token_hash,),
                ).fetchone()
                if token_row is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_NOT_FOUND,
                        "链接已失效或无权访问该复核任务",
                    )
                if str(token_row["review_task_id"]) != review_task_id:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_REVIEW_MISMATCH,
                        "链接已失效或无权访问该复核任务",
                    )

                review_row = connection.execute(
                    "SELECT * FROM review_tasks WHERE review_task_id = ?",
                    (review_task_id,),
                ).fetchone()
                if review_row is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.REVIEW_NOT_FOUND,
                        "链接已失效或无权访问该复核任务",
                    )

                expires_at = _text_to_datetime(token_row["expires_at"])
                timestamp = _timestamp_for_deadline(
                    provided=now,
                    deadline=expires_at,
                )
                if expires_at is not None and expires_at <= timestamp:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_EXPIRED,
                        "链接已失效或无权访问该复核任务",
                    )
                if token_row["revoked_at"] is not None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_REVOKED,
                        "链接已失效或无权访问该复核任务",
                    )
                if token_row["used_at"] is not None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.TOKEN_ALREADY_USED,
                        "链接已失效或无权访问该复核任务",
                    )
                if str(review_row["review_status"]) != ReviewTaskStatus.PENDING.value:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED,
                        "链接已失效或无权访问该复核任务",
                    )

                action = status.value
                allowed_actions = _json_list_load(token_row["allowed_actions"])
                if action not in allowed_actions:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.ACTION_NOT_ALLOWED,
                        "链接已失效或无权访问该复核任务",
                    )
                if action not in MOBILE_REVIEW_ACTIONS:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE,
                        "链接已失效或无权访问该复核任务",
                    )

                review_model = _row_to_review_task(review_row)
                if review_model.review_type == "emergency_protection":
                    result = self._resolve_incident_mobile_review_on_connection(
                        connection,
                        review_model=review_model,
                        token_row=token_row,
                        status=status,
                        actor_source=actor_source,
                        actor=actor,
                        note=note,
                        payload=payload,
                        timestamp=timestamp,
                        emergency_base_cost=emergency_base_cost,
                        emergency_base_cost_source_ref=emergency_base_cost_source_ref,
                        emergency_product_snapshot_verifier=(
                            emergency_product_snapshot_verifier
                        ),
                        inject=inject,
                    )
                    connection.commit()
                    return result

                source_task_id = review_row["source_task_id"]
                if not source_task_id:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND,
                        "关联源任务不存在或已失效",
                    )
                source_row = (
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (source_task_id,),
                    ).fetchone()
                    if source_task_id
                    else None
                )
                if source_row is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND,
                        "关联源任务不存在或已失效",
                    )
                source_model = _row_to_task(source_row)
                affected_task_ids = review_source_task_ids(review_model)
                source_rows = [source_row]
                if len(affected_task_ids) > 1:
                    placeholders = ",".join("?" for _ in affected_task_ids)
                    grouped_rows = connection.execute(
                        f"SELECT * FROM tasks WHERE task_id IN ({placeholders})",
                        affected_task_ids,
                    ).fetchall()
                    rows_by_id = {str(row["task_id"]): row for row in grouped_rows}
                    if any(task_id not in rows_by_id for task_id in affected_task_ids):
                        raise MobileReviewTransactionError(
                            MobileReviewErrorCode.SOURCE_TASK_NOT_FOUND,
                            "复核任务组中的来源任务不存在或已失效",
                        )
                    source_rows = [rows_by_id[task_id] for task_id in affected_task_ids]
                if status not in allowed_review_statuses(review_model, source_model):
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE,
                        "执行失败复核只允许选择“重试任务”或“取消任务”。",
                    )
                business_decision = review_business_decision(
                    review_model,
                    source_model,
                    status,
                )
                if business_decision:
                    payload["decision"] = business_decision
                    payload["task_group_id"] = str(
                        review_model.review_payload.get("task_group_id") or ""
                    )
                    payload["affected_task_ids"] = affected_task_ids
                    payload["affected_task_count"] = len(affected_task_ids)
                retry_required_by = (
                    retry_task_deadline(timestamp)
                    if business_decision == "retry_task"
                    else None
                )
                if retry_required_by is not None:
                    payload["retry_required_by"] = retry_required_by.isoformat()
                source_task_status = _atomic_source_task_status(source_row, status)
                if source_task_status is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "关联源任务状态已变化，复核请求未提交",
                    )
                resolved_actor = actor or str(token_row["token_subject"])
                adjustment = (
                    payload.get("adjustment")
                    if status == ReviewTaskStatus.ADJUSTED
                    else None
                )
                adjusted_target_price = None
                adjusted_target_status = None
                adjusted_result_message = note
                adjusted_decision_trace_json = None
                if isinstance(adjustment, dict):
                    adjusted_target_price = adjustment.get("target_price")
                    adjusted_target_status = adjustment.get("target_status")
                    adjusted_result_message = str(
                        adjustment.get("result_message") or note
                    )
                    if source_row is not None:
                        decision_trace = _json_load(source_row["decision_trace_json"])
                        decision_trace["mobile_review_adjustment"] = adjustment
                        adjusted_decision_trace_json = _json_dump(decision_trace)

                token_updated = connection.execute(
                    """
                    UPDATE review_tokens
                    SET used_at = ?, last_used_at = ?
                    WHERE token_id = ?
                      AND review_task_id = ?
                      AND token_hash = ?
                      AND allowed_actions = ?
                      AND used_at IS NULL
                      AND revoked_at IS NULL
                      AND expires_at > ?
                    """,
                    (
                        _datetime_to_text(timestamp),
                        _datetime_to_text(timestamp),
                        token_row["token_id"],
                        review_task_id,
                        token_hash,
                        token_row["allowed_actions"],
                        _datetime_to_text(timestamp),
                    ),
                ).rowcount
                if token_updated != 1:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "复核请求发生并发更新，请重试",
                    )
                inject("after_token_update")

                review_updated = connection.execute(
                    """
                    UPDATE review_tasks
                    SET review_status = ?,
                        resolution_payload_json = ?,
                        updated_at = ?,
                        resolved_by = ?,
                        resolved_at = ?,
                        resolution_note = ?
                    WHERE review_task_id = ?
                      AND review_status = ?
                    """,
                    (
                        action,
                        _json_dump(payload),
                        _datetime_to_text(timestamp),
                        resolved_actor,
                        _datetime_to_text(timestamp),
                        note,
                        review_task_id,
                        ReviewTaskStatus.PENDING.value,
                    ),
                ).rowcount
                if review_updated != 1:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED,
                        "复核任务已被其他请求处理",
                    )
                self._cancel_review_outbox_on_connection(
                    connection,
                    review_task_id,
                    changed_at=timestamp,
                )
                inject("after_review_update")

                if source_row is not None and source_task_status is not None:
                    for grouped_source_row in source_rows:
                        grouped_task_id = str(grouped_source_row["task_id"])
                        current_source_status = TaskStatus(
                            str(grouped_source_row["task_status"])
                        )
                        if (
                            source_task_status != current_source_status
                            and source_task_status
                            not in ATOMIC_TASK_TRANSITIONS.get(
                                current_source_status,
                                set(),
                            )
                        ):
                            raise MobileReviewTransactionError(
                                MobileReviewErrorCode.CONCURRENT_UPDATE,
                                "任务组成员状态已变化，复核请求未提交",
                            )
                        task_updated = connection.execute(
                            """
                            UPDATE tasks
                            SET task_status = ?,
                                target_price = COALESCE(?, target_price),
                                target_status = COALESCE(?, target_status),
                                decision_trace_json = COALESCE(?, decision_trace_json),
                                required_by = COALESCE(?, required_by),
                                expires_at = COALESCE(?, expires_at),
                                result_message = COALESCE(NULLIF(?, ''), result_message),
                                updated_at = ?
                            WHERE task_id = ?
                              AND task_status = ?
                            """,
                            (
                                source_task_status.value,
                                adjusted_target_price,
                                adjusted_target_status,
                                adjusted_decision_trace_json,
                                _datetime_to_text(retry_required_by),
                                _datetime_to_text(retry_required_by),
                                adjusted_result_message,
                                _datetime_to_text(timestamp),
                                grouped_task_id,
                                current_source_status.value,
                            ),
                        ).rowcount
                        if task_updated != 1:
                            raise MobileReviewTransactionError(
                                MobileReviewErrorCode.CONCURRENT_UPDATE,
                                "任务组成员状态已变化，复核请求未提交",
                            )
                        inject("after_task_update")

                        history_metadata = {
                            "review_task_id": review_task_id,
                            "review_status": status.value,
                            "business_decision": business_decision,
                            "task_group_id": payload.get("task_group_id"),
                            "affected_task_count": len(source_rows),
                            "retry_required_by": (
                                retry_required_by.isoformat()
                                if retry_required_by is not None
                                else None
                            ),
                            "actor": resolved_actor,
                            "actor_source": actor_source,
                            "resolution_note": note,
                            "resolution_payload_summary": (
                                resolution_payload_summary(payload)
                            ),
                        }
                        history_id = uuid4().hex[:12]
                        inject("before_history_insert")
                        connection.execute(
                            """
                            INSERT INTO task_status_history(
                                history_id, task_id, from_status, to_status,
                                changed_by, changed_at, reason, metadata_json
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                history_id,
                                grouped_task_id,
                                current_source_status.value,
                                source_task_status.value,
                                resolved_actor,
                                _datetime_to_text(timestamp),
                                (
                                    f"review_task_group:{review_task_id}:"
                                    f"{business_decision}"
                                    if business_decision
                                    else f"review_task:{review_task_id}:{status.value}"
                                ),
                                _json_dump(history_metadata),
                            ),
                        )
                        inject("after_history_insert")

                committed_review_row = connection.execute(
                    "SELECT * FROM review_tasks WHERE review_task_id = ?",
                    (review_task_id,),
                ).fetchone()
                committed_token_row = connection.execute(
                    "SELECT * FROM review_tokens WHERE token_id = ?",
                    (token_row["token_id"],),
                ).fetchone()
                committed_source_row = (
                    connection.execute(
                        "SELECT * FROM tasks WHERE task_id = ?",
                        (source_task_id,),
                    ).fetchone()
                    if source_task_id
                    else None
                )
                if committed_review_row is None or committed_token_row is None:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "复核结果提交前读取失败",
                    )
                inject("before_result_conversion")
                result = MobileReviewAtomicResult(
                    review_task=_row_to_review_task(committed_review_row),
                    review_token=_row_to_review_token(committed_token_row),
                    source_task=_row_to_task(committed_source_row)
                    if committed_source_row is not None
                    else None,
                    source_task_status=source_task_status,
                )
                connection.commit()
                return result
            except MobileReviewTransactionError:
                connection.rollback()
                raise
            except sqlite3.OperationalError as exc:
                connection.rollback()
                if _is_sqlite_concurrency_error(exc):
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "复核请求发生并发更新，请重试",
                    ) from exc
                raise
            except Exception:
                connection.rollback()
                raise

    def _resolve_incident_mobile_review_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        review_model: ReviewTask,
        token_row: sqlite3.Row,
        status: ReviewTaskStatus,
        actor_source: str,
        actor: str | None,
        note: str,
        payload: dict[str, object],
        timestamp: datetime,
        emergency_base_cost: Decimal | None,
        emergency_base_cost_source_ref: str,
        emergency_product_snapshot_verifier: (
            Callable[[], tuple[Decimal, str]] | None
        ),
        inject: Callable[[str], None],
    ) -> MobileReviewAtomicResult:
        if status not in {
            ReviewTaskStatus.ADJUSTED,
            ReviewTaskStatus.APPROVED,
            ReviewTaskStatus.REJECTED,
        }:
            raise MobileReviewTransactionError(
                MobileReviewErrorCode.ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE,
                "紧急保护复核只允许选择改价、立即下架或我来处理。",
            )
        incident_id = str(review_model.review_payload.get("incident_id") or "").strip()
        if not incident_id:
            raise MobileReviewTransactionError(
                MobileReviewErrorCode.CONCURRENT_UPDATE,
                "关联异常记录不存在或已变化，请刷新后重试",
            )
        incident_row = connection.execute(
            "SELECT * FROM operational_incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if (
            incident_row is None
            or str(incident_row["incident_status"])
            not in {
                IncidentStatus.WAITING_HUMAN.value,
                IncidentStatus.AUTO_PROTECTING.value,
            }
        ):
            raise MobileReviewTransactionError(
                MobileReviewErrorCode.CONCURRENT_UPDATE,
                "关联异常记录不存在或已变化，请刷新后重试",
            )
        if str(incident_row["severity"]) not in {"S3", "S4"}:
            raise MobileReviewTransactionError(
                MobileReviewErrorCode.CONCURRENT_UPDATE,
                "异常等级已变化，请刷新后重试",
            )
        emergency_task_row = connection.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE origin_type = 'SYSTEM_EMERGENCY'
              AND json_extract(decision_trace_json, '$.incident_id') = ?
            ORDER BY created_at DESC, task_id DESC
            LIMIT 1
            """,
            (incident_id,),
        ).fetchone()
        worker_final_click_fence_won = bool(
            emergency_task_row is not None
            and has_emergency_final_click_fence_won(
                connection,
                incident_id=incident_id,
                source_task_id=str(emergency_task_row["task_id"]),
            )
        )
        internal_sku = str(review_model.internal_sku or "").strip()
        platform_name = str(review_model.platform_name or "").strip()
        if not internal_sku or not platform_name:
            raise MobileReviewTransactionError(
                MobileReviewErrorCode.CONCURRENT_UPDATE,
                "复核缺少商品或平台身份，请刷新后重试",
            )

        task: Task | None = None
        decision = "human_handling"
        if (
            status in {ReviewTaskStatus.ADJUSTED, ReviewTaskStatus.APPROVED}
            and not worker_final_click_fence_won
        ):
            if (
                emergency_base_cost is None
                or not emergency_base_cost.is_finite()
                or emergency_base_cost <= 0
                or not emergency_base_cost_source_ref.strip()
            ):
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "商品基础成本不可用，已阻止创建平台任务",
                )
            if emergency_product_snapshot_verifier is None:
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "商品主数据提交校验不可用，已阻止创建平台任务",
                )
            locked_base_cost, locked_source_ref = (
                emergency_product_snapshot_verifier()
            )
            if (
                locked_base_cost != emergency_base_cost
                or locked_source_ref != emergency_base_cost_source_ref
            ):
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "商品主数据在复核提交前发生变化，请重试",
                )
            listing_rows = connection.execute(
                """
                SELECT * FROM listing_status
                WHERE platform_name = ? AND internal_sku = ?
                ORDER BY price_observed_at DESC, updated_at DESC, listing_status_id DESC
                """,
                (platform_name, internal_sku),
            ).fetchall()
            if not listing_rows:
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "当前平台价格事实不可用，已阻止创建平台任务",
                )
            listing_row = listing_rows[0]
            try:
                expected_old_price = Decimal(str(listing_row["current_price"]))
            except (ArithmeticError, ValueError) as exc:
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "当前平台价格事实不可用，已阻止创建平台任务",
                ) from exc
            if not expected_old_price.is_finite() or expected_old_price <= 0:
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "当前平台价格事实不可用，已阻止创建平台任务",
                )
            collision = connection.execute(
                """
                SELECT task_id FROM tasks
                WHERE platform_name = ? AND internal_sku = ?
                  AND origin_type = 'MANUAL'
                  AND action_type IN ('update_price', 'set_offline')
                  AND task_status IN ('pending', 'running', 'manual_review')
                LIMIT 1
                """,
                (platform_name, internal_sku),
            ).fetchone()
            if collision is not None:
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "该商品已有待执行的人工改价或下架任务，请先处理现有任务",
                )

            target_price: Decimal | None = None
            if status is ReviewTaskStatus.ADJUSTED:
                if str(listing_row["online_status"]).strip().lower() != "online":
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.CONCURRENT_UPDATE,
                        "商品当前不在上架中，已阻止创建改价任务",
                    )
                adjustment = payload.get("adjustment")
                if not isinstance(adjustment, dict) or "target_price" not in adjustment:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.INVALID_ADJUSTMENT,
                        "改价操作必须输入目标价格",
                    )
                if "target_status" in adjustment:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.INVALID_ADJUSTMENT,
                        "改价操作不能同时修改上下架状态",
                    )
                target_price = Decimal(str(adjustment["target_price"]))
                if target_price < emergency_base_cost:
                    raise MobileReviewTransactionError(
                        MobileReviewErrorCode.INVALID_ADJUSTMENT,
                        "目标价格不得低于商品基础成本",
                    )
                action_type = TaskActionType.UPDATE_PRICE
                target_status = None
                pricing_source = PricingSource.MANUAL_OVERRIDE
                decision = "manual_update_price"
            else:
                action_type = TaskActionType.SET_OFFLINE
                target_status = "offline"
                pricing_source = None
                decision = "manual_set_offline"

            task_identity = (
                f"incident-review-task:{review_model.review_task_id}:{status.value}"
            )
            task_id = (
                f"task-{hashlib.sha256(task_identity.encode('utf-8')).hexdigest()[:24]}"
            )
            task = Task(
                task_id=task_id,
                internal_sku=internal_sku,
                platform_name=platform_name,
                action_type=action_type,
                priority=0,
                task_status=TaskStatus.PENDING,
                created_at=timestamp,
                origin_type=TaskOriginType.MANUAL,
                origin_ref_id=f"incident-review:{review_model.review_task_id}",
                expected_old_price=expected_old_price,
                target_price=target_price,
                target_status=target_status,
                pricing_source=pricing_source,
                decision_trace={
                    "incident_id": incident_id,
                    "review_task_id": review_model.review_task_id,
                    "review_status": status.value,
                    "base_cost": serialize_decimal(emergency_base_cost),
                    "base_cost_source_ref": emergency_base_cost_source_ref,
                    "listing_status_id": str(listing_row["listing_status_id"]),
                    "price_source_attempt_id": str(
                        listing_row["price_source_attempt_id"]
                    ),
                    "actor_source": actor_source,
                },
                result_message=(
                    str(payload.get("adjustment", {}).get("result_message") or note)
                    if isinstance(payload.get("adjustment"), dict)
                    else note
                ),
                required_by=review_model.required_by,
                trade_date=review_model.trade_date,
                approval_policy="MOBILE_REVIEW",
                policy_version="incident-review-v1",
                platform_trade_date=_text_to_date(incident_row["platform_trade_date"]),
                seller_operation_date=_text_to_date(
                    incident_row["seller_operation_date"]
                ),
                scope_type="sku",
                scope_key=internal_sku,
                dedupe_key=task_identity,
                expires_at=review_model.required_by,
                updated_at=timestamp,
            )
            self._validate_tasks_for_insert([task])
            if self._insert_tasks_on_connection(connection, [task]) != 1:
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "人工任务已存在或发生并发更新，请刷新后重试",
                )
            inject("after_incident_task_insert")

        if worker_final_click_fence_won:
            payload["requested_decision"] = {
                ReviewTaskStatus.ADJUSTED: "manual_update_price",
                ReviewTaskStatus.APPROVED: "manual_set_offline",
                ReviewTaskStatus.REJECTED: "human_handling",
            }[status]
            decision = "late_after_emergency_final_click_fence"
            payload["platform_side_effect_prevented"] = False
            payload["awaiting_emergency_result_import"] = True
            payload["automatic_task_id"] = str(emergency_task_row["task_id"])
        payload["decision"] = decision
        payload["incident_id"] = incident_id
        if task is not None:
            payload["created_task_id"] = task.task_id
        resolved_actor = actor or str(token_row["token_subject"])
        token_updated = connection.execute(
            """
            UPDATE review_tokens
            SET used_at = ?, last_used_at = ?
            WHERE token_id = ? AND review_task_id = ? AND used_at IS NULL
              AND revoked_at IS NULL AND expires_at > ?
            """,
            (
                _datetime_to_text(timestamp),
                _datetime_to_text(timestamp),
                token_row["token_id"],
                review_model.review_task_id,
                _datetime_to_text(timestamp),
            ),
        ).rowcount
        if token_updated != 1:
            raise MobileReviewTransactionError(
                MobileReviewErrorCode.CONCURRENT_UPDATE,
                "复核请求发生并发更新，请重试",
            )
        inject("after_token_update")
        review_updated = connection.execute(
            """
            UPDATE review_tasks
            SET review_status = ?, resolution_payload_json = ?, updated_at = ?,
                resolved_by = ?, resolved_at = ?, resolution_note = ?
            WHERE review_task_id = ? AND review_status = ?
            """,
            (
                status.value,
                _json_dump(payload),
                _datetime_to_text(timestamp),
                resolved_actor,
                _datetime_to_text(timestamp),
                note,
                review_model.review_task_id,
                ReviewTaskStatus.PENDING.value,
            ),
        ).rowcount
        if review_updated != 1:
            raise MobileReviewTransactionError(
                MobileReviewErrorCode.REVIEW_ALREADY_RESOLVED,
                "链接已失效或无权访问该复核任务",
            )
        inject("after_review_update")
        self._cancel_review_outbox_on_connection(
            connection,
            review_model.review_task_id,
            changed_at=timestamp,
        )

        incident_from_status = str(incident_row["incident_status"])
        if (
            incident_from_status == IncidentStatus.AUTO_PROTECTING.value
            and not worker_final_click_fence_won
        ):
            cancelled = connection.execute(
                """
                UPDATE tasks
                SET task_status = 'cancelled', result_message = ?, updated_at = ?
                WHERE origin_type = 'SYSTEM_EMERGENCY'
                  AND task_status IN ('pending', 'running')
                  AND json_extract(decision_trace_json, '$.incident_id') = ?
                """,
                (
                    EMERGENCY_HUMAN_PREEMPTED_TASK_MESSAGE,
                    _datetime_to_text(timestamp),
                    incident_id,
                ),
            ).rowcount
            if cancelled != 1:
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "自动紧急下架状态已变化，请刷新后重试",
                )
            transitioned = connection.execute(
                """
                UPDATE operational_incidents
                SET incident_status = 'WAITING_HUMAN', updated_at = ?
                WHERE incident_id = ? AND incident_status = 'AUTO_PROTECTING'
                """,
                (_datetime_to_text(timestamp), incident_id),
            ).rowcount
            if transitioned != 1:
                raise MobileReviewTransactionError(
                    MobileReviewErrorCode.CONCURRENT_UPDATE,
                    "关联异常记录已变化，请刷新后重试",
                )
        review_event_key = f"incident-review-resolution:{review_model.review_task_id}"
        event_id = f"incident-event-{hashlib.sha256(review_event_key.encode('utf-8')).hexdigest()[:24]}"
        connection.execute(
            """
            INSERT INTO operational_incident_events(
                event_id, event_key, incident_id, event_type, occurred_at,
                source_type, source_ref_id, from_status, to_status, severity,
                event_payload_json, created_at
            ) VALUES(?, ?, ?, 'REVIEW_RECORDED', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                review_event_key,
                incident_id,
                _datetime_to_text(timestamp),
                "MOBILE_REVIEW",
                review_model.review_task_id,
                incident_from_status,
                (
                    incident_from_status
                    if worker_final_click_fence_won
                    else IncidentStatus.WAITING_HUMAN.value
                ),
                str(incident_row["severity"]),
                _json_dump(
                    {
                        "review_task_id": review_model.review_task_id,
                        "review_status": status.value,
                        "decision": decision,
                        "created_task_id": task.task_id if task is not None else None,
                        "platform_side_effect_prevented": (
                            False if worker_final_click_fence_won else True
                        ),
                    }
                ),
                _datetime_to_text(timestamp),
            ),
        )
        if task is not None:
            task_event_key = f"incident-task:{task.task_id}"
            task_event_id = f"incident-event-{hashlib.sha256(task_event_key.encode('utf-8')).hexdigest()[:24]}"
            connection.execute(
                """
                INSERT INTO operational_incident_events(
                    event_id, event_key, incident_id, event_type, occurred_at,
                    source_type, source_ref_id, from_status, to_status, severity,
                    event_payload_json, created_at
                ) VALUES(?, ?, ?, 'TASK_RECORDED', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_event_id,
                    task_event_key,
                    incident_id,
                    _datetime_to_text(timestamp),
                    "MOBILE_REVIEW",
                    task.task_id,
                    incident_from_status,
                    IncidentStatus.WAITING_HUMAN.value,
                    str(incident_row["severity"]),
                    _json_dump(
                        {
                            "task_id": task.task_id,
                            "action_type": task.action_type.value,
                            "origin_type": task.origin_type.value,
                        }
                    ),
                    _datetime_to_text(timestamp),
                ),
            )
        connection.execute(
            """
            UPDATE operational_incidents
            SET updated_at = CASE
                WHEN julianday(updated_at) < julianday(?) THEN ?
                ELSE updated_at
            END
            WHERE incident_id = ?
            """,
            (
                _datetime_to_text(timestamp),
                _datetime_to_text(timestamp),
                incident_id,
            ),
        )
        inject("after_incident_review_resolution")

        committed_review_row = connection.execute(
            "SELECT * FROM review_tasks WHERE review_task_id = ?",
            (review_model.review_task_id,),
        ).fetchone()
        committed_token_row = connection.execute(
            "SELECT * FROM review_tokens WHERE token_id = ?",
            (token_row["token_id"],),
        ).fetchone()
        committed_task_row = (
            connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task.task_id,),
            ).fetchone()
            if task is not None
            else None
        )
        return MobileReviewAtomicResult(
            review_task=_row_to_review_task(committed_review_row),
            review_token=_row_to_review_token(committed_token_row),
            source_task=(
                _row_to_task(committed_task_row)
                if committed_task_row is not None
                else None
            ),
            source_task_status=task.task_status if task is not None else None,
        )

    def insert_review_token(self, review_token: ReviewToken) -> int:
        row = _review_token_to_row(review_token)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO review_tokens(
                    token_id, review_task_id, token_hash, token_subject, allowed_actions,
                    expires_at, used_at, revoked_at, created_at, created_by, last_used_at, note
                )
                VALUES(
                    :token_id, :review_task_id, :token_hash, :token_subject, :allowed_actions,
                    :expires_at, :used_at, :revoked_at, :created_at, :created_by, :last_used_at, :note
                )
                """,
                row,
            )
            return connection.total_changes - before

    def get_review_token(self, token_id: str) -> ReviewToken | None:
        with closing(self.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM review_tokens WHERE token_id = ?",
                (token_id,),
            ).fetchone()
        return _row_to_review_token(row) if row is not None else None

    def get_review_token_by_hash(self, token_hash: str) -> ReviewToken | None:
        with closing(self.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM review_tokens WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return _row_to_review_token(row) if row is not None else None

    def list_review_tokens_by_review_task_id(
        self, review_task_id: str
    ) -> list[ReviewToken]:
        with closing(self.connect_read()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM review_tokens
                WHERE review_task_id = ?
                ORDER BY created_at ASC, token_id ASC
                """,
                (review_task_id,),
            ).fetchall()
        return [_row_to_review_token(row) for row in rows]

    def update_review_token_usage(
        self,
        token_id: str,
        *,
        used_at: datetime | None = None,
        last_used_at: datetime | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[str] = []
        if used_at is not None:
            assignments.append("used_at = ?")
            params.append(_datetime_to_text(used_at) or "")
        if last_used_at is not None:
            assignments.append("last_used_at = ?")
            params.append(_datetime_to_text(last_used_at) or "")
        if not assignments:
            return
        params.append(token_id)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE review_tokens SET {', '.join(assignments)} WHERE token_id = ?",
                params,
            )

    def revoke_review_token(self, token_id: str, revoked_at: datetime) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "UPDATE review_tokens SET revoked_at = ? WHERE token_id = ?",
                (_datetime_to_text(revoked_at), token_id),
            )

    def revoke_review_tokens_by_review_task_id(
        self, review_task_id: str, revoked_at: datetime
    ) -> int:
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                UPDATE review_tokens
                SET revoked_at = ?
                WHERE review_task_id = ? AND revoked_at IS NULL
                """,
                (_datetime_to_text(revoked_at), review_task_id),
            )
            return connection.total_changes - before

    def insert_execution_logs(self, logs: Iterable[ExecutionLog]) -> int:
        rows = [_execution_log_to_row(log) for log in logs]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO execution_logs(
                    log_id, task_id, executor_name, start_time, end_time, success_flag,
                    error_code, error_message, raw_output, ai_model_version, ai_summary, created_at
                )
                VALUES(
                    :log_id, :task_id, :executor_name, :start_time, :end_time, :success_flag,
                    :error_code, :error_message, :raw_output, :ai_model_version, :ai_summary, :created_at
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def list_execution_logs(
        self,
        *,
        task_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ExecutionLog]:
        query = "SELECT * FROM execution_logs"
        params: list[object] = []
        if task_id:
            query = f"{query} WHERE task_id = ?"
            params.append(task_id)
        query = f"{query} ORDER BY created_at DESC, log_id ASC"
        query, params = _with_limit_offset(query, params, limit=limit, offset=offset)
        with closing(self.connect_read()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_execution_log(row) for row in rows]

    def get_execution_log(self, log_id: str) -> ExecutionLog | None:
        with closing(self.connect_read()) as connection:
            row = connection.execute(
                "SELECT * FROM execution_logs WHERE log_id = ?",
                (str(log_id).strip(),),
            ).fetchone()
        return _row_to_execution_log(row) if row is not None else None

    def insert_shadowbot_operation(self, operation: ShadowBotOperationLedger) -> int:
        row = _shadowbot_operation_to_row(operation)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO shadowbot_operations(
                    operation_id, task_id, platform, product_identity_json, expected_old_price,
                    target_price, status, lock_owner, approved_payload_hash, created_at, updated_at
                )
                VALUES(
                    :operation_id, :task_id, :platform, :product_identity_json, :expected_old_price,
                    :target_price, :status, :lock_owner, :approved_payload_hash, :created_at, :updated_at
                )
                """,
                row,
            )
            return connection.total_changes - before

    def get_shadowbot_operation(
        self, operation_id: str
    ) -> ShadowBotOperationLedger | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM shadowbot_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        return _row_to_shadowbot_operation(row) if row is not None else None

    def acquire_shadowbot_operation_lock(
        self, operation_id: str, lock_owner: str
    ) -> bool:
        with closing(self.connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE shadowbot_operations
                SET lock_owner = ?, updated_at = ?
                WHERE operation_id = ? AND (lock_owner = '' OR lock_owner = ?)
                """,
                (
                    lock_owner,
                    _datetime_to_text(datetime.now()),
                    operation_id,
                    lock_owner,
                ),
            )
            return cursor.rowcount == 1

    def update_shadowbot_operation_status(
        self, operation_id: str, status: str, *, lock_owner: str | None = None
    ) -> None:
        assignments = ["status = ?", "updated_at = ?"]
        params: list[object] = [status, _datetime_to_text(datetime.now())]
        if lock_owner is not None:
            assignments.append("lock_owner = ?")
            params.append(lock_owner)
        params.append(operation_id)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE shadowbot_operations SET {', '.join(assignments)} WHERE operation_id = ?",
                params,
            )

    def release_shadowbot_write_lock(
        self, operation_id: str, *, released_at: datetime
    ) -> bool:
        timestamp = _datetime_to_text(released_at)
        with closing(self.connect_write()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE shadowbot_write_locks
                SET status = 'RELEASED', released_at = ?, updated_at = ?
                WHERE operation_id = ? AND status IN ('ACTIVE', 'UNKNOWN')
                """,
                (timestamp, timestamp, operation_id),
            )
        return cursor.rowcount == 1

    def insert_shadowbot_execution_attempt(
        self, attempt: ShadowBotExecutionAttempt
    ) -> int:
        row = _shadowbot_attempt_to_row(attempt)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode, shadowbot_run_id,
                    status, side_effect_state, started_at, instruction_hash,
                    request_file_sha256, queue_request_path, ended_at, raw_output_json
                )
                VALUES(
                    :execution_attempt_id, :operation_id, :execution_mode, :shadowbot_run_id,
                    :status, :side_effect_state, :started_at, :instruction_hash,
                    :request_file_sha256, :queue_request_path, :ended_at, :raw_output_json
                )
                """,
                row,
            )
            return connection.total_changes - before

    def get_shadowbot_execution_attempt(
        self, execution_attempt_id: str
    ) -> ShadowBotExecutionAttempt | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (execution_attempt_id,),
            ).fetchone()
        return _row_to_shadowbot_attempt(row) if row is not None else None

    def list_shadowbot_execution_attempts(
        self, *, operation_id: str
    ) -> list[ShadowBotExecutionAttempt]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM shadowbot_execution_attempts
                WHERE operation_id = ?
                ORDER BY started_at, execution_attempt_id
                """,
                (operation_id,),
            ).fetchall()
        return [_row_to_shadowbot_attempt(row) for row in rows]

    def list_shadowbot_listing_action_task_projection(
        self,
        *,
        task_id: str,
    ) -> list[dict[str, object]]:
        """Return the read-only v5 listing execution projection for one task."""

        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT
                    item.source_task_id,
                    batch.action_type,
                    batch.batch_id,
                    batch.execution_attempt_id AS batch_execution_attempt_id,
                    batch.status AS batch_status,
                    item.operation_id,
                    item.item_execution_attempt_id,
                    item.internal_sku,
                    item.expected_old_status,
                    item.target_status,
                    item.target_price,
                    item.target_inventory,
                    item.detail_effect_state,
                    item.listing_effect_state,
                    item.operation_result,
                    item.observed_price_before_action,
                    item.observed_inventory_before_action,
                    item.observed_price_after_detail_save,
                    item.observed_inventory_after_detail_save,
                    item.action_clicked_at,
                    item.readback_observed_at,
                    item.error_code,
                    item.error_message,
                    operation.status AS operation_status
                FROM shadowbot_listing_action_batch_items AS item
                INNER JOIN shadowbot_listing_action_batches AS batch
                    ON batch.batch_id = item.batch_id
                INNER JOIN shadowbot_operations AS operation
                    ON operation.operation_id = item.operation_id
                WHERE item.source_task_id = ?
                ORDER BY batch.created_at, item.item_id
                """,
                (task_id,),
            ).fetchall()
            projections: list[dict[str, object]] = []
            for row in rows:
                projection = dict(row)
                attempts = connection.execute(
                    """
                    SELECT execution_attempt_id, execution_mode, status,
                           side_effect_state, started_at, ended_at
                    FROM shadowbot_execution_attempts
                    WHERE operation_id = ?
                    ORDER BY started_at, execution_attempt_id
                    """,
                    (row["operation_id"],),
                ).fetchall()
                projection["attempts"] = [dict(attempt) for attempt in attempts]
                projections.append(projection)
        return projections

    def list_active_shadowbot_execution_attempts(
        self,
    ) -> list[ShadowBotExecutionAttempt]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM shadowbot_execution_attempts
                WHERE status IN ('STARTING', 'RUNNING')
                ORDER BY operation_id, started_at, execution_attempt_id
                """
            ).fetchall()
        return [_row_to_shadowbot_attempt(row) for row in rows]

    def freeze_duplicate_active_commit_attempts(
        self, operation_id: str, *, now: datetime
    ) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT' AND status IN ('STARTING', 'RUNNING')
                ORDER BY started_at, execution_attempt_id
                """,
                (operation_id,),
            ).fetchall()
            if len(rows) < 2:
                connection.rollback()
                return False
            for row in rows:
                raw = _json_load(row["raw_output_json"])
                raw["frozen_reason"] = "DUPLICATE_ACTIVE_COMMIT_ATTEMPT"
                raw["frozen_at"] = _datetime_to_text(now)
                lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
                lease["active"] = False
                lease["frozen_at"] = _datetime_to_text(now)
                raw["lease"] = lease
                checkpoint = connection.execute(
                    """
                    SELECT side_effect_state FROM shadowbot_side_effect_checkpoints
                    WHERE execution_attempt_id = ? ORDER BY version DESC LIMIT 1
                    """,
                    (str(row["execution_attempt_id"]),),
                ).fetchone()
                observed_side_effect = (
                    str(checkpoint["side_effect_state"])
                    if checkpoint is not None
                    else str(row["side_effect_state"])
                )
                start_unknown = (
                    str(row["status"]) == "STARTING"
                    and observed_side_effect == "NOT_STARTED"
                )
                attempt_status = (
                    "START_UNKNOWN" if start_unknown else "SIDE_EFFECT_UNKNOWN"
                )
                terminal_side_effect = "NOT_STARTED" if start_unknown else "UNKNOWN"
                connection.execute(
                    """
                    UPDATE shadowbot_execution_attempts
                    SET status = ?, side_effect_state = ?, ended_at = ?, raw_output_json = ?
                    WHERE execution_attempt_id = ? AND status IN ('STARTING', 'RUNNING')
                    """,
                    (
                        attempt_status,
                        terminal_side_effect,
                        _datetime_to_text(now),
                        _json_dump(raw),
                        str(row["execution_attempt_id"]),
                    ),
                )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = 'MANUAL_REVIEW', lock_owner = '', updated_at = ?
                WHERE operation_id = ?
                """,
                (_datetime_to_text(now), operation_id),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def create_shadowbot_attempt_with_lease(
        self,
        attempt: ShadowBotExecutionAttempt,
        *,
        owner_token: str,
        lease_expires_at: datetime,
        expected_operation_statuses: Iterable[str],
    ) -> ShadowBotExecutionAttempt | None:
        """Atomically bind a fresh attempt, lease and RUNNING operation."""
        expected = tuple(
            dict.fromkeys(str(value) for value in expected_operation_statuses)
        )
        if not expected:
            raise ValueError("expected_operation_statuses must not be empty")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT status, lock_owner FROM shadowbot_operations WHERE operation_id = ?",
                (attempt.operation_id,),
            ).fetchone()
            if (
                operation is None
                or str(operation["status"]) not in expected
                or str(operation["lock_owner"] or "")
            ):
                connection.rollback()
                return None
            active = connection.execute(
                """
                SELECT 1 FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT' AND status IN ('STARTING', 'RUNNING')
                LIMIT 1
                """,
                (attempt.operation_id,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                return None
            lease_version = self._next_shadowbot_lease_version(
                connection, attempt.operation_id
            )
            attempt.raw_output = {
                **attempt.raw_output,
                "operation_status_before_attempt": str(operation["status"]),
                "lease": {
                    "owner_token": owner_token,
                    "version": lease_version,
                    "expires_at": _datetime_to_text(lease_expires_at),
                    "active": True,
                },
            }
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode, shadowbot_run_id,
                    status, side_effect_state, started_at, instruction_hash,
                    request_file_sha256, queue_request_path, ended_at, raw_output_json
                ) VALUES(
                    :execution_attempt_id, :operation_id, :execution_mode, :shadowbot_run_id,
                    :status, :side_effect_state, :started_at, :instruction_hash,
                    :request_file_sha256, :queue_request_path, :ended_at, :raw_output_json
                )
                """,
                _shadowbot_attempt_to_row(attempt),
            )
            placeholders = ",".join("?" for _ in expected)
            cursor = connection.execute(
                f"""
                UPDATE shadowbot_operations
                SET status = 'RUNNING', lock_owner = ?, updated_at = ?
                WHERE operation_id = ? AND status IN ({placeholders}) AND lock_owner = ''
                """,
                (
                    owner_token,
                    _datetime_to_text(datetime.now()),
                    attempt.operation_id,
                    *expected,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            return attempt
        except sqlite3.IntegrityError:
            connection.rollback()
            return None
        finally:
            connection.close()

    def mark_shadowbot_start_outcome(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        attempt_status: str,
        operation_status: str,
        shadowbot_run_id: str = "",
        instruction_hash: str = "",
        request_file_sha256: str = "",
        queue_request_path: str = "",
        raw_output: dict[str, Any] | None = None,
        ended_at: datetime | None = None,
    ) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT a.*, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ?
                """,
                (execution_attempt_id,),
            ).fetchone()
            if row is None or str(row["lock_owner"] or "") != owner_token:
                connection.rollback()
                return False
            current_raw = _json_load(row["raw_output_json"])
            lease = (
                current_raw.get("lease")
                if isinstance(current_raw.get("lease"), dict)
                else {}
            )
            lease_expires_at = _text_to_datetime(lease.get("expires_at"))
            lease_now = (
                datetime.now(lease_expires_at.tzinfo)
                if lease_expires_at is not None
                else None
            )
            if (
                str(lease.get("owner_token") or "") != owner_token
                or int(lease.get("version") or 0) != lease_version
                or not bool(lease.get("active", False))
                or lease_expires_at is None
                or lease_now is None
                or lease_expires_at <= lease_now
            ):
                connection.rollback()
                return False
            merged_raw = {**current_raw, **(raw_output or {})}
            merged_lease = dict(lease)
            terminal = attempt_status not in {"STARTING", "RUNNING"}
            if terminal:
                merged_lease["active"] = False
                merged_lease["ended_at"] = _datetime_to_text(ended_at or datetime.now())
            merged_raw["lease"] = merged_lease
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET shadowbot_run_id = ?, status = ?, instruction_hash = ?,
                    request_file_sha256 = ?, queue_request_path = ?, ended_at = ?, raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    shadowbot_run_id,
                    attempt_status,
                    instruction_hash,
                    request_file_sha256,
                    queue_request_path,
                    _datetime_to_text(ended_at),
                    _json_dump(merged_raw),
                    execution_attempt_id,
                ),
            )

            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, lock_owner = ?, updated_at = ?
                WHERE operation_id = ? AND lock_owner = ?
                """,
                (
                    operation_status,
                    "" if terminal else owner_token,
                    _datetime_to_text(datetime.now()),
                    str(row["operation_id"]),
                    owner_token,
                ),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def validate_shadowbot_lease(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        now: datetime,
    ) -> bool:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT a.raw_output_json, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ?
                """,
                (execution_attempt_id,),
            ).fetchone()
        if row is None or str(row["lock_owner"] or "") != owner_token:
            return False
        raw = _json_load(row["raw_output_json"])
        lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
        expires_at = _text_to_datetime(lease.get("expires_at"))
        return bool(
            lease.get("active", False)
            and str(lease.get("owner_token") or "") == owner_token
            and int(lease.get("version") or 0) == lease_version
            and expires_at is not None
            and expires_at > now
        )

    def complete_shadowbot_attempt_with_lease(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        attempt_status: str,
        operation_status: str,
        side_effect_state: str,
        ended_at: datetime,
        raw_output: dict[str, Any],
    ) -> bool:
        """Fence result writeback with owner/version and close lease atomically."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT a.*, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ? AND a.status IN ('STARTING', 'RUNNING')
                """,
                (execution_attempt_id,),
            ).fetchone()
            if row is None or str(row["lock_owner"] or "") != owner_token:
                connection.rollback()
                return False
            current_raw = _json_load(row["raw_output_json"])
            lease = (
                current_raw.get("lease")
                if isinstance(current_raw.get("lease"), dict)
                else {}
            )
            expires_at = _text_to_datetime(lease.get("expires_at"))
            if (
                not bool(lease.get("active", False))
                or str(lease.get("owner_token") or "") != owner_token
                or int(lease.get("version") or 0) != lease_version
                or expires_at is None
                or expires_at <= ended_at
            ):
                connection.rollback()
                return False
            lease["active"] = False
            lease["ended_at"] = _datetime_to_text(ended_at)
            merged_raw = {**current_raw, **raw_output, "lease": lease}
            next_version = connection.execute(
                """
                SELECT COALESCE(MAX(version), 0) + 1 AS next_version
                FROM shadowbot_side_effect_checkpoints WHERE operation_id = ?
                """,
                (str(row["operation_id"]),),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO shadowbot_side_effect_checkpoints(
                    operation_id, execution_attempt_id, side_effect_state, checkpoint_at, version
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(row["operation_id"]),
                    execution_attempt_id,
                    side_effect_state,
                    _datetime_to_text(ended_at),
                    int(next_version["next_version"]),
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = ?, side_effect_state = ?, ended_at = ?, raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    attempt_status,
                    side_effect_state,
                    _datetime_to_text(ended_at),
                    _json_dump(merged_raw),
                    execution_attempt_id,
                ),
            )
            updated = connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, lock_owner = '', updated_at = ?
                WHERE operation_id = ? AND lock_owner = ?
                """,
                (
                    operation_status,
                    _datetime_to_text(ended_at),
                    str(row["operation_id"]),
                    owner_token,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        finally:
            connection.close()

    def renew_shadowbot_lease(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        lease_seconds: int = 900,
    ) -> bool:
        """Renew a lease with bounded retries for this database-only operation."""

        return self._run_sqlite_retry(
            lambda: self._renew_shadowbot_lease_once(
                execution_attempt_id,
                owner_token=owner_token,
                lease_version=lease_version,
                lease_seconds=lease_seconds,
            ),
            operation_name="renew ShadowBot lease",
        )

    def _renew_shadowbot_lease_once(
        self,
        execution_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        lease_seconds: int,
    ) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            locked_now = self._clock()
            renewed_expires_at = locked_now + timedelta(
                seconds=max(int(lease_seconds), 1)
            )
            row = connection.execute(
                """
                SELECT a.raw_output_json, a.operation_id, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ? AND a.status IN ('STARTING', 'RUNNING')
                """,
                (execution_attempt_id,),
            ).fetchone()
            if row is None or str(row["lock_owner"] or "") != owner_token:
                connection.rollback()
                return False
            raw = _json_load(row["raw_output_json"])
            lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
            current_expires = _text_to_datetime(lease.get("expires_at"))
            if (
                not bool(lease.get("active", False))
                or str(lease.get("owner_token") or "") != owner_token
                or int(lease.get("version") or 0) != lease_version
                or current_expires is None
                or current_expires <= locked_now
            ):
                connection.rollback()
                return False
            lease["expires_at"] = _datetime_to_text(renewed_expires_at)
            raw["lease"] = lease
            cursor = connection.execute(
                "UPDATE shadowbot_execution_attempts SET raw_output_json = ? WHERE execution_attempt_id = ?",
                (_json_dump(raw), execution_attempt_id),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def expire_shadowbot_lease(
        self, execution_attempt_id: str, *, now: datetime
    ) -> bool:
        """F10: fence a stale owner and move the operation to reconciliation."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT a.*, o.lock_owner
                FROM shadowbot_execution_attempts a
                JOIN shadowbot_operations o ON o.operation_id = a.operation_id
                WHERE a.execution_attempt_id = ? AND a.status IN ('STARTING', 'RUNNING')
                """,
                (execution_attempt_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            raw = _json_load(row["raw_output_json"])
            lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
            expires_at = _text_to_datetime(lease.get("expires_at"))
            if (
                not bool(lease.get("active", False))
                or expires_at is None
                or expires_at > now
            ):
                connection.rollback()
                return False
            if str(row["lock_owner"] or "") != str(lease.get("owner_token") or ""):
                connection.rollback()
                return False
            lease["active"] = False
            lease["expired_at"] = _datetime_to_text(now)
            raw["lease"] = lease
            attempt_status = (
                "START_UNKNOWN"
                if str(row["status"]) == "STARTING"
                else "SIDE_EFFECT_UNKNOWN"
            )
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET status = ?, side_effect_state = 'UNKNOWN', ended_at = ?, raw_output_json = ?
                WHERE execution_attempt_id = ?
                """,
                (
                    attempt_status,
                    _datetime_to_text(now),
                    _json_dump(raw),
                    execution_attempt_id,
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = 'NEEDS_RECONCILIATION', lock_owner = '', updated_at = ?
                WHERE operation_id = ? AND lock_owner = ?
                """,
                (
                    _datetime_to_text(now),
                    str(row["operation_id"]),
                    str(lease.get("owner_token") or ""),
                ),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def quarantine_shadowbot_attempt(
        self,
        execution_attempt_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> bool:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (execution_attempt_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            raw = _json_load(row["raw_output_json"])
            lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
            lease["active"] = False
            lease["quarantined_at"] = _datetime_to_text(now)
            raw["lease"] = lease
            raw["quarantine_reason"] = reason
            terminal = str(row["status"]) not in {"STARTING", "RUNNING"}
            operation_status = "MANUAL_REVIEW" if terminal else "NEEDS_RECONCILIATION"
            if terminal:
                connection.execute(
                    "UPDATE shadowbot_execution_attempts SET raw_output_json = ? WHERE execution_attempt_id = ?",
                    (_json_dump(raw), execution_attempt_id),
                )
            else:
                side_effect = str(row["side_effect_state"])
                attempt_status = (
                    "START_UNKNOWN"
                    if side_effect == "NOT_STARTED"
                    else "SIDE_EFFECT_UNKNOWN"
                )
                connection.execute(
                    """
                    UPDATE shadowbot_execution_attempts
                    SET status = ?, side_effect_state = ?, ended_at = ?, raw_output_json = ?
                    WHERE execution_attempt_id = ?
                    """,
                    (
                        attempt_status,
                        "UNKNOWN"
                        if attempt_status == "SIDE_EFFECT_UNKNOWN"
                        else side_effect,
                        _datetime_to_text(now),
                        _json_dump(raw),
                        execution_attempt_id,
                    ),
                )
            connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = ?, lock_owner = '', updated_at = ? WHERE operation_id = ?
                """,
                (operation_status, _datetime_to_text(now), str(row["operation_id"])),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def quarantine_shadowbot_commit_batch(
        self,
        batch_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> bool:
        """Freeze every v4 child ledger when its batch result is untrusted."""

        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            batch = connection.execute(
                "SELECT * FROM shadowbot_commit_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
            if batch is None:
                connection.rollback()
                return False
            accepted_receipt = connection.execute(
                """
                SELECT 1 FROM shadowbot_commit_result_receipts
                WHERE batch_id = ?
                LIMIT 1
                """,
                (batch_id,),
            ).fetchone()
            if accepted_receipt is not None:
                # A later malformed/conflicting file is evidence to quarantine,
                # but it cannot roll an already accepted business projection
                # back to UNKNOWN.
                connection.rollback()
                return False
            now_text = _datetime_to_text(now)
            items = connection.execute(
                """
                SELECT source_task_id, operation_id, item_execution_attempt_id,
                       write_identity_key
                FROM shadowbot_commit_batch_items
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
            for item in items:
                attempt_id = str(item["item_execution_attempt_id"] or "")
                attempt = connection.execute(
                    """
                    SELECT status, raw_output_json
                    FROM shadowbot_execution_attempts
                    WHERE execution_attempt_id = ?
                    """,
                    (attempt_id,),
                ).fetchone()
                if attempt is not None:
                    raw = _json_load(attempt["raw_output_json"])
                    raw["quarantine_reason"] = reason
                    raw["quarantined_at"] = now_text
                    connection.execute(
                        """
                        UPDATE shadowbot_execution_attempts
                        SET status = 'SIDE_EFFECT_UNKNOWN',
                            side_effect_state = 'UNKNOWN',
                            ended_at = ?, raw_output_json = ?
                        WHERE execution_attempt_id = ?
                        """,
                        (now_text, _json_dump(raw), attempt_id),
                    )
                connection.execute(
                    """
                    UPDATE shadowbot_operations
                    SET status = 'NEEDS_RECONCILIATION', lock_owner = '',
                        updated_at = ?
                    WHERE operation_id = ?
                    """,
                    (now_text, str(item["operation_id"] or "")),
                )
                connection.execute(
                    """
                    UPDATE shadowbot_write_locks
                    SET status = 'UNKNOWN', released_at = NULL, updated_at = ?
                    WHERE write_identity_key = ?
                      AND item_execution_attempt_id = ?
                    """,
                    (
                        now_text,
                        str(item["write_identity_key"] or ""),
                        attempt_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE tasks
                    SET task_status = 'manual_review',
                        result_message = ?, updated_at = ?
                    WHERE task_id = ?
                    """,
                    (
                        f"ShadowBot v4 结果已隔离，副作用状态待对账：{batch_id}",
                        now_text,
                        str(item["source_task_id"] or ""),
                    ),
                )
            connection.execute(
                """
                UPDATE shadowbot_commit_batch_items
                SET status = 'UNKNOWN', submit_attempted = 1,
                    side_effect_state = 'UNKNOWN',
                    error_code = 'RESULT_CONTRACT_INVALID',
                    error_message = ?, updated_at = ?
                WHERE batch_id = ?
                """,
                (reason, now_text, batch_id),
            )
            connection.execute(
                """
                UPDATE shadowbot_commit_batches
                SET status = 'UNKNOWN', updated_at = ?
                WHERE batch_id = ?
                """,
                (now_text, batch_id),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    @staticmethod
    def _next_shadowbot_lease_version(
        connection: sqlite3.Connection, operation_id: str
    ) -> int:
        rows = connection.execute(
            "SELECT raw_output_json FROM shadowbot_execution_attempts WHERE operation_id = ?",
            (operation_id,),
        ).fetchall()
        versions = []
        for row in rows:
            raw = _json_load(row["raw_output_json"])
            lease = raw.get("lease") if isinstance(raw.get("lease"), dict) else {}
            versions.append(int(lease.get("version") or 0))
        return max(versions, default=0) + 1

    def update_shadowbot_execution_attempt(
        self,
        execution_attempt_id: str,
        *,
        shadowbot_run_id: str | None = None,
        status: str | None = None,
        side_effect_state: str | None = None,
        instruction_hash: str | None = None,
        request_file_sha256: str | None = None,
        queue_request_path: str | None = None,
        ended_at: datetime | None = None,
        raw_output: dict[str, Any] | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[object] = []
        if shadowbot_run_id is not None:
            assignments.append("shadowbot_run_id = ?")
            params.append(shadowbot_run_id)
        if status is not None:
            assignments.append("status = ?")
            params.append(status)
        if side_effect_state is not None:
            assignments.append("side_effect_state = ?")
            params.append(side_effect_state)
        if instruction_hash is not None:
            assignments.append("instruction_hash = ?")
            params.append(instruction_hash)
        if request_file_sha256 is not None:
            assignments.append("request_file_sha256 = ?")
            params.append(request_file_sha256)
        if queue_request_path is not None:
            assignments.append("queue_request_path = ?")
            params.append(queue_request_path)
        if ended_at is not None:
            assignments.append("ended_at = ?")
            params.append(_datetime_to_text(ended_at))
        if raw_output is not None:
            assignments.append("raw_output_json = ?")
            params.append(_json_dump(raw_output))
        if not assignments:
            return
        params.append(execution_attempt_id)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                f"UPDATE shadowbot_execution_attempts SET {', '.join(assignments)} WHERE execution_attempt_id = ?",
                params,
            )

    def insert_shadowbot_side_effect_checkpoint(
        self,
        *,
        operation_id: str,
        execution_attempt_id: str,
        side_effect_state: str,
        checkpoint_at: datetime,
    ) -> ShadowBotSideEffectCheckpoint:
        with closing(self.connect()) as connection, connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS next_version FROM shadowbot_side_effect_checkpoints WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            version = int(row["next_version"])
            checkpoint = ShadowBotSideEffectCheckpoint(
                operation_id=operation_id,
                execution_attempt_id=execution_attempt_id,
                side_effect_state=side_effect_state,
                checkpoint_at=checkpoint_at,
                version=version,
            )
            connection.execute(
                """
                INSERT INTO shadowbot_side_effect_checkpoints(
                    operation_id, execution_attempt_id, side_effect_state, checkpoint_at, version
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.operation_id,
                    checkpoint.execution_attempt_id,
                    checkpoint.side_effect_state,
                    _datetime_to_text(checkpoint.checkpoint_at),
                    checkpoint.version,
                ),
            )
            connection.execute(
                """
                UPDATE shadowbot_execution_attempts
                SET side_effect_state = ?
                WHERE execution_attempt_id = ?
                """,
                (side_effect_state, execution_attempt_id),
            )
        return checkpoint

    def latest_shadowbot_side_effect_checkpoint(
        self, operation_id: str
    ) -> ShadowBotSideEffectCheckpoint | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM shadowbot_side_effect_checkpoints
                WHERE operation_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (operation_id,),
            ).fetchone()
        return _row_to_shadowbot_checkpoint(row) if row is not None else None

    def insert_retry_authorization(self, authorization: RetryAuthorization) -> int:
        row = _retry_authorization_to_row(authorization)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO retry_authorizations(
                    retry_authorization_id, operation_id, source_execution_attempt_id,
                    authorization_type, authorized_by, evidence_type, evidence_hash,
                    approved_payload_hash, status, max_uses, consumed_by_execution_attempt_id,
                    expires_at, reason, created_at, consumed_at
                )
                VALUES(
                    :retry_authorization_id, :operation_id, :source_execution_attempt_id,
                    :authorization_type, :authorized_by, :evidence_type, :evidence_hash,
                    :approved_payload_hash, :status, :max_uses, :consumed_by_execution_attempt_id,
                    :expires_at, :reason, :created_at, :consumed_at
                )
                """,
                row,
            )
            return connection.total_changes - before

    def issue_retry_authorization(
        self,
        authorization: RetryAuthorization,
        *,
        allowed_operation_statuses: Iterable[str],
        retry_window_deadline: datetime,
        max_retry_window_seconds: int,
    ) -> bool:
        """Persist one authorization and expose RETRY_AUTHORIZED atomically."""
        allowed = tuple(
            dict.fromkeys(str(value) for value in allowed_operation_statuses)
        )
        if not allowed:
            raise ValueError("allowed_operation_statuses must not be empty")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            operation = connection.execute(
                "SELECT * FROM shadowbot_operations WHERE operation_id = ?",
                (authorization.operation_id,),
            ).fetchone()
            source = connection.execute(
                "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (authorization.source_execution_attempt_id,),
            ).fetchone()
            reference_time = authorization.created_at or datetime.now(
                retry_window_deadline.tzinfo
            )
            source_raw = (
                _json_load(source["raw_output_json"]) if source is not None else {}
            )
            approval_expires_at = _text_to_datetime(
                source_raw.get("approval_expires_at")
            )
            if (
                approval_expires_at is not None
                and approval_expires_at.tzinfo is None
                and reference_time.tzinfo is not None
            ):
                approval_expires_at = approval_expires_at.replace(
                    tzinfo=reference_time.tzinfo
                )
            operation_created_at = (
                _text_to_datetime(operation["created_at"])
                if operation is not None
                else None
            )
            if (
                operation_created_at is not None
                and operation_created_at.tzinfo is None
                and reference_time.tzinfo is not None
            ):
                operation_created_at = operation_created_at.replace(
                    tzinfo=reference_time.tzinfo
                )
            commit_rows = connection.execute(
                """
                SELECT started_at FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT'
                """,
                (authorization.operation_id,),
            ).fetchall()
            retry_origins = [
                value
                for value in (
                    operation_created_at,
                    *(_text_to_datetime(row["started_at"]) for row in commit_rows),
                )
                if value is not None
            ]
            retry_origins = [
                value.replace(tzinfo=reference_time.tzinfo)
                if value.tzinfo is None and reference_time.tzinfo is not None
                else value
                for value in retry_origins
            ]
            recomputed_deadline = (
                min(
                    min(retry_origins) + timedelta(seconds=max_retry_window_seconds),
                    approval_expires_at,
                )
                if retry_origins
                and approval_expires_at is not None
                and max_retry_window_seconds > 0
                else None
            )
            authorization_expires_at = authorization.expires_at
            if (
                operation is None
                or source is None
                or str(operation["status"]) not in allowed
                or str(operation["lock_owner"] or "")
                or str(operation["approved_payload_hash"])
                != authorization.approved_payload_hash
                or str(source["operation_id"]) != authorization.operation_id
                or str(source["status"]) in {"STARTING", "RUNNING"}
                or recomputed_deadline is None
                or recomputed_deadline != retry_window_deadline
                or authorization_expires_at is None
                or authorization_expires_at > retry_window_deadline
                or reference_time > retry_window_deadline
            ):
                connection.rollback()
                return False
            active = connection.execute(
                """
                SELECT 1 FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT' AND status IN ('STARTING', 'RUNNING')
                LIMIT 1
                """,
                (authorization.operation_id,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                return False
            source_raw["retry_window_deadline"] = _datetime_to_text(
                retry_window_deadline
            )
            source_raw["max_retry_window_seconds"] = max_retry_window_seconds
            source_raw["retry_window_authorization_id"] = (
                authorization.retry_authorization_id
            )
            connection.execute(
                "UPDATE shadowbot_execution_attempts SET raw_output_json = ? WHERE execution_attempt_id = ?",
                (_json_dump(source_raw), authorization.source_execution_attempt_id),
            )
            connection.execute(
                """
                INSERT INTO retry_authorizations(
                    retry_authorization_id, operation_id, source_execution_attempt_id,
                    authorization_type, authorized_by, evidence_type, evidence_hash,
                    approved_payload_hash, status, max_uses, consumed_by_execution_attempt_id,
                    expires_at, reason, created_at, consumed_at
                ) VALUES(
                    :retry_authorization_id, :operation_id, :source_execution_attempt_id,
                    :authorization_type, :authorized_by, :evidence_type, :evidence_hash,
                    :approved_payload_hash, :status, :max_uses, :consumed_by_execution_attempt_id,
                    :expires_at, :reason, :created_at, :consumed_at
                )
                """,
                _retry_authorization_to_row(authorization),
            )
            placeholders = ",".join("?" for _ in allowed)
            cursor = connection.execute(
                f"""
                UPDATE shadowbot_operations
                SET status = 'RETRY_AUTHORIZED', updated_at = ?
                WHERE operation_id = ? AND status IN ({placeholders}) AND lock_owner = ''
                """,
                (
                    _datetime_to_text(datetime.now()),
                    authorization.operation_id,
                    *allowed,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except sqlite3.IntegrityError:
            connection.rollback()
            return False
        finally:
            connection.close()

    def consume_retry_authorization_and_create_attempt(
        self,
        retry_authorization_id: str,
        attempt: ShadowBotExecutionAttempt,
        *,
        owner_token: str,
        lease_expires_at: datetime,
        approved_payload_hash: str,
        consumed_at: datetime,
    ) -> ShadowBotExecutionAttempt | None:
        """Consume ACTIVE authorization and create the new attempt/lease in one transaction."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            authorization = connection.execute(
                "SELECT * FROM retry_authorizations WHERE retry_authorization_id = ?",
                (retry_authorization_id,),
            ).fetchone()
            operation = connection.execute(
                "SELECT * FROM shadowbot_operations WHERE operation_id = ?",
                (attempt.operation_id,),
            ).fetchone()
            if authorization is None or operation is None:
                connection.rollback()
                return None
            expires_at = _text_to_datetime(authorization["expires_at"])
            if (
                expires_at is not None
                and expires_at.tzinfo is None
                and consumed_at.tzinfo is not None
            ):
                expires_at = expires_at.replace(tzinfo=consumed_at.tzinfo)
            if (
                str(authorization["status"]) != "ACTIVE"
                or int(authorization["max_uses"]) != 1
                or str(authorization["operation_id"]) != attempt.operation_id
                or str(authorization["approved_payload_hash"]) != approved_payload_hash
                or expires_at is None
                or expires_at <= consumed_at
                or str(operation["status"]) != "RETRY_AUTHORIZED"
                or str(operation["approved_payload_hash"]) != approved_payload_hash
                or str(operation["lock_owner"] or "")
            ):
                connection.rollback()
                return None
            source = connection.execute(
                "SELECT * FROM shadowbot_execution_attempts WHERE execution_attempt_id = ?",
                (str(authorization["source_execution_attempt_id"]),),
            ).fetchone()
            active = connection.execute(
                """
                SELECT 1 FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT' AND status IN ('STARTING', 'RUNNING')
                LIMIT 1
                """,
                (attempt.operation_id,),
            ).fetchone()
            source_raw = (
                _json_load(source["raw_output_json"]) if source is not None else {}
            )
            source_status = str(source["status"]) if source is not None else ""
            source_approval_expires_at = _text_to_datetime(
                source_raw.get("approval_expires_at")
            )
            if (
                source_approval_expires_at is not None
                and source_approval_expires_at.tzinfo is None
                and consumed_at.tzinfo is not None
            ):
                source_approval_expires_at = source_approval_expires_at.replace(
                    tzinfo=consumed_at.tzinfo
                )
            retry_window_deadline = _text_to_datetime(
                source_raw.get("retry_window_deadline")
            )
            if (
                retry_window_deadline is not None
                and retry_window_deadline.tzinfo is None
                and consumed_at.tzinfo is not None
            ):
                retry_window_deadline = retry_window_deadline.replace(
                    tzinfo=consumed_at.tzinfo
                )
            try:
                max_retry_window_seconds = int(
                    source_raw.get("max_retry_window_seconds") or 0
                )
            except (TypeError, ValueError):
                max_retry_window_seconds = 0
            operation_created_at = _text_to_datetime(operation["created_at"])
            if (
                operation_created_at is not None
                and operation_created_at.tzinfo is None
                and consumed_at.tzinfo is not None
            ):
                operation_created_at = operation_created_at.replace(
                    tzinfo=consumed_at.tzinfo
                )
            commit_rows = connection.execute(
                """
                SELECT started_at FROM shadowbot_execution_attempts
                WHERE operation_id = ? AND execution_mode = 'COMMIT'
                """,
                (attempt.operation_id,),
            ).fetchall()
            retry_origins = [
                value
                for value in (
                    operation_created_at,
                    *(_text_to_datetime(row["started_at"]) for row in commit_rows),
                )
                if value is not None
            ]
            retry_origins = [
                value.replace(tzinfo=consumed_at.tzinfo)
                if value.tzinfo is None and consumed_at.tzinfo is not None
                else value
                for value in retry_origins
            ]
            recomputed_retry_window_deadline = (
                min(
                    min(retry_origins) + timedelta(seconds=max_retry_window_seconds),
                    source_approval_expires_at,
                )
                if retry_origins
                and source_approval_expires_at is not None
                and max_retry_window_seconds > 0
                else None
            )
            retry_window_valid = bool(
                retry_window_deadline is not None
                and recomputed_retry_window_deadline == retry_window_deadline
                and consumed_at <= retry_window_deadline
            )
            source_approval_valid = bool(
                source_raw.get("approval_id")
                and str(source_raw.get("approved_payload_hash") or "")
                == approved_payload_hash
                and source_approval_expires_at is not None
                and source_approval_expires_at > consumed_at
            )
            frozen_manual_source = bool(
                source is not None
                and str(authorization["authorization_type"]) == "MANUAL"
                and source_raw.get("frozen_reason") == "DUPLICATE_ACTIVE_COMMIT_ATTEMPT"
                and (
                    (
                        str(authorization["evidence_type"])
                        == "PRE_PUBLISH_NOT_PUBLISHED"
                        and source_status == "START_UNKNOWN"
                        and str(source["side_effect_state"]) == "NOT_STARTED"
                    )
                    or (
                        str(authorization["evidence_type"]) == "NOT_APPLIED_RESULT"
                        and source_status in {"START_UNKNOWN", "SIDE_EFFECT_UNKNOWN"}
                    )
                )
            )
            normal_source = bool(
                source is not None
                and source_status in {"START_FAILED", "FAILED", "NOT_APPLIED"}
                and (
                    (
                        source_status == "START_FAILED"
                        and str(source["side_effect_state"]) == "NOT_STARTED"
                    )
                    or (
                        source_status in {"FAILED", "NOT_APPLIED"}
                        and str(source["side_effect_state"]) == "NOT_APPLIED"
                    )
                )
            )
            if (
                source is None
                or str(source["operation_id"]) != attempt.operation_id
                or str(source["execution_mode"]) != "COMMIT"
                or not source_approval_valid
                or not retry_window_valid
                or not (normal_source or frozen_manual_source)
                or active is not None
            ):
                connection.rollback()
                return None
            lease_version = self._next_shadowbot_lease_version(
                connection, attempt.operation_id
            )
            attempt.raw_output = {
                **attempt.raw_output,
                "retry_authorization_id": retry_authorization_id,
                "source_execution_attempt_id": str(
                    authorization["source_execution_attempt_id"]
                ),
                "operation_status_before_attempt": str(operation["status"]),
                "lease": {
                    "owner_token": owner_token,
                    "version": lease_version,
                    "expires_at": _datetime_to_text(lease_expires_at),
                    "active": True,
                },
            }
            connection.execute(
                """
                INSERT INTO shadowbot_execution_attempts(
                    execution_attempt_id, operation_id, execution_mode, shadowbot_run_id,
                    status, side_effect_state, started_at, instruction_hash,
                    request_file_sha256, queue_request_path, ended_at, raw_output_json
                ) VALUES(
                    :execution_attempt_id, :operation_id, :execution_mode, :shadowbot_run_id,
                    :status, :side_effect_state, :started_at, :instruction_hash,
                    :request_file_sha256, :queue_request_path, :ended_at, :raw_output_json
                )
                """,
                _shadowbot_attempt_to_row(attempt),
            )
            consumed = connection.execute(
                """
                UPDATE retry_authorizations
                SET status = 'CONSUMED', consumed_by_execution_attempt_id = ?, consumed_at = ?
                WHERE retry_authorization_id = ? AND status = 'ACTIVE' AND max_uses = 1
                  AND consumed_by_execution_attempt_id IS NULL AND expires_at > ?
                """,
                (
                    attempt.execution_attempt_id,
                    _datetime_to_text(consumed_at),
                    retry_authorization_id,
                    _datetime_to_text(consumed_at),
                ),
            )
            operation_updated = connection.execute(
                """
                UPDATE shadowbot_operations
                SET status = 'RUNNING', lock_owner = ?, updated_at = ?
                WHERE operation_id = ? AND status = 'RETRY_AUTHORIZED' AND lock_owner = ''
                  AND approved_payload_hash = ?
                """,
                (
                    owner_token,
                    _datetime_to_text(consumed_at),
                    attempt.operation_id,
                    approved_payload_hash,
                ),
            )
            if consumed.rowcount != 1 or operation_updated.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
            return attempt
        except sqlite3.IntegrityError:
            connection.rollback()
            return None
        finally:
            connection.close()

    def get_retry_authorization(
        self, retry_authorization_id: str
    ) -> RetryAuthorization | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM retry_authorizations WHERE retry_authorization_id = ?",
                (retry_authorization_id,),
            ).fetchone()
        return _row_to_retry_authorization(row) if row is not None else None

    def list_retry_authorizations(
        self, *, operation_id: str | None = None
    ) -> list[RetryAuthorization]:
        query = "SELECT * FROM retry_authorizations"
        params: tuple[str, ...] = ()
        if operation_id:
            query += " WHERE operation_id = ?"
            params = (operation_id,)
        query += " ORDER BY created_at, retry_authorization_id"
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_retry_authorization(row) for row in rows]

    def insert_notification_logs(self, logs: Iterable[NotificationLog]) -> int:
        rows = [_notification_log_to_row(log) for log in logs]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO notification_logs(
                    notification_id, related_task_id, related_review_task_id, recipient_type,
                    recipient, channel, sent_at, send_status, dedupe_key, message, error_message, created_at
                )
                VALUES(
                    :notification_id, :related_task_id, :related_review_task_id, :recipient_type,
                    :recipient, :channel, :sent_at, :send_status, :dedupe_key, :message, :error_message, :created_at
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def list_notification_logs(
        self,
        *,
        related_review_task_id: str | None = None,
        send_status: str | None = None,
        channel: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[NotificationLog]:
        query = "SELECT * FROM notification_logs"
        clauses: list[str] = []
        params: list[str] = []
        if related_review_task_id:
            clauses.append("related_review_task_id = ?")
            params.append(related_review_task_id)
        if send_status:
            clauses.append("send_status = ?")
            params.append(send_status)
        if channel:
            clauses.append("channel = ?")
            params.append(channel)
        if clauses:
            query = f"{query} WHERE {' AND '.join(clauses)}"
        query = f"{query} ORDER BY created_at DESC, notification_id ASC"
        query, params = _with_limit_offset(query, params, limit=limit, offset=offset)
        with closing(self.connect_read()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_notification_log(row) for row in rows]

    def get_notification_log(self, notification_id: str) -> NotificationLog | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM notification_logs WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        return _row_to_notification_log(row) if row is not None else None

    def update_notification_log_delivery(
        self,
        notification_id: str,
        *,
        send_status: str,
        sent_at: datetime | None = None,
        error_message: str = "",
    ) -> bool:
        """Update the v5 compatibility projection after an Outbox writeback."""

        with closing(self.connect_write()) as connection, connection:
            return self._update_notification_log_delivery_on_connection(
                connection,
                notification_id,
                send_status=send_status,
                sent_at=sent_at,
                error_message=error_message,
            )

    @staticmethod
    def _update_notification_log_delivery_on_connection(
        connection: sqlite3.Connection,
        notification_id: str,
        *,
        send_status: str,
        sent_at: datetime | None = None,
        error_message: str = "",
    ) -> bool:
        return (
            connection.execute(
                """
            UPDATE notification_logs
            SET send_status = ?, sent_at = ?, error_message = ?
            WHERE notification_id = ?
            """,
                (
                    send_status,
                    _datetime_to_text(sent_at),
                    _sanitize_persisted_error(error_message),
                    notification_id,
                ),
            ).rowcount
            == 1
        )

    # ------------------------------------------------------------------
    # Schema v6 durable notification outbox
    # ------------------------------------------------------------------

    def insert_notification_outbox(self, notification: NotificationOutbox) -> int:
        """Insert one logical notification without sending it.

        The unique notification_key makes retries idempotent.  Callers that
        need the existing row can use get_notification_outbox_by_key().
        """

        with closing(self.connect()) as connection, connection:
            return self._insert_notification_outbox_on_connection(
                connection, notification
            )

    @staticmethod
    def _insert_notification_outbox_on_connection(
        connection: sqlite3.Connection,
        notification: NotificationOutbox,
    ) -> int:
        row = _notification_outbox_to_row(notification)
        before = connection.total_changes
        connection.execute(
            """
            INSERT OR IGNORE INTO notification_outbox(
                notification_id, notification_key, notification_type,
                related_task_id, related_review_task_id, recipient_type, recipient_ref,
                channel, priority, payload_json, status, attempt_count, max_attempts,
                next_attempt_at, deadline_at, lease_owner_token, lease_version,
                lease_expires_at, sent_at, provider_message_id, last_error_code,
                last_error_message, created_at, updated_at
            )
            VALUES(
                :notification_id, :notification_key, :notification_type,
                :related_task_id, :related_review_task_id, :recipient_type, :recipient_ref,
                :channel, :priority, :payload_json, :status, :attempt_count, :max_attempts,
                :next_attempt_at, :deadline_at, :lease_owner_token, :lease_version,
                :lease_expires_at, :sent_at, :provider_message_id, :last_error_code,
                :last_error_message, :created_at, :updated_at
            )
            """,
            row,
        )
        return connection.total_changes - before

    def insert_review_task_with_notification_outbox(
        self,
        review_task: ReviewTask,
        notification: NotificationOutbox,
        *,
        review_token: ReviewToken | None = None,
        incident_event: OperationalIncidentEvent | None = None,
        compatibility_log: NotificationLog | None = None,
        failure_injector: Callable[[str], None] | None = None,
    ) -> tuple[int, int]:
        """Atomically insert a review task and its notification intent."""

        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            review_row = _review_task_to_row(review_task)
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO review_tasks(
                    review_task_id, trade_date, scope_type, scope_key, dedupe_key, source_task_id,
                    review_type, review_status, internal_sku, platform_name, reason, review_payload_json,
                    resolution_payload_json, required_by, created_at, updated_at, resolved_by,
                    resolved_at, resolution_note
                )
                VALUES(
                    :review_task_id, :trade_date, :scope_type, :scope_key, :dedupe_key, :source_task_id,
                    :review_type, :review_status, :internal_sku, :platform_name, :reason, :review_payload_json,
                    :resolution_payload_json, :required_by, :created_at, :updated_at, :resolved_by,
                    :resolved_at, :resolution_note
                )
                """,
                review_row,
            )
            review_inserted = connection.total_changes - before
            if review_inserted != 1:
                connection.rollback()
                return 0, 0
            if failure_injector is not None:
                failure_injector("after_review_insert")
            if review_token is not None:
                token_row = _review_token_to_row(review_token)
                inserted_token = connection.execute(
                    """
                    INSERT OR IGNORE INTO review_tokens(
                        token_id, review_task_id, token_hash, token_subject, allowed_actions,
                        expires_at, used_at, revoked_at, created_at, created_by,
                        last_used_at, note
                    ) VALUES(
                        :token_id, :review_task_id, :token_hash, :token_subject,
                        :allowed_actions, :expires_at, :used_at, :revoked_at,
                        :created_at, :created_by, :last_used_at, :note
                    )
                    """,
                    token_row,
                ).rowcount
                if inserted_token != 1:
                    raise ValueError(
                        "review token already exists for a new review task"
                    )
                if failure_injector is not None:
                    failure_injector("after_review_token_insert")
            outbox_inserted = self._insert_notification_outbox_on_connection(
                connection, notification
            )
            if outbox_inserted != 1:
                raise ValueError(
                    "notification_key already exists for a new review task"
                )
            if failure_injector is not None:
                failure_injector("after_outbox_insert")
            if compatibility_log is not None:
                log_row = _notification_log_to_row(compatibility_log)
                inserted_log = connection.execute(
                    """
                    INSERT OR IGNORE INTO notification_logs(
                        notification_id, related_task_id, related_review_task_id, recipient_type,
                        recipient, channel, sent_at, send_status, dedupe_key, message,
                        error_message, created_at
                    ) VALUES(
                        :notification_id, :related_task_id, :related_review_task_id, :recipient_type,
                        :recipient, :channel, :sent_at, :send_status, :dedupe_key, :message,
                        :error_message, :created_at
                    )
                    """,
                    log_row,
                ).rowcount
                if inserted_log != 1:
                    raise ValueError(
                        "compatibility notification log already exists for a new outbox"
                    )
                if failure_injector is not None:
                    failure_injector("after_compatibility_log_insert")
            if incident_event is not None:
                if incident_event.event_type.value != "REVIEW_RECORDED":
                    raise ValueError(
                        "atomic review bundle requires REVIEW_RECORDED Incident event"
                    )
                if incident_event.from_status is not incident_event.to_status:
                    if (
                        incident_event.from_status is None
                        or incident_event.to_status is None
                    ):
                        raise ValueError(
                            "Incident review transition requires both statuses"
                        )
                    if incident_event.to_status not in {
                        IncidentStatus.WAITING_HUMAN,
                        IncidentStatus.RESOLVED,
                    }:
                        raise ValueError("unsupported Incident review target status")
                    resolved_at = (
                        _datetime_to_text(incident_event.occurred_at)
                        if incident_event.to_status is IncidentStatus.RESOLVED
                        else None
                    )
                    updated_incident = connection.execute(
                        """
                        UPDATE operational_incidents
                        SET incident_status = ?, resolved_at = ?, updated_at = ?
                        WHERE incident_id = ? AND incident_status = ?
                        """,
                        (
                            incident_event.to_status.value,
                            resolved_at,
                            _datetime_to_text(incident_event.occurred_at),
                            incident_event.incident_id,
                            incident_event.from_status.value,
                        ),
                    ).rowcount
                    if updated_incident != 1:
                        raise ValueError("Incident changed before Review bundle commit")
                inserted_event = connection.execute(
                    """
                    INSERT INTO operational_incident_events(
                        event_id, event_key, incident_id, event_type, occurred_at,
                        source_type, source_ref_id, from_status, to_status, severity,
                        event_payload_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        incident_event.event_id,
                        incident_event.event_key,
                        incident_event.incident_id,
                        incident_event.event_type.value,
                        _datetime_to_text(incident_event.occurred_at),
                        incident_event.source_type,
                        incident_event.source_ref_id,
                        (
                            incident_event.from_status.value
                            if incident_event.from_status is not None
                            else None
                        ),
                        (
                            incident_event.to_status.value
                            if incident_event.to_status is not None
                            else None
                        ),
                        incident_event.severity,
                        _json_dump(incident_event.event_payload),
                        _datetime_to_text(incident_event.created_at),
                    ),
                ).rowcount
                if inserted_event != 1:
                    raise ValueError(
                        "Incident Review event already exists for a new review task"
                    )
                if failure_injector is not None:
                    failure_injector("after_incident_review_event_insert")
            connection.commit()
            return review_inserted, outbox_inserted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def sync_incident_initial_notification_state(
        self,
        *,
        incident_id: str,
        review_task_id: str,
        midpoint_minutes: int = 5,
    ) -> dict[str, object]:
        """Project initial Review delivery into the existing Incident notification state."""

        if midpoint_minutes <= 0:
            raise ValueError("midpoint_minutes must be positive")
        with closing(self.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                incident = connection.execute(
                    "SELECT * FROM operational_incidents WHERE incident_id = ?",
                    (incident_id,),
                ).fetchone()
                review = connection.execute(
                    "SELECT * FROM review_tasks WHERE review_task_id = ?",
                    (review_task_id,),
                ).fetchone()
                if incident is None or review is None:
                    raise ValueError(
                        "Incident or Review not found for notification sync"
                    )
                review_payload = _json_load(review["review_payload_json"])
                if str(review_payload.get("incident_id") or "") != incident_id:
                    raise ValueError("Review is not bound to the requested Incident")
                outboxes = connection.execute(
                    """
                    SELECT * FROM notification_outbox
                    WHERE related_review_task_id = ?
                      AND notification_type = 'mobile_review_required'
                      AND status <> 'CANCELLED'
                    ORDER BY created_at DESC, notification_id DESC
                    """,
                    (review_task_id,),
                ).fetchall()
                if not outboxes:
                    raise ValueError(
                        "Incident Review has no initial notification Outbox"
                    )
                initial = outboxes[0]
                channel = str(initial["channel"])
                identity_sha256 = hashlib.sha256(
                    _json_dump(
                        {
                            "incident_id": incident_id,
                            "review_task_id": review_task_id,
                            "notification_id": str(initial["notification_id"]),
                            "notification_key": str(initial["notification_key"]),
                            "channel": channel,
                        }
                    ).encode("utf-8")
                ).hexdigest()
                state = connection.execute(
                    """
                    SELECT * FROM incident_notification_state
                    WHERE incident_id = ? AND channel = ?
                    """,
                    (incident_id, channel),
                ).fetchone()
                if (
                    state is not None
                    and str(state["escalation_state"]).startswith("MIDPOINT_")
                    and str(state["payload_sha256"]) == identity_sha256
                ):
                    connection.commit()
                    return dict(state)
                status = str(initial["status"])
                sent_at = _text_to_datetime(initial["sent_at"])
                if status == "SENT" and sent_at is not None:
                    escalation_state = "INITIAL_SENT"
                    notification_count = 1
                    last_notified_at = sent_at
                    next_notification_at = (
                        sent_at + timedelta(minutes=midpoint_minutes)
                        if str(incident["severity"]) == "S4"
                        else None
                    )
                elif status in {
                    "UNKNOWN_DELIVERY",
                    "FAILED",
                    "EXPIRED",
                    "CANCELLED",
                } or (status == "SENT" and sent_at is None):
                    escalation_state = "INITIAL_DELIVERY_BLOCKED"
                    notification_count = 0
                    last_notified_at = None
                    next_notification_at = None
                else:
                    escalation_state = "WAITING_INITIAL_DELIVERY"
                    notification_count = 0
                    last_notified_at = None
                    next_notification_at = None
                updated_at = sent_at or self._clock()
                connection.execute(
                    """
                    INSERT INTO incident_notification_state(
                        incident_id, channel, notification_count, last_notified_at,
                        next_notification_at, escalation_state, payload_sha256, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(incident_id, channel) DO UPDATE SET
                        notification_count = excluded.notification_count,
                        last_notified_at = excluded.last_notified_at,
                        next_notification_at = excluded.next_notification_at,
                        escalation_state = excluded.escalation_state,
                        payload_sha256 = excluded.payload_sha256,
                        updated_at = excluded.updated_at
                    """,
                    (
                        incident_id,
                        channel,
                        notification_count,
                        _datetime_to_text(last_notified_at),
                        _datetime_to_text(next_notification_at),
                        escalation_state,
                        identity_sha256,
                        _datetime_to_text(updated_at),
                    ),
                )
                projected = connection.execute(
                    """
                    SELECT * FROM incident_notification_state
                    WHERE incident_id = ? AND channel = ?
                    """,
                    (incident_id, channel),
                ).fetchone()
                connection.commit()
                return dict(projected)
            except Exception:
                connection.rollback()
                raise

    def enqueue_incident_midpoint_notification_atomic(
        self,
        *,
        incident_id: str,
        review_task_id: str,
        notification: NotificationOutbox,
        compatibility_log: NotificationLog,
        now: datetime,
    ) -> str:
        """Queue at most one S4 midpoint reminder after all suppression checks."""

        with closing(self.connect_write()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    """
                    SELECT * FROM incident_notification_state
                    WHERE incident_id = ? AND channel = ?
                    """,
                    (incident_id, notification.channel),
                ).fetchone()
                if state is None:
                    raise ValueError("initial Incident notification state is missing")
                escalation_state = str(state["escalation_state"])
                if escalation_state != "INITIAL_SENT":
                    connection.commit()
                    return (
                        "MIDPOINT_ALREADY_QUEUED"
                        if escalation_state == "MIDPOINT_QUEUED"
                        else escalation_state
                    )
                due_at = _text_to_datetime(state["next_notification_at"])
                if due_at is None or due_at > now:
                    connection.commit()
                    return "NOT_DUE"
                incident = connection.execute(
                    "SELECT * FROM operational_incidents WHERE incident_id = ?",
                    (incident_id,),
                ).fetchone()
                review = connection.execute(
                    "SELECT * FROM review_tasks WHERE review_task_id = ?",
                    (review_task_id,),
                ).fetchone()
                if incident is None or review is None:
                    raise ValueError(
                        "Incident or Review not found for midpoint notification"
                    )
                if str(incident["severity"]) != "S4" or str(
                    incident["incident_status"]
                ) in {
                    "RESOLVED",
                    "CLOSED",
                }:
                    connection.execute(
                        """
                        UPDATE incident_notification_state
                        SET next_notification_at = NULL,
                            escalation_state = 'MIDPOINT_CONDITION_CLEARED',
                            updated_at = ?
                        WHERE incident_id = ? AND channel = ?
                        """,
                        (_datetime_to_text(now), incident_id, notification.channel),
                    )
                    connection.commit()
                    return "MIDPOINT_CONDITION_CLEARED"
                if str(review["review_status"]) != ReviewTaskStatus.PENDING.value:
                    connection.execute(
                        """
                        UPDATE incident_notification_state
                        SET next_notification_at = NULL,
                            escalation_state = 'MIDPOINT_REVIEW_RESOLVED',
                            updated_at = ?
                        WHERE incident_id = ? AND channel = ?
                        """,
                        (_datetime_to_text(now), incident_id, notification.channel),
                    )
                    connection.commit()
                    return "MIDPOINT_REVIEW_RESOLVED"
                ack = connection.execute(
                    """
                    SELECT 1 FROM operational_incident_events
                    WHERE incident_id = ? AND event_type = 'ACK'
                      AND occurred_at >= ?
                    LIMIT 1
                    """,
                    (incident_id, str(review["created_at"])),
                ).fetchone()
                if ack is not None:
                    connection.execute(
                        """
                        UPDATE incident_notification_state
                        SET next_notification_at = NULL,
                            escalation_state = 'MIDPOINT_ACK_SUPPRESSED',
                            updated_at = ?
                        WHERE incident_id = ? AND channel = ?
                        """,
                        (_datetime_to_text(now), incident_id, notification.channel),
                    )
                    connection.commit()
                    return "MIDPOINT_ACK_SUPPRESSED"

                if (
                    self._insert_notification_outbox_on_connection(
                        connection, notification
                    )
                    != 1
                ):
                    raise ValueError(
                        "midpoint notification key already exists unexpectedly"
                    )
                log_row = _notification_log_to_row(compatibility_log)
                if (
                    connection.execute(
                        """
                    INSERT OR IGNORE INTO notification_logs(
                        notification_id, related_task_id, related_review_task_id,
                        recipient_type, recipient, channel, sent_at, send_status,
                        dedupe_key, message, error_message, created_at
                    ) VALUES(
                        :notification_id, :related_task_id, :related_review_task_id,
                        :recipient_type, :recipient, :channel, :sent_at, :send_status,
                        :dedupe_key, :message, :error_message, :created_at
                    )
                    """,
                        log_row,
                    ).rowcount
                    != 1
                ):
                    raise ValueError("midpoint compatibility log already exists")
                connection.execute(
                    """
                    UPDATE incident_notification_state
                    SET next_notification_at = NULL,
                        escalation_state = 'MIDPOINT_QUEUED',
                        updated_at = ?
                    WHERE incident_id = ? AND channel = ?
                    """,
                    (_datetime_to_text(now), incident_id, notification.channel),
                )
                connection.commit()
                return "MIDPOINT_QUEUED"
            except Exception:
                connection.rollback()
                raise

    def sync_incident_midpoint_notification_state(
        self,
        *,
        incident_id: str,
        notification_id: str,
    ) -> dict[str, object]:
        with closing(self.connect_write()) as connection, connection:
            notification = connection.execute(
                "SELECT * FROM notification_outbox WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            if notification is None:
                raise ValueError("midpoint notification not found")
            channel = str(notification["channel"])
            status = str(notification["status"])
            sent_at = _text_to_datetime(notification["sent_at"])
            if status == "SENT" and sent_at is not None:
                escalation_state = "MIDPOINT_SENT"
                notification_count = 2
                last_notified_at = sent_at
            elif status in {"UNKNOWN_DELIVERY", "FAILED", "EXPIRED", "CANCELLED"}:
                escalation_state = "MIDPOINT_DELIVERY_FAILED"
                notification_count = 1
                last_notified_at = None
            else:
                escalation_state = "MIDPOINT_QUEUED"
                notification_count = 1
                last_notified_at = None
            connection.execute(
                """
                UPDATE incident_notification_state
                SET notification_count = ?,
                    last_notified_at = COALESCE(?, last_notified_at),
                    escalation_state = ?, updated_at = ?
                WHERE incident_id = ? AND channel = ?
                """,
                (
                    notification_count,
                    _datetime_to_text(last_notified_at),
                    escalation_state,
                    _datetime_to_text(sent_at or self._clock()),
                    incident_id,
                    channel,
                ),
            )
            state = connection.execute(
                """
                SELECT * FROM incident_notification_state
                WHERE incident_id = ? AND channel = ?
                """,
                (incident_id, channel),
            ).fetchone()
            if state is None:
                raise ValueError("Incident notification state not found")
            return dict(state)

    def get_notification_outbox(
        self, notification_id: str
    ) -> NotificationOutbox | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        return _row_to_notification_outbox(row) if row is not None else None

    def get_notification_outbox_by_key(
        self, notification_key: str
    ) -> NotificationOutbox | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE notification_key = ?",
                (notification_key,),
            ).fetchone()
        return _row_to_notification_outbox(row) if row is not None else None

    def list_notification_outbox(
        self,
        *,
        status: str | None = None,
        related_task_id: str | None = None,
        related_review_task_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[NotificationOutbox]:
        query = "SELECT * FROM notification_outbox"
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(getattr(status, "value", status))
        if related_task_id:
            clauses.append("related_task_id = ?")
            params.append(related_task_id)
        if related_review_task_id:
            clauses.append("related_review_task_id = ?")
            params.append(related_review_task_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY priority DESC, deadline_at IS NULL, deadline_at ASC, created_at ASC, notification_id ASC"
        query, params = _with_limit_offset(query, params, limit=limit, offset=offset)
        with closing(self.connect_read()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_notification_outbox(row) for row in rows]

    def claim_notification_outbox(
        self,
        *,
        now: datetime | None = None,
        lease_seconds: int = 60,
        limit: int = 1,
        channel: str | None = None,
    ) -> list[NotificationOutbox]:
        """Claim due notifications using one short BEGIN IMMEDIATE transaction."""

        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        if limit <= 0:
            return []
        connection = self.connect_write()
        claimed: list[NotificationOutbox] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            # Read the authoritative clock only after acquiring the write lock.
            reference_time = now or self._clock()
            reference_text = _datetime_to_text(reference_time)
            # A deadline is authoritative even if a worker has not yet claimed
            # the row.  SENDING rows are handled separately by the watchdog.
            expired_rows = connection.execute(
                """
                SELECT notification_id FROM notification_outbox
                WHERE status IN ('PENDING', 'RETRY_WAIT')
                  AND deadline_at IS NOT NULL AND deadline_at <= ?
                """,
                (reference_text,),
            ).fetchall()
            for expired_row in expired_rows:
                notification_id = str(expired_row["notification_id"])
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'EXPIRED', lease_owner_token = '', lease_expires_at = NULL,
                        last_error_code = 'DEADLINE_EXPIRED',
                        last_error_message = 'notification deadline expired before delivery',
                        updated_at = ?
                    WHERE notification_id = ? AND status IN ('PENDING', 'RETRY_WAIT')
                    """,
                    (reference_text, notification_id),
                )
                self._update_notification_log_delivery_on_connection(
                    connection,
                    notification_id,
                    send_status="failed",
                    error_message="notification deadline expired before delivery",
                )
            channel_clause = ""
            channel_params: list[object] = []
            if channel:
                channel_clause = " AND channel = ?"
                channel_params.append(str(channel).strip().lower())
            rows = connection.execute(
                f"""
                SELECT * FROM notification_outbox
                WHERE (
                    (
                        status IN ('PENDING', 'RETRY_WAIT')
                        AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                    )
                    OR (
                        status = 'LEASED'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?
                    )
                  )
                  AND (deadline_at IS NULL OR deadline_at > ?)
                  {channel_clause}
                ORDER BY priority DESC, deadline_at IS NULL, deadline_at ASC,
                         created_at ASC, notification_id ASC
                LIMIT ?
                """,
                [
                    reference_text,
                    reference_text,
                    reference_text,
                    *channel_params,
                    int(limit),
                ],
            ).fetchall()
            for row in rows:
                owner_token = uuid4().hex
                lease_version = int(row["lease_version"] or 0) + 1
                lease_expires_at = reference_time + timedelta(seconds=lease_seconds)
                updated = connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'LEASED', lease_owner_token = ?, lease_version = ?,
                        lease_expires_at = ?, updated_at = ?
                    WHERE notification_id = ?
                      AND lease_version = ?
                      AND (
                          (status IN ('PENDING', 'RETRY_WAIT')
                           AND (next_attempt_at IS NULL OR next_attempt_at <= ?))
                          OR (status = 'LEASED' AND lease_expires_at IS NOT NULL
                              AND lease_expires_at <= ?)
                      )
                    """,
                    (
                        owner_token,
                        lease_version,
                        _datetime_to_text(lease_expires_at),
                        reference_text,
                        row["notification_id"],
                        row["lease_version"],
                        reference_text,
                        reference_text,
                    ),
                ).rowcount
                if updated == 1:
                    claimed_row = connection.execute(
                        "SELECT * FROM notification_outbox WHERE notification_id = ?",
                        (row["notification_id"],),
                    ).fetchone()
                    if claimed_row is not None:
                        claimed.append(_row_to_notification_outbox(claimed_row))
            connection.commit()
            return claimed
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    # Short aliases make the repository usable by workers without coupling
    # them to the historical notification_logs naming.
    lease_pending_notifications = claim_notification_outbox

    def renew_notification_outbox_lease(
        self,
        notification_id: str,
        *,
        owner_token: str,
        lease_version: int,
        now: datetime | None = None,
        lease_seconds: int = 60,
    ) -> bool:
        if lease_seconds <= 0 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reference_time = now or self._clock()
            changed = connection.execute(
                """
                UPDATE notification_outbox
                SET lease_expires_at = ?, updated_at = ?
                WHERE notification_id = ?
                  AND status IN ('LEASED', 'SENDING')
                  AND lease_owner_token = ?
                  AND lease_version = ?
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at > ?
                """,
                (
                    _datetime_to_text(
                        reference_time + timedelta(seconds=lease_seconds)
                    ),
                    _datetime_to_text(reference_time),
                    notification_id,
                    owner_token,
                    int(lease_version),
                    _datetime_to_text(reference_time),
                ),
            ).rowcount
            connection.commit()
            return changed == 1
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    renew_notification_lease = renew_notification_outbox_lease

    def begin_notification_delivery(
        self,
        notification_id: str,
        *,
        owner_token: str,
        lease_version: int,
        request_fingerprint: str,
        now: datetime | None = None,
    ) -> NotificationDeliveryAttempt:
        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reference_time = now or self._clock()
            reference_text = _datetime_to_text(reference_time)
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            if (
                row is None
                or str(row["status"]) != "LEASED"
                or str(row["lease_owner_token"] or "") != owner_token
                or int(row["lease_version"] or 0) != int(lease_version)
                or _coerce_datetime_for_comparison(
                    _text_to_datetime(row["lease_expires_at"]), reference_time
                )
                is None
                or _coerce_datetime_for_comparison(
                    _text_to_datetime(row["lease_expires_at"]), reference_time
                )
                <= reference_time
                or (
                    row["deadline_at"] is not None
                    and _coerce_datetime_for_comparison(
                        _text_to_datetime(row["deadline_at"]), reference_time
                    )
                    is not None
                    and _coerce_datetime_for_comparison(
                        _text_to_datetime(row["deadline_at"]), reference_time
                    )
                    <= reference_time
                )
                or int(row["attempt_count"] or 0) >= int(row["max_attempts"] or 0)
            ):
                connection.rollback()
                raise NotificationLeaseError(
                    "notification lease is not valid for sending"
                )

            attempt_no = int(row["attempt_count"] or 0) + 1
            attempt = NotificationDeliveryAttempt(
                delivery_attempt_id=uuid4().hex,
                notification_id=notification_id,
                attempt_no=attempt_no,
                status="STARTED",
                lease_owner_token=owner_token,
                lease_version=int(lease_version),
                request_fingerprint=str(request_fingerprint),
                started_at=reference_time,
            )
            changed = connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'SENDING', attempt_count = attempt_count + 1,
                    next_attempt_at = NULL, updated_at = ?
                WHERE notification_id = ? AND status = 'LEASED'
                  AND lease_owner_token = ? AND lease_version = ?
                """,
                (reference_text, notification_id, owner_token, int(lease_version)),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise NotificationLeaseError(
                    "notification lease was fenced before sending"
                )
            connection.execute(
                """
                INSERT INTO notification_delivery_attempts(
                    delivery_attempt_id, notification_id, attempt_no, status,
                    lease_owner_token, lease_version, request_fingerprint, started_at,
                    completed_at, provider_status_code, provider_message_id,
                    response_fingerprint, error_code, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, '', '', '', '', '')
                """,
                (
                    attempt.delivery_attempt_id,
                    attempt.notification_id,
                    attempt.attempt_no,
                    attempt.status,
                    attempt.lease_owner_token,
                    attempt.lease_version,
                    attempt.request_fingerprint,
                    reference_text,
                ),
            )

            connection.commit()
            return attempt
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    start_notification_delivery = begin_notification_delivery

    def complete_notification_delivery(
        self,
        notification_id: str,
        delivery_attempt_id: str,
        *,
        owner_token: str,
        lease_version: int,
        result: NotificationDeliveryResult,
        now: datetime | None = None,
    ) -> NotificationOutbox:
        classification = str(
            getattr(result.classification, "value", result.classification)
        ).upper()
        if classification not in {"SUCCESS", "TEMP_FAILED", "PERM_FAILED", "UNKNOWN"}:
            raise ValueError(
                f"unsupported delivery classification: {result.classification}"
            )
        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reference_time = now or self._clock()
            reference_text = _datetime_to_text(reference_time)
            error_message = _sanitize_persisted_error(result.error_message)
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            attempt = connection.execute(
                "SELECT * FROM notification_delivery_attempts WHERE delivery_attempt_id = ?",
                (delivery_attempt_id,),
            ).fetchone()
            if (
                row is None
                or attempt is None
                or str(row["status"]) != "SENDING"
                or str(row["lease_owner_token"] or "") != owner_token
                or int(row["lease_version"] or 0) != int(lease_version)
                or str(attempt["notification_id"]) != notification_id
                or str(attempt["status"]) != "STARTED"
                or str(attempt["lease_owner_token"]) != owner_token
                or int(attempt["lease_version"]) != int(lease_version)
            ):
                connection.rollback()
                raise NotificationDeliveryError(
                    "notification delivery result was fenced"
                )

            lease_expires_at = _coerce_datetime_for_comparison(
                _text_to_datetime(row["lease_expires_at"]), reference_time
            )
            deadline_at = _coerce_datetime_for_comparison(
                _text_to_datetime(row["deadline_at"]), reference_time
            )
            if (
                lease_expires_at is None
                or lease_expires_at <= reference_time
                or (deadline_at is not None and deadline_at <= reference_time)
            ):
                # Leave SENDING + STARTED evidence intact.  The watchdog owns
                # the safe transition to UNKNOWN_DELIVERY after this point.
                connection.rollback()
                raise NotificationDeliveryError(
                    "notification delivery lease or deadline expired before writeback"
                )

            if classification == "SUCCESS":
                attempt_status = "ACKNOWLEDGED"
                outbox_status = "SENT"
                next_attempt_at = None
                sent_at = reference_text
                last_error_code = ""
                last_error_message = ""
            elif classification == "UNKNOWN":
                attempt_status = "UNKNOWN"
                outbox_status = "UNKNOWN_DELIVERY"
                next_attempt_at = None
                sent_at = None
                last_error_code = str(result.error_code or "UNKNOWN_DELIVERY")[:200]
                last_error_message = error_message
            elif classification == "PERM_FAILED":
                attempt_status = "PERM_FAILED"
                outbox_status = "FAILED"
                next_attempt_at = None
                sent_at = None
                last_error_code = str(result.error_code or "PERM_FAILED")[:200]
                last_error_message = error_message
            else:
                attempt_status = "TEMP_FAILED"
                deadline = _coerce_datetime_for_comparison(
                    _text_to_datetime(row["deadline_at"]), reference_time
                )
                max_attempts_reached = int(row["attempt_count"] or 0) >= int(
                    row["max_attempts"] or 0
                )
                if deadline is not None and deadline <= reference_time:
                    outbox_status = "EXPIRED"
                    next_attempt_at = None
                    last_error_code = str(result.error_code or "DEADLINE_EXPIRED")[:200]
                elif max_attempts_reached:
                    outbox_status = "FAILED"
                    next_attempt_at = None
                    last_error_code = str(result.error_code or "MAX_ATTEMPTS")[:200]
                else:
                    outbox_status = "RETRY_WAIT"
                    retry_seconds = result.retry_after_seconds
                    if retry_seconds is None:
                        retry_seconds = min(
                            300, 2 ** max(0, int(row["attempt_count"] or 1) - 1)
                        )
                    retry_seconds = max(0, min(int(retry_seconds), 3600))
                    next_attempt_at = _datetime_to_text(
                        reference_time + timedelta(seconds=retry_seconds)
                    )
                    last_error_code = str(result.error_code or "TEMP_FAILED")[:200]
                sent_at = None
                last_error_message = error_message

            attempt_changed = connection.execute(
                """
                UPDATE notification_delivery_attempts
                SET status = ?, completed_at = ?, provider_status_code = ?,
                    provider_message_id = ?, response_fingerprint = ?, error_code = ?, error_message = ?
                WHERE delivery_attempt_id = ? AND notification_id = ? AND status = 'STARTED'
                  AND lease_owner_token = ? AND lease_version = ?
                """,
                (
                    attempt_status,
                    reference_text,
                    str(result.provider_status_code or "")[:100],
                    str(result.provider_message_id or "")[:200],
                    str(result.response_fingerprint or "")[:200],
                    str(result.error_code or "")[:200],
                    error_message,
                    delivery_attempt_id,
                    notification_id,
                    owner_token,
                    int(lease_version),
                ),
            ).rowcount
            if attempt_changed != 1:
                connection.rollback()
                raise NotificationDeliveryError(
                    "notification attempt writeback was fenced"
                )
            changed = connection.execute(
                """
                UPDATE notification_outbox
                SET status = ?, next_attempt_at = ?, lease_owner_token = '',
                    lease_expires_at = NULL, sent_at = ?, provider_message_id = ?,
                    last_error_code = ?, last_error_message = ?, updated_at = ?
                WHERE notification_id = ? AND status = 'SENDING'
                  AND lease_owner_token = ? AND lease_version = ?
                """,
                (
                    outbox_status,
                    next_attempt_at,
                    sent_at,
                    str(result.provider_message_id or "")[:200],
                    last_error_code,
                    last_error_message,
                    reference_text,
                    notification_id,
                    owner_token,
                    int(lease_version),
                ),
            ).rowcount
            if changed != 1:
                connection.rollback()
                raise NotificationDeliveryError("notification writeback was fenced")
            if outbox_status == "SENT":
                compatibility_status = "success"
                compatibility_sent_at = _text_to_datetime(sent_at)
            elif outbox_status in {"PENDING", "LEASED", "SENDING", "RETRY_WAIT"}:
                compatibility_status = "pending"
                compatibility_sent_at = None
            else:
                compatibility_status = "failed"
                compatibility_sent_at = None
            self._update_notification_log_delivery_on_connection(
                connection,
                notification_id,
                send_status=compatibility_status,
                sent_at=compatibility_sent_at,
                error_message=last_error_message,
            )
            connection.commit()
            final_row = connection.execute(
                "SELECT * FROM notification_outbox WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            if final_row is None:
                raise NotificationDeliveryError(
                    "notification disappeared after writeback"
                )
            return _row_to_notification_outbox(final_row)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    record_notification_delivery = complete_notification_delivery

    def list_notification_delivery_attempts(
        self,
        notification_id: str,
    ) -> list[NotificationDeliveryAttempt]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM notification_delivery_attempts
                WHERE notification_id = ?
                ORDER BY attempt_no ASC
                """,
                (notification_id,),
            ).fetchall()
        return [_row_to_notification_delivery_attempt(row) for row in rows]

    def recover_expired_notification_leases(
        self,
        *,
        now: datetime | None = None,
    ) -> list[NotificationOutbox]:
        """Requeue safe LEASED rows and fence expired SENDING rows as UNKNOWN."""

        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reference_time = now or self._clock()
            reference_text = _datetime_to_text(reference_time)
            deadline_rows = connection.execute(
                """
                SELECT notification_id FROM notification_outbox
                WHERE status IN ('LEASED', 'RETRY_WAIT', 'PENDING')
                  AND deadline_at IS NOT NULL AND deadline_at <= ?
                """,
                (reference_text,),
            ).fetchall()
            for row in deadline_rows:
                notification_id = str(row["notification_id"])
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'EXPIRED', lease_owner_token = '', lease_expires_at = NULL,
                        last_error_code = 'DEADLINE_EXPIRED',
                        last_error_message = 'notification deadline expired', updated_at = ?
                    WHERE notification_id = ? AND status IN ('LEASED', 'RETRY_WAIT', 'PENDING')
                    """,
                    (reference_text, notification_id),
                )
                self._update_notification_log_delivery_on_connection(
                    connection,
                    notification_id,
                    send_status="failed",
                    error_message="notification deadline expired",
                )
            leased_rows = connection.execute(
                """
                SELECT notification_id FROM notification_outbox
                WHERE status = 'LEASED' AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                  AND (deadline_at IS NULL OR deadline_at > ?)
                """,
                (reference_text, reference_text),
            ).fetchall()
            for row in leased_rows:
                notification_id = str(row["notification_id"])
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'PENDING', lease_owner_token = '', lease_expires_at = NULL,
                        updated_at = ?
                    WHERE notification_id = ? AND status = 'LEASED'
                    """,
                    (reference_text, notification_id),
                )
                self._update_notification_log_delivery_on_connection(
                    connection,
                    notification_id,
                    send_status="pending",
                )
            sending_rows = connection.execute(
                """
                SELECT notification_id, lease_owner_token, lease_version
                FROM notification_outbox
                WHERE status = 'SENDING' AND lease_expires_at IS NOT NULL
                  AND (lease_expires_at <= ? OR (deadline_at IS NOT NULL AND deadline_at <= ?))
                """,
                (reference_text, reference_text),
            ).fetchall()
            for row in sending_rows:
                connection.execute(
                    """
                    UPDATE notification_delivery_attempts
                    SET status = 'UNKNOWN', completed_at = ?,
                        error_code = 'UNKNOWN_DELIVERY_WATCHDOG',
                        error_message = 'worker lease or deadline expired while sending'
                    WHERE notification_id = ? AND status = 'STARTED'
                      AND lease_owner_token = ? AND lease_version = ?
                    """,
                    (
                        reference_text,
                        row["notification_id"],
                        row["lease_owner_token"],
                        row["lease_version"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE notification_outbox
                    SET status = 'UNKNOWN_DELIVERY', lease_owner_token = '',
                        lease_expires_at = NULL, last_error_code = 'UNKNOWN_DELIVERY_WATCHDOG',
                        last_error_message = 'worker lease or deadline expired while sending', updated_at = ?
                    WHERE notification_id = ? AND status = 'SENDING'
                    """,
                    (reference_text, row["notification_id"]),
                )
                self._update_notification_log_delivery_on_connection(
                    connection,
                    str(row["notification_id"]),
                    send_status="failed",
                    error_message="worker lease or deadline expired while sending",
                )
            changed_ids = (
                [row["notification_id"] for row in leased_rows]
                + [row["notification_id"] for row in sending_rows]
                + [row["notification_id"] for row in deadline_rows]
            )
            changed_ids = list(dict.fromkeys(changed_ids))
            connection.commit()
            if not changed_ids:
                return []
            placeholders = ",".join("?" for _ in changed_ids)
            rows = connection.execute(
                f"SELECT * FROM notification_outbox WHERE notification_id IN ({placeholders}) "
                "ORDER BY created_at, notification_id",
                changed_ids,
            ).fetchall()
            return [_row_to_notification_outbox(row) for row in rows]
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    watchdog_notification_leases = recover_expired_notification_leases

    def cancel_notification_outbox(
        self, notification_id: str, *, now: datetime | None = None
    ) -> bool:
        connection = self.connect_write()
        try:
            connection.execute("BEGIN IMMEDIATE")
            reference_time = now or self._clock()
            reference_text = _datetime_to_text(reference_time)
            changed = connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'CANCELLED', lease_owner_token = '', lease_expires_at = NULL,
                    updated_at = ?
                WHERE notification_id = ? AND status IN ('PENDING', 'RETRY_WAIT', 'LEASED')
                """,
                (reference_text, notification_id),
            ).rowcount
            if changed == 1:
                self._update_notification_log_delivery_on_connection(
                    connection,
                    notification_id,
                    send_status="failed",
                    error_message="notification cancelled",
                )
            connection.commit()
            return changed == 1
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def insert_script_run(self, script_run: ScriptRun) -> int:
        row = _script_run_to_row(script_run)
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO script_runs(
                    script_run_id, evaluator_id, evaluator_name, description, run_mode,
                    run_status, trade_date, started_at, finished_at, summary_json,
                    error_message, created_by
                )
                VALUES(
                    :script_run_id, :evaluator_id, :evaluator_name, :description, :run_mode,
                    :run_status, :trade_date, :started_at, :finished_at, :summary_json,
                    :error_message, :created_by
                )
                """,
                row,
            )
            return connection.total_changes - before

    def update_script_run(self, script_run: ScriptRun) -> None:
        row = _script_run_to_row(script_run)
        with closing(self.connect()) as connection, connection:
            connection.execute(
                """
                UPDATE script_runs
                SET run_status = :run_status,
                    finished_at = :finished_at,
                    summary_json = :summary_json,
                    error_message = :error_message
                WHERE script_run_id = :script_run_id
                """,
                row,
            )

    def list_script_runs(self, *, limit: int | None = None) -> list[ScriptRun]:
        query = "SELECT * FROM script_runs ORDER BY started_at DESC, script_run_id ASC"
        params: list[object] = []
        if limit is not None:
            query = f"{query} LIMIT ?"
            params.append(limit)
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [_row_to_script_run(row) for row in rows]

    def get_script_run(self, script_run_id: str) -> ScriptRun | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM script_runs WHERE script_run_id = ?",
                (script_run_id,),
            ).fetchone()
        return _row_to_script_run(row) if row is not None else None

    def insert_script_run_items(self, items: Iterable[ScriptRunItem]) -> int:
        rows = [_script_run_item_to_row(item) for item in items]
        if not rows:
            return 0
        with closing(self.connect()) as connection, connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO script_run_items(
                    item_id, script_run_id, proposal_type, dedupe_key, severity,
                    item_status, message, payload_json, decision_trace_json,
                    related_task_id, related_review_task_id, related_notification_id,
                    error_message, created_at
                )
                VALUES(
                    :item_id, :script_run_id, :proposal_type, :dedupe_key, :severity,
                    :item_status, :message, :payload_json, :decision_trace_json,
                    :related_task_id, :related_review_task_id, :related_notification_id,
                    :error_message, :created_at
                )
                """,
                rows,
            )
            return connection.total_changes - before

    def list_script_run_items(self, script_run_id: str) -> list[ScriptRunItem]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM script_run_items
                WHERE script_run_id = ?
                ORDER BY created_at ASC, item_id ASC
                """,
                (script_run_id,),
            ).fetchall()
        return [_row_to_script_run_item(row) for row in rows]


def _task_to_row(task: Task) -> dict[str, Any]:
    created_at = task.created_at
    updated_at = task.updated_at or created_at
    return {
        "task_id": task.task_id,
        "trade_date": _date_to_text(task.trade_date),
        "scope_type": task.scope_type,
        "scope_key": task.scope_key,
        "dedupe_key": task.dedupe_key,
        "internal_sku": task.internal_sku,
        "platform_name": task.platform_name,
        "action_type": task.action_type.value,
        "priority": task.priority,
        "task_status": task.task_status.value,
        "created_at": _datetime_to_text(created_at),
        "scheduled_at": _datetime_to_text(task.scheduled_at),
        "expires_at": _datetime_to_text(task.expires_at),
        "expected_old_price": serialize_decimal(task.expected_old_price),
        "target_price": serialize_decimal(task.target_price),
        "target_inventory": task.target_inventory,
        "target_status": task.target_status,
        "pricing_source": task.pricing_source.value if task.pricing_source else None,
        "decision_trace_json": _json_dump(task.decision_trace),
        "result_message": task.result_message,
        "required_by": _datetime_to_text(task.required_by),
        "updated_at": _datetime_to_text(updated_at),
        "origin_type": task.origin_type.value,
        "origin_ref_id": task.origin_ref_id,
        "approval_policy": task.approval_policy,
        "policy_version": task.policy_version,
        "platform_trade_date": _date_to_text(task.platform_trade_date),
        "seller_operation_date": _date_to_text(task.seller_operation_date),
        "seller_phase": task.seller_phase.value if task.seller_phase else None,
        "time_policy_version": task.time_policy_version,
    }


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        task_id=str(row["task_id"]),
        internal_sku=row["internal_sku"],
        platform_name=row["platform_name"],
        action_type=TaskActionType(str(row["action_type"])),
        priority=int(row["priority"]),
        task_status=TaskStatus(str(row["task_status"])),
        created_at=_text_to_datetime(row["created_at"]) or datetime.now(),
        expected_old_price=Decimal(str(row["expected_old_price"]))
        if row["expected_old_price"] not in ("", None)
        else None,
        target_price=Decimal(str(row["target_price"]))
        if row["target_price"] not in ("", None)
        else None,
        target_inventory=(
            int(row["target_inventory"])
            if "target_inventory" in row.keys()
            and row["target_inventory"] not in ("", None)
            else None
        ),
        target_status=row["target_status"],
        pricing_source=PricingSource(str(row["pricing_source"]))
        if row["pricing_source"] not in ("", None)
        else None,
        decision_trace=_json_load(row["decision_trace_json"]),
        result_message=str(row["result_message"] or ""),
        required_by=_text_to_datetime(row["required_by"]),
        trade_date=_text_to_date(row["trade_date"]),
        origin_type=TaskOriginType(str(row["origin_type"])),
        origin_ref_id=row["origin_ref_id"],
        approval_policy=str(row["approval_policy"] or "UNSPECIFIED"),
        policy_version=row["policy_version"],
        platform_trade_date=_text_to_date(row["platform_trade_date"]),
        seller_operation_date=_text_to_date(row["seller_operation_date"]),
        seller_phase=(
            SellerPhase(str(row["seller_phase"]))
            if row["seller_phase"] not in ("", None)
            else None
        ),
        time_policy_version=row["time_policy_version"],
        scope_type=str(row["scope_type"]),
        scope_key=str(row["scope_key"]),
        dedupe_key=str(row["dedupe_key"] or ""),
        scheduled_at=_text_to_datetime(row["scheduled_at"]),
        expires_at=_text_to_datetime(row["expires_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
    )


def _listing_status_from_row(row: sqlite3.Row) -> ListingStatus:
    return ListingStatus(
        listing_status_id=str(row["listing_status_id"]),
        platform_name=str(row["platform_name"]),
        internal_sku=str(row["internal_sku"]),
        variety=str(row["variety"]),
        current_price=Decimal(str(row["current_price"])),
        grade=str(row["grade"]),
        platform_stock_qty=int(row["platform_stock_qty"]),
        sold_qty=int(row["sold_qty"]),
        online_status=str(row["online_status"]),
        source=str(row["source"]),
        updated_at=_text_to_datetime(row["updated_at"]),
        inventory_source=str(row["inventory_source"]),
        inventory_observed_at=_text_to_datetime(row["inventory_observed_at"]),
        inventory_source_attempt_id=str(row["inventory_source_attempt_id"]),
        price_source=str(row["price_source"]),
        price_observed_at=_text_to_datetime(row["price_observed_at"]),
        price_source_attempt_id=str(row["price_source_attempt_id"]),
        last_listing_change_at=_text_to_datetime(row["last_listing_change_at"]),
        last_listing_operation_id=str(row["last_listing_operation_id"] or ""),
        online_status_observed_at=_text_to_datetime(row["online_status_observed_at"]),
        online_status_source_type=str(row["online_status_source_type"] or ""),
        online_status_source_id=str(row["online_status_source_id"] or ""),
    )


def _review_task_to_row(review_task: ReviewTask) -> dict[str, Any]:
    created_at = review_task.created_at or datetime.now()
    updated_at = review_task.updated_at or created_at
    return {
        "review_task_id": review_task.review_task_id,
        "trade_date": _date_to_text(review_task.trade_date),
        "scope_type": review_task.scope_type,
        "scope_key": review_task.scope_key,
        "dedupe_key": review_task.dedupe_key,
        "source_task_id": review_task.source_task_id,
        "review_type": review_task.review_type,
        "review_status": review_task.review_status.value,
        "internal_sku": review_task.internal_sku,
        "platform_name": review_task.platform_name,
        "reason": review_task.reason,
        "review_payload_json": _json_dump(review_task.review_payload),
        "resolution_payload_json": _json_dump(review_task.resolution_payload),
        "required_by": _datetime_to_text(review_task.required_by),
        "created_at": _datetime_to_text(created_at),
        "updated_at": _datetime_to_text(updated_at),
        "resolved_by": review_task.resolved_by,
        "resolved_at": _datetime_to_text(review_task.resolved_at),
        "resolution_note": review_task.resolution_note,
    }


def _row_to_review_task(row: sqlite3.Row) -> ReviewTask:
    return ReviewTask(
        review_task_id=str(row["review_task_id"]),
        trade_date=_text_to_date(row["trade_date"]),
        scope_type=str(row["scope_type"]),
        scope_key=str(row["scope_key"]),
        dedupe_key=str(row["dedupe_key"] or ""),
        source_task_id=row["source_task_id"],
        review_type=str(row["review_type"]),
        review_status=ReviewTaskStatus(str(row["review_status"])),
        internal_sku=row["internal_sku"],
        platform_name=row["platform_name"],
        reason=str(row["reason"] or ""),
        review_payload=_json_load(row["review_payload_json"]),
        resolution_payload=_json_load(row["resolution_payload_json"]),
        required_by=_text_to_datetime(row["required_by"]),
        created_at=_text_to_datetime(row["created_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
        resolved_by=str(row["resolved_by"] or ""),
        resolved_at=_text_to_datetime(row["resolved_at"]),
        resolution_note=str(row["resolution_note"] or ""),
    )


def _review_token_to_row(review_token: ReviewToken) -> dict[str, Any]:
    created_at = review_token.created_at or datetime.now()
    return {
        "token_id": review_token.token_id,
        "review_task_id": review_token.review_task_id,
        "token_hash": review_token.token_hash,
        "token_subject": review_token.token_subject,
        "allowed_actions": _json_dump(review_token.allowed_actions),
        "expires_at": _datetime_to_text(review_token.expires_at),
        "used_at": _datetime_to_text(review_token.used_at),
        "revoked_at": _datetime_to_text(review_token.revoked_at),
        "created_at": _datetime_to_text(created_at),
        "created_by": review_token.created_by,
        "last_used_at": _datetime_to_text(review_token.last_used_at),
        "note": review_token.note,
    }


def _row_to_review_token(row: sqlite3.Row) -> ReviewToken:
    return ReviewToken(
        token_id=str(row["token_id"]),
        review_task_id=str(row["review_task_id"]),
        token_hash=str(row["token_hash"]),
        token_subject=str(row["token_subject"]),
        allowed_actions=_json_list_load(row["allowed_actions"]),
        expires_at=_text_to_datetime(row["expires_at"]) or datetime.now(),
        used_at=_text_to_datetime(row["used_at"]),
        revoked_at=_text_to_datetime(row["revoked_at"]),
        created_at=_text_to_datetime(row["created_at"]),
        created_by=str(row["created_by"]),
        last_used_at=_text_to_datetime(row["last_used_at"]),
        note=row["note"],
    )


def _execution_log_to_row(log: ExecutionLog) -> dict[str, Any]:
    return {
        "log_id": log.log_id,
        "task_id": log.task_id,
        "executor_name": log.executor_name,
        "start_time": _datetime_to_text(log.start_time),
        "end_time": _datetime_to_text(log.end_time),
        "success_flag": int(log.success_flag) if log.success_flag is not None else None,
        "error_code": log.error_code,
        "error_message": log.error_message,
        "raw_output": log.raw_output,
        "ai_model_version": log.ai_model_version,
        "ai_summary": log.ai_summary,
        "created_at": _datetime_to_text(log.created_at or datetime.now()),
    }


def _row_to_execution_log(row: sqlite3.Row) -> ExecutionLog:
    success_flag = row["success_flag"]
    return ExecutionLog(
        log_id=str(row["log_id"]),
        task_id=str(row["task_id"]),
        executor_name=str(row["executor_name"]),
        start_time=_text_to_datetime(row["start_time"]) or datetime.now(),
        end_time=_text_to_datetime(row["end_time"]),
        success_flag=bool(success_flag) if success_flag is not None else None,
        error_code=str(row["error_code"] or ""),
        error_message=str(row["error_message"] or ""),
        raw_output=str(row["raw_output"] or ""),
        ai_model_version=str(row["ai_model_version"] or ""),
        ai_summary=str(row["ai_summary"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
    )


def _shadowbot_operation_to_row(operation: ShadowBotOperationLedger) -> dict[str, Any]:
    created_at = operation.created_at or datetime.now()
    updated_at = operation.updated_at or created_at
    return {
        "operation_id": operation.operation_id,
        "task_id": operation.task_id,
        "platform": operation.platform,
        "product_identity_json": _json_dump(operation.product_identity),
        "expected_old_price": serialize_decimal(operation.expected_old_price),
        "target_price": serialize_decimal(operation.target_price),
        "status": operation.status,
        "lock_owner": operation.lock_owner,
        "approved_payload_hash": operation.approved_payload_hash,
        "created_at": _datetime_to_text(created_at),
        "updated_at": _datetime_to_text(updated_at),
    }


def _ensure_column(
    connection: sqlite3.Connection, table: str, column: str, declaration: str
) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _backfill_task_expected_old_price(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT task_id, decision_trace_json
        FROM tasks
        WHERE action_type = ?
          AND (expected_old_price IS NULL OR expected_old_price = '')
        """,
        (TaskActionType.UPDATE_PRICE.value,),
    ).fetchall()
    for row in rows:
        trace = _json_load(row["decision_trace_json"])
        steps = trace.get("rule_steps") if isinstance(trace, dict) else None
        if not isinstance(steps, list):
            continue
        old_price_text = ""
        for step in steps:
            match = re.fullmatch(r"old_price=(.+)", str(step).strip())
            if match:
                old_price_text = match.group(1).strip()
                break
        if not old_price_text:
            continue
        try:
            old_price = Decimal(old_price_text)
        except Exception:
            continue
        if not old_price.is_finite() or old_price < 0:
            continue
        connection.execute(
            "UPDATE tasks SET expected_old_price = ? WHERE task_id = ?",
            (serialize_decimal(old_price), str(row["task_id"])),
        )


def _backfill_commit_item_identities(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT item.rowid AS item_rowid, item.batch_id, item.source_task_id,
               item.internal_sku, item.expected_product_name, item.expected_grade,
               item.expected_old_price, item.target_price, batch.platform_name,
               item.item_id, item.operation_id, item.item_execution_attempt_id,
               item.write_identity_key, item.page_identity_key
        FROM shadowbot_commit_batch_items AS item
        JOIN shadowbot_commit_batches AS batch ON batch.batch_id = item.batch_id
        """
    ).fetchall()
    for row in rows:
        batch_id = str(row["batch_id"])
        task_id = str(row["source_task_id"])
        item_digest = hashlib.sha256(
            f"{batch_id}\0{task_id}".encode("utf-8")
        ).hexdigest()
        payload_digest = hashlib.sha256(
            "\0".join(
                (
                    task_id,
                    str(row["internal_sku"]).upper(),
                    str(row["expected_old_price"]),
                    str(row["target_price"]),
                )
            ).encode("utf-8")
        ).hexdigest()
        item_id = str(row["item_id"] or "") or f"ITEM-{item_digest[:32]}"
        operation_id = str(row["operation_id"] or "") or f"OP-{payload_digest[:32]}"
        item_attempt_id = (
            str(row["item_execution_attempt_id"] or "") or f"ATTEMPT-{item_digest[:32]}"
        )
        write_identity_key = str(row["write_identity_key"] or "") or (
            f"{normalize_listing_text(row['platform_name'])}|"
            f"{str(row['internal_sku']).strip().upper()}"
        )
        page_identity_key = str(row["page_identity_key"] or "") or "|".join(
            (
                normalize_listing_text(row["platform_name"]),
                normalize_listing_text(row["expected_product_name"]),
                normalize_listing_text(row["expected_grade"]).upper(),
            )
        )
        connection.execute(
            """
            UPDATE shadowbot_commit_batch_items
            SET item_id = ?, operation_id = ?, item_execution_attempt_id = ?,
                write_identity_key = ?, page_identity_key = ?
            WHERE rowid = ?
            """,
            (
                item_id,
                operation_id,
                item_attempt_id,
                write_identity_key,
                page_identity_key,
                int(row["item_rowid"]),
            ),
        )


def _row_to_shadowbot_operation(row: sqlite3.Row) -> ShadowBotOperationLedger:
    return ShadowBotOperationLedger(
        operation_id=str(row["operation_id"]),
        task_id=str(row["task_id"]),
        platform=str(row["platform"]),
        product_identity=_json_load(row["product_identity_json"]),
        expected_old_price=Decimal(str(row["expected_old_price"])),
        target_price=Decimal(str(row["target_price"])),
        status=str(row["status"]),
        lock_owner=str(row["lock_owner"] or ""),
        approved_payload_hash=str(row["approved_payload_hash"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
    )


def _shadowbot_attempt_to_row(attempt: ShadowBotExecutionAttempt) -> dict[str, Any]:
    return {
        "execution_attempt_id": attempt.execution_attempt_id,
        "operation_id": attempt.operation_id,
        "execution_mode": attempt.execution_mode,
        "shadowbot_run_id": attempt.shadowbot_run_id,
        "status": attempt.status,
        "side_effect_state": attempt.side_effect_state,
        "started_at": _datetime_to_text(attempt.started_at),
        "instruction_hash": attempt.instruction_hash,
        "request_file_sha256": attempt.request_file_sha256,
        "queue_request_path": attempt.queue_request_path,
        "ended_at": _datetime_to_text(attempt.ended_at),
        "raw_output_json": _json_dump(attempt.raw_output),
    }


def _row_to_shadowbot_attempt(row: sqlite3.Row) -> ShadowBotExecutionAttempt:
    return ShadowBotExecutionAttempt(
        execution_attempt_id=str(row["execution_attempt_id"]),
        operation_id=str(row["operation_id"]),
        execution_mode=str(row["execution_mode"]),
        shadowbot_run_id=str(row["shadowbot_run_id"] or ""),
        status=str(row["status"]),
        side_effect_state=str(row["side_effect_state"]),
        started_at=_text_to_datetime(row["started_at"]) or datetime.now(),
        instruction_hash=str(row["instruction_hash"] or ""),
        request_file_sha256=str(row["request_file_sha256"] or ""),
        queue_request_path=str(row["queue_request_path"] or ""),
        ended_at=_text_to_datetime(row["ended_at"]),
        raw_output=_json_load(row["raw_output_json"]),
    )


def _row_to_shadowbot_checkpoint(row: sqlite3.Row) -> ShadowBotSideEffectCheckpoint:
    return ShadowBotSideEffectCheckpoint(
        operation_id=str(row["operation_id"]),
        execution_attempt_id=str(row["execution_attempt_id"]),
        side_effect_state=str(row["side_effect_state"]),
        checkpoint_at=_text_to_datetime(row["checkpoint_at"]) or datetime.now(),
        version=int(row["version"]),
    )


def _retry_authorization_to_row(authorization: RetryAuthorization) -> dict[str, Any]:
    created_at = authorization.created_at or datetime.now()
    expires_at = authorization.expires_at or created_at
    return {
        "retry_authorization_id": authorization.retry_authorization_id,
        "operation_id": authorization.operation_id,
        "source_execution_attempt_id": authorization.source_execution_attempt_id,
        "authorization_type": authorization.authorization_type,
        "authorized_by": authorization.authorized_by,
        "evidence_type": authorization.evidence_type,
        "evidence_hash": authorization.evidence_hash,
        "approved_payload_hash": authorization.approved_payload_hash,
        "status": authorization.status,
        "max_uses": authorization.max_uses,
        "consumed_by_execution_attempt_id": authorization.consumed_by_execution_attempt_id,
        "expires_at": _datetime_to_text(expires_at),
        "reason": authorization.reason,
        "created_at": _datetime_to_text(created_at),
        "consumed_at": _datetime_to_text(authorization.consumed_at),
    }


def _row_to_retry_authorization(row: sqlite3.Row) -> RetryAuthorization:
    return RetryAuthorization(
        retry_authorization_id=str(row["retry_authorization_id"]),
        operation_id=str(row["operation_id"]),
        source_execution_attempt_id=str(row["source_execution_attempt_id"]),
        authorization_type=str(row["authorization_type"]),
        authorized_by=str(row["authorized_by"]),
        evidence_type=str(row["evidence_type"]),
        evidence_hash=str(row["evidence_hash"]),
        approved_payload_hash=str(row["approved_payload_hash"]),
        status=str(row["status"]),
        max_uses=int(row["max_uses"]),
        consumed_by_execution_attempt_id=(
            str(row["consumed_by_execution_attempt_id"])
            if row["consumed_by_execution_attempt_id"] is not None
            else None
        ),
        expires_at=_text_to_datetime(row["expires_at"]),
        reason=str(row["reason"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
        consumed_at=_text_to_datetime(row["consumed_at"]),
    )


def _notification_log_to_row(log: NotificationLog) -> dict[str, Any]:
    return {
        "notification_id": log.notification_id,
        "related_task_id": log.related_task_id,
        "related_review_task_id": log.related_review_task_id,
        "recipient_type": log.recipient_type,
        "recipient": log.recipient,
        "channel": log.channel,
        "sent_at": _datetime_to_text(log.sent_at),
        "send_status": log.send_status,
        "dedupe_key": log.dedupe_key,
        "message": log.message,
        "error_message": log.error_message,
        "created_at": _datetime_to_text(log.created_at or datetime.now()),
    }


def _row_to_notification_log(row: sqlite3.Row) -> NotificationLog:
    return NotificationLog(
        notification_id=str(row["notification_id"]),
        related_task_id=row["related_task_id"],
        related_review_task_id=row["related_review_task_id"],
        recipient_type=str(row["recipient_type"]),
        recipient=str(row["recipient"]),
        channel=str(row["channel"]),
        sent_at=_text_to_datetime(row["sent_at"]),
        send_status=str(row["send_status"]),
        dedupe_key=str(row["dedupe_key"] or ""),
        message=str(row["message"] or ""),
        error_message=str(row["error_message"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
    )


def _notification_outbox_to_row(notification: NotificationOutbox) -> dict[str, Any]:
    created_at = notification.created_at or datetime.now()
    updated_at = notification.updated_at or created_at
    return {
        "notification_id": notification.notification_id,
        "notification_key": notification.notification_key,
        "notification_type": notification.notification_type,
        "related_task_id": notification.related_task_id,
        "related_review_task_id": notification.related_review_task_id,
        "recipient_type": notification.recipient_type,
        "recipient_ref": notification.recipient_ref,
        "channel": notification.channel,
        "priority": int(notification.priority),
        "payload_json": _json_dump(notification.payload),
        "status": str(getattr(notification.status, "value", notification.status)),
        "attempt_count": int(notification.attempt_count),
        "max_attempts": int(notification.max_attempts),
        "next_attempt_at": _datetime_to_text(notification.next_attempt_at),
        "deadline_at": _datetime_to_text(notification.deadline_at),
        "lease_owner_token": notification.lease_owner_token,
        "lease_version": int(notification.lease_version),
        "lease_expires_at": _datetime_to_text(notification.lease_expires_at),
        "sent_at": _datetime_to_text(notification.sent_at),
        "provider_message_id": notification.provider_message_id,
        "last_error_code": notification.last_error_code,
        "last_error_message": notification.last_error_message,
        "created_at": _datetime_to_text(created_at),
        "updated_at": _datetime_to_text(updated_at),
    }


def _row_to_notification_outbox(row: sqlite3.Row) -> NotificationOutbox:
    return NotificationOutbox(
        notification_id=str(row["notification_id"]),
        notification_key=str(row["notification_key"]),
        notification_type=str(row["notification_type"]),
        related_task_id=row["related_task_id"],
        related_review_task_id=row["related_review_task_id"],
        recipient_type=str(row["recipient_type"]),
        recipient_ref=str(row["recipient_ref"]),
        channel=str(row["channel"]),
        priority=int(row["priority"]),
        payload=_json_load(row["payload_json"]),
        status=str(row["status"]),
        attempt_count=int(row["attempt_count"]),
        max_attempts=int(row["max_attempts"]),
        next_attempt_at=_text_to_datetime(row["next_attempt_at"]),
        deadline_at=_text_to_datetime(row["deadline_at"]),
        lease_owner_token=str(row["lease_owner_token"] or ""),
        lease_version=int(row["lease_version"] or 0),
        lease_expires_at=_text_to_datetime(row["lease_expires_at"]),
        sent_at=_text_to_datetime(row["sent_at"]),
        provider_message_id=str(row["provider_message_id"] or ""),
        last_error_code=str(row["last_error_code"] or ""),
        last_error_message=str(row["last_error_message"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
        updated_at=_text_to_datetime(row["updated_at"]),
    )


def _row_to_notification_delivery_attempt(
    row: sqlite3.Row,
) -> NotificationDeliveryAttempt:
    return NotificationDeliveryAttempt(
        delivery_attempt_id=str(row["delivery_attempt_id"]),
        notification_id=str(row["notification_id"]),
        attempt_no=int(row["attempt_no"]),
        status=str(row["status"]),
        lease_owner_token=str(row["lease_owner_token"]),
        lease_version=int(row["lease_version"]),
        request_fingerprint=str(row["request_fingerprint"]),
        started_at=_text_to_datetime(row["started_at"]) or datetime.now(),
        completed_at=_text_to_datetime(row["completed_at"]),
        provider_status_code=str(row["provider_status_code"] or ""),
        provider_message_id=str(row["provider_message_id"] or ""),
        response_fingerprint=str(row["response_fingerprint"] or ""),
        error_code=str(row["error_code"] or ""),
        error_message=str(row["error_message"] or ""),
    )


def _script_run_to_row(script_run: ScriptRun) -> dict[str, Any]:
    return {
        "script_run_id": script_run.script_run_id,
        "evaluator_id": script_run.evaluator_id,
        "evaluator_name": script_run.evaluator_name,
        "description": script_run.description,
        "run_mode": script_run.run_mode,
        "run_status": script_run.run_status,
        "trade_date": _date_to_text(script_run.trade_date),
        "started_at": _datetime_to_text(script_run.started_at),
        "finished_at": _datetime_to_text(script_run.finished_at),
        "summary_json": _json_dump(script_run.summary),
        "error_message": script_run.error_message,
        "created_by": script_run.created_by,
    }


def _row_to_script_run(row: sqlite3.Row) -> ScriptRun:
    return ScriptRun(
        script_run_id=str(row["script_run_id"]),
        evaluator_id=str(row["evaluator_id"]),
        evaluator_name=str(row["evaluator_name"]),
        description=str(row["description"] or ""),
        run_mode=str(row["run_mode"]),
        run_status=str(row["run_status"]),
        trade_date=_text_to_date(row["trade_date"]),
        started_at=_text_to_datetime(row["started_at"]) or datetime.now(),
        finished_at=_text_to_datetime(row["finished_at"]),
        summary=_json_load(row["summary_json"]),
        error_message=str(row["error_message"] or ""),
        created_by=str(row["created_by"] or "system"),
    )


def _script_run_item_to_row(item: ScriptRunItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "script_run_id": item.script_run_id,
        "proposal_type": item.proposal_type,
        "dedupe_key": item.dedupe_key,
        "severity": item.severity,
        "item_status": item.item_status,
        "message": item.message,
        "payload_json": _json_dump(item.payload),
        "decision_trace_json": _json_dump(item.decision_trace),
        "related_task_id": item.related_task_id,
        "related_review_task_id": item.related_review_task_id,
        "related_notification_id": item.related_notification_id,
        "error_message": item.error_message,
        "created_at": _datetime_to_text(item.created_at or datetime.now()),
    }


def _row_to_script_run_item(row: sqlite3.Row) -> ScriptRunItem:
    return ScriptRunItem(
        item_id=str(row["item_id"]),
        script_run_id=str(row["script_run_id"]),
        proposal_type=str(row["proposal_type"]),
        dedupe_key=str(row["dedupe_key"]),
        severity=str(row["severity"]),
        item_status=str(row["item_status"]),
        message=str(row["message"] or ""),
        payload=_json_load(row["payload_json"]),
        decision_trace=_json_load(row["decision_trace_json"]),
        related_task_id=row["related_task_id"],
        related_review_task_id=row["related_review_task_id"],
        related_notification_id=row["related_notification_id"],
        error_message=str(row["error_message"] or ""),
        created_at=_text_to_datetime(row["created_at"]),
    )


def _row_to_status_history(row: sqlite3.Row) -> TaskStatusHistory:
    return TaskStatusHistory(
        history_id=str(row["history_id"]),
        task_id=str(row["task_id"]),
        from_status=TaskStatus(str(row["from_status"]))
        if row["from_status"] not in ("", None)
        else None,
        to_status=TaskStatus(str(row["to_status"])),
        changed_by=str(row["changed_by"]),
        changed_at=_text_to_datetime(row["changed_at"]) or datetime.now(),
        reason=str(row["reason"] or ""),
        metadata=_json_load(row["metadata_json"]),
    )


def _is_sqlite_concurrency_error(error: sqlite3.OperationalError) -> bool:
    """Classify SQLite busy/locked errors without inspecting localized text."""

    return is_sqlite_concurrency_error(error)


def _atomic_source_task_status(
    source_row: sqlite3.Row | None,
    status: ReviewTaskStatus,
) -> TaskStatus | None:
    if source_row is None:
        return None
    current_status = TaskStatus(str(source_row["task_status"]))
    if current_status == TaskStatus.MANUAL_REVIEW:
        if status in {ReviewTaskStatus.APPROVED, ReviewTaskStatus.ADJUSTED}:
            return TaskStatus.PENDING
        if status == ReviewTaskStatus.CANCELLED:
            return TaskStatus.CANCELLED
        return TaskStatus.SKIPPED
    if current_status == TaskStatus.PENDING:
        action_type = TaskActionType(str(source_row["action_type"]))
        if action_type in MANUAL_REVIEW_SOURCE_ACTIONS:
            if status == ReviewTaskStatus.CANCELLED:
                return TaskStatus.CANCELLED
            if status == ReviewTaskStatus.ADJUSTED:
                return TaskStatus.PENDING
            return TaskStatus.SKIPPED
    return None


def _date_to_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _text_to_date(value: str | None) -> date | None:
    if value in ("", None):
        return None
    return date.fromisoformat(str(value))


def _with_limit_offset(
    query: str,
    params: list[object],
    *,
    limit: int | None,
    offset: int,
) -> tuple[str, list[object]]:
    """Append validated SQLite pagination without changing legacy list defaults."""

    normalized_offset = int(offset)
    if normalized_offset < 0:
        raise ValueError("offset must be greater than or equal to zero")
    if limit is None:
        if normalized_offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(normalized_offset)
        return query, params
    normalized_limit = int(limit)
    if normalized_limit < 1:
        return f"SELECT * FROM ({query}) WHERE 0", params
    query += " LIMIT ? OFFSET ?"
    params.extend((normalized_limit, normalized_offset))
    return query, params


def _text_to_datetime(value: str | None) -> datetime | None:
    if value in ("", None):
        return None
    return datetime.fromisoformat(str(value))


def _coerce_datetime_for_comparison(
    value: datetime | None, reference: datetime
) -> datetime | None:
    """Compare legacy naive timestamps with current timezone-aware timestamps safely."""

    if value is None:
        return None
    if value.tzinfo is None and reference.tzinfo is not None:
        return value.replace(tzinfo=reference.tzinfo)
    if value.tzinfo is not None and reference.tzinfo is None:
        return value.replace(tzinfo=None)
    return value


def _timestamp_for_deadline(
    *,
    provided: datetime | None,
    deadline: datetime | None,
) -> datetime:
    """Return a timestamp comparable with a persisted naive or aware deadline."""

    if deadline is None or deadline.tzinfo is None or deadline.utcoffset() is None:
        if provided is None:
            return datetime.now()
        return provided.replace(tzinfo=None)
    if provided is None:
        return datetime.now(deadline.tzinfo)
    if provided.tzinfo is None or provided.utcoffset() is None:
        return provided.replace(tzinfo=deadline.tzinfo)
    return provided.astimezone(deadline.tzinfo)


def _sanitize_persisted_error(value: object) -> str:
    """Persist only a bounded, credential-free provider error summary."""

    text = str(value or "")
    text = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)(authorization|cookie|password|access[_-]?token|webhook[_-]?url)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"https?://\S+", "[URL_REDACTED]", text)
    return text[:1000]


def _json_dump(value: Any) -> str:
    return json.dumps(
        {} if value is None else value, ensure_ascii=False, sort_keys=True
    )


def _json_load(value: str | None) -> dict[str, Any]:
    if value in ("", None):
        return {}
    loaded = json.loads(str(value))
    return loaded if isinstance(loaded, dict) else {}


def _json_list_load(value: str | None) -> list[str]:
    if value in ("", None):
        return []
    loaded = json.loads(str(value))
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]
