from __future__ import annotations

import json
from datetime import datetime
from uuid import uuid4

from app.repositories.sqlite_runtime_repository import SQLiteRuntimeRepository


def seed_scheduled_listing_scan(
    repository: SQLiteRuntimeRepository,
    *,
    platform_name: str,
    observed_at: datetime,
    items: tuple[tuple[str, bool], ...],
    run_status: str = "SUCCESS",
    batch_status: str = "ACCEPTED",
    scope_complete: bool = True,
    end_marker_verified: bool = True,
) -> tuple[str, str]:
    suffix = uuid4().hex[:12]
    run_id = f"RUN-LISTING-{suffix}"
    batch_id = f"OBS-LISTING-{suffix}"
    snapshot_id = f"SNAPSHOT-LISTING-{suffix}"
    timestamp = observed_at.isoformat()
    with repository.connect_write() as connection:
        policy = connection.execute(
            """
            SELECT policy_version
            FROM operational_time_policies
            WHERE effective_to IS NULL
            ORDER BY effective_from DESC
            LIMIT 1
            """
        ).fetchone()
        job = connection.execute(
            """
            SELECT job_id
            FROM automation_jobs
            WHERE job_type = 'LISTING_STATUS_SCAN'
            ORDER BY job_id
            LIMIT 1
            """
        ).fetchone()
        if policy is None:
            raise AssertionError("listing scan fixture requires an active time policy")
        if job is None:
            job_id = "SYNTHETIC-LISTING-STATUS-SCAN"
            connection.execute(
                """
                INSERT INTO automation_jobs(
                    job_id, job_type, display_name, enabled, schedule_kind,
                    schedule_expression, priority, config_json,
                    created_at, updated_at
                ) VALUES (?, 'LISTING_STATUS_SCAN', '合成商品状态扫描', 0,
                          'CHILD_ONLY', '-', 50, '{}', ?, ?)
                """,
                (job_id, timestamp, timestamp),
            )
        else:
            job_id = str(job["job_id"])
        policy_version = str(policy["policy_version"])
        trade_date = observed_at.date().isoformat()
        connection.execute(
            """
            INSERT INTO automation_runs(
                run_id, job_id, job_type, logical_run_key, run_status,
                platform_name, platform_trade_date, seller_operation_date,
                seller_phase, time_policy_version, scheduled_for, started_at,
                finished_at, input_manifest_sha256, output_manifest_sha256,
                created_at, updated_at
            ) VALUES (?, ?, 'LISTING_STATUS_SCAN', ?, ?, ?, ?, ?,
                      'NORMAL_SALES', ?, ?, ?, ?, '', '', ?, ?)
            """,
            (
                run_id,
                job_id,
                f"synthetic-listing-scan:{suffix}",
                run_status,
                platform_name,
                trade_date,
                trade_date,
                policy_version,
                timestamp,
                timestamp,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO product_observation_batches(
                observation_batch_id, automation_run_id, platform_name,
                scan_type, batch_status, scan_started_at, scan_completed_at,
                requested_scope_json, scope_complete, end_marker_verified,
                content_sha256, time_policy_version, error_code,
                error_message, created_at
            ) VALUES (?, ?, ?, 'LISTING_STATUS_SCAN', ?, ?, ?, ?, ?, ?, ?, ?,
                      '', '', ?)
            """,
            (
                batch_id,
                run_id,
                platform_name,
                batch_status,
                timestamp,
                timestamp,
                json.dumps(
                    {
                        "pages": ["online", "waiting"],
                        "source_snapshot_id": snapshot_id,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                int(scope_complete),
                int(end_marker_verified),
                "sha256:" + suffix.ljust(64, "0"),
                policy_version,
                timestamp,
            ),
        )
        for index, (internal_sku, observed_online) in enumerate(items, start=1):
            listing = connection.execute(
                """
                SELECT variety, grade
                FROM listing_status
                WHERE platform_name = ? AND internal_sku = ?
                """,
                (platform_name, internal_sku),
            ).fetchone()
            if listing is None:
                raise AssertionError(f"missing synthetic listing for {internal_sku}")
            connection.execute(
                """
                INSERT INTO product_observation_items(
                    observation_item_id, observation_batch_id, internal_sku,
                    platform_product_name, grade, observed_online, observed_at,
                    platform_trade_date, seller_operation_date, seller_phase,
                    page_identity_key, mapping_status, mapping_version,
                    evidence_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'NORMAL_SALES', ?,
                          'VERIFIED', 'synthetic-mapping', ?)
                """,
                (
                    f"OBS-ITEM-{suffix}-{index}",
                    batch_id,
                    internal_sku,
                    str(listing["variety"]),
                    str(listing["grade"]),
                    int(observed_online),
                    timestamp,
                    trade_date,
                    trade_date,
                    f"synthetic:{internal_sku}",
                    "sha256:" + f"{index:x}".ljust(64, "0"),
                ),
            )
            connection.execute(
                """
                UPDATE listing_status
                SET online_status = ?, online_status_observed_at = ?,
                    online_status_source_type = 'LISTING_SYNC_SNAPSHOT',
                    online_status_source_id = ?, updated_at = ?
                WHERE platform_name = ? AND internal_sku = ?
                """,
                (
                    "online" if observed_online else "offline",
                    timestamp,
                    snapshot_id,
                    timestamp,
                    platform_name,
                    internal_sku,
                ),
            )
        connection.commit()
    return run_id, batch_id
