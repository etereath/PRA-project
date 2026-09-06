from __future__ import annotations

import hashlib
import json
from contextlib import closing
from datetime import date, datetime, timedelta

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository
from app.services.operational_time import (
    DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
)


def insert_cutover_order_snapshot(
    runtime: SQLiteRuntimeRepository,
    *,
    batch_id: str,
    observed_at: datetime,
    platform_trade_date: date,
    platform_name: str = "platform",
    time_policy_version: str = DEFAULT_OPERATIONAL_TIME_POLICY_VERSION,
    order_quantities: tuple[int, ...] = (),
    trade_day_status: str = "OPEN",
    capability_result: str = "SUCCEEDED",
    batch_status: str = "ACCEPTED",
    source_batch_status: str = "ACCEPTED",
    scope_complete: bool = True,
    end_marker_verified: bool = True,
) -> str:
    job_id = f"job-{batch_id}"
    run_id = f"run-{batch_id}"
    timestamp = observed_at.isoformat()
    content_sha256 = _sha256(batch_id)
    requested_range = json.dumps(
        {
            "source_batch_status": source_batch_status,
            "accepted_mapping_version": "mapping-v1",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with closing(runtime.connect_write()) as connection, connection:
        connection.execute(
            """
            INSERT INTO automation_jobs(
                job_id, job_type, display_name, enabled,
                schedule_kind, schedule_expression, priority,
                config_json, created_at, updated_at
            ) VALUES (?, 'ORDER_SCAN', ?, 0, 'CHILD_ONLY', '-', 51, ?, ?, ?)
            """,
            (
                job_id,
                job_id,
                json.dumps(
                    {"platform_name": platform_name},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO automation_runs(
                run_id, job_id, job_type, logical_run_key, run_status,
                platform_name, platform_trade_date, seller_operation_date,
                seller_phase, time_policy_version, scheduled_for,
                created_at, updated_at
            ) VALUES (
                ?, ?, 'ORDER_SCAN', ?, 'SUCCESS', ?, ?, ?,
                'NORMAL_SALES', ?, ?, ?, ?
            )
            """,
            (
                run_id,
                job_id,
                f"logical-{batch_id}",
                platform_name,
                platform_trade_date.isoformat(),
                platform_trade_date.isoformat(),
                time_policy_version,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO order_observation_batches(
                observation_batch_id, automation_run_id, platform_name,
                requested_platform_trade_date, trade_day_status,
                capability_result, batch_status, scan_started_at,
                scan_completed_at, requested_range_json, scope_complete,
                end_marker_verified, content_sha256, time_policy_version,
                error_code, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?)
            """,
            (
                batch_id,
                run_id,
                platform_name,
                platform_trade_date.isoformat(),
                trade_day_status,
                capability_result,
                batch_status,
                (observed_at - timedelta(minutes=1)).isoformat(),
                timestamp,
                requested_range,
                int(scope_complete),
                int(end_marker_verified),
                content_sha256,
                time_policy_version,
                timestamp,
            ),
        )
        for occurrence_no, quantity in enumerate(order_quantities, start=1):
            connection.execute(
                """
                INSERT INTO order_observation_items(
                    observation_item_id, observation_batch_id,
                    platform_name, platform_trade_date, trade_day_status,
                    order_identity_fingerprint, occurrence_no,
                    order_created_at, platform_product_name, grade,
                    internal_sku, mapping_status, mapping_version,
                    order_qty, order_transaction_amount, observed_at,
                    seller_operation_date, seller_phase,
                    raw_observation_sha256
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, 'Synthetic Rose', 'A',
                    'AISHA-A-50-Z', 'VERIFIED', 'mapping-v1',
                    ?, ?, ?, ?, 'NORMAL_SALES', ?
                )
                """,
                (
                    f"item-{batch_id}-{occurrence_no}",
                    batch_id,
                    platform_name,
                    platform_trade_date.isoformat(),
                    trade_day_status,
                    _sha256(f"fingerprint-{batch_id}-{occurrence_no}"),
                    occurrence_no,
                    (observed_at - timedelta(minutes=2)).isoformat(),
                    quantity,
                    str(quantity * 10),
                    timestamp,
                    platform_trade_date.isoformat(),
                    _sha256(f"raw-{batch_id}-{occurrence_no}"),
                ),
            )
    return batch_id


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
