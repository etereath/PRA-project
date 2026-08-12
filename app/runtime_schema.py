"""Authoritative runtime SQLite schema metadata and health checks.

This module intentionally contains the only latest-runtime-schema version
constant used by the application.  Repository migrations and operational
health checks both consume the metadata here so a migration row cannot make a
database look healthy when its physical structure is incomplete.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

LATEST_RUNTIME_SCHEMA_VERSION = 17
RUNTIME_SCHEMA_VERSIONS = tuple(range(1, LATEST_RUNTIME_SCHEMA_VERSION + 1))

REQUIRED_RUNTIME_TABLES = frozenset(
    {
        "runtime_schema_migrations",
        "tasks",
        "review_tasks",
        "review_tokens",
        "execution_logs",
        "notification_logs",
        "task_status_history",
        "script_runs",
        "script_run_items",
        "shadowbot_operations",
        "shadowbot_execution_attempts",
        "shadowbot_side_effect_checkpoints",
        "retry_authorizations",
        "notification_outbox",
        "notification_delivery_attempts",
        "listing_status",
        "shadowbot_commit_batches",
        "shadowbot_commit_batch_items",
        "shadowbot_write_locks",
        "shadowbot_commit_result_receipts",
        "shadowbot_batch_registry",
        "shadowbot_listing_action_batches",
        "shadowbot_listing_action_batch_items",
        "shadowbot_listing_result_receipts",
        "listing_sync_snapshots",
        "listing_sync_snapshot_items",
        "listing_anomaly_cases",
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
        "operational_incident_events",
        "incident_notification_state",
        "emergency_offline_policies",
        "inventory_authority_state",
        "inventory_balances",
        "inventory_transactions",
        "inventory_sales_baselines",
        "inventory_alert_policies",
    }
)

V14_APPEND_ONLY_TABLES = (
    "product_observation_batches",
    "product_observation_items",
    "order_observation_batches",
    "order_observation_items",
    "sales_estimate_segments",
    "platform_trade_day_summary_events",
    "platform_trade_day_summary_inputs",
)

V15_APPEND_ONLY_TABLES = ("operational_incident_events",)
V17_APPEND_ONLY_TABLES = ("inventory_transactions",)

V7_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "listing_status": (
        "listing_status_id",
        "platform_name",
        "internal_sku",
        "variety",
        "current_price",
        "platform_stock_qty",
        "sold_qty",
        "online_status",
        "source",
        "updated_at",
    ),
}

V8_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "listing_status": (
        "inventory_source",
        "inventory_observed_at",
        "inventory_source_attempt_id",
    ),
}

V9_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "listing_status": ("grade",),
}

V10_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "tasks": ("expected_old_price",),
}

V11_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "shadowbot_commit_batches": (
        "batch_id",
        "contract_version",
        "execution_profile",
        "platform_name",
        "manifest_sha256",
        "instruction_hash",
        "execution_attempt_id",
        "result_id",
        "status",
        "created_at",
        "updated_at",
    ),
    "shadowbot_commit_batch_items": (
        "batch_id",
        "source_task_id",
        "internal_sku",
        "expected_product_name",
        "expected_grade",
        "expected_old_price",
        "target_price",
        "item_payload_sha256",
        "preflight_row",
        "preflight_price",
        "execution_ordinal",
        "submit_attempted",
        "actual_price",
        "status",
        "error_code",
        "error_message",
        "updated_at",
    ),
}

V12_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "shadowbot_commit_batch_items": (
        "item_id",
        "operation_id",
        "item_execution_attempt_id",
        "write_identity_key",
        "page_identity_key",
        "side_effect_state",
        "preflight_observed_at",
        "submit_intent_at",
        "submit_clicked_at",
        "readback_observed_at",
    ),
    "shadowbot_write_locks": (
        "write_identity_key",
        "operation_id",
        "item_execution_attempt_id",
        "batch_id",
        "status",
        "acquired_at",
        "released_at",
        "updated_at",
    ),
    "shadowbot_commit_result_receipts": (
        "result_id",
        "batch_id",
        "execution_attempt_id",
        "instruction_hash",
        "manifest_sha256",
        "result_sha256",
        "source_result_path",
        "accepted_at",
        "ack_state",
        "ack_updated_at",
        "last_projection_error",
    ),
}

V12_INDEX_SPECS: Mapping[str, tuple[str, ...]] = {
    "ux_shadowbot_commit_batch_items_item_id": ("item_id",),
    "ix_shadowbot_commit_batch_items_operation_id": ("operation_id",),
    "ux_shadowbot_commit_batch_items_attempt_id": ("item_execution_attempt_id",),
    "ux_shadowbot_write_locks_operation_id": ("operation_id",),
    "ix_shadowbot_commit_result_receipts_batch_id": ("batch_id",),
    "ix_shadowbot_commit_result_receipts_ack_state": ("ack_state", "accepted_at"),
}

V13_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "tasks": ("target_inventory",),
    "listing_status": (
        "price_source",
        "price_observed_at",
        "price_source_attempt_id",
        "last_listing_change_at",
        "last_listing_operation_id",
        "online_status_observed_at",
        "online_status_source_type",
        "online_status_source_id",
    ),
    "shadowbot_operations": (
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
    ),
    "shadowbot_batch_registry": (
        "batch_id",
        "batch_type",
        "contract_version",
        "platform_name",
        "created_at",
    ),
    "shadowbot_listing_action_batches": (
        "batch_id",
        "contract_version",
        "execution_profile",
        "action_type",
        "platform_name",
        "manifest_sha256",
        "instruction_hash",
        "execution_attempt_id",
        "result_id",
        "status",
        "batch_target_count",
        "verified_count",
        "unknown_count",
        "partial_effect_count",
        "not_attempted_count",
        "failed_count",
        "created_at",
        "updated_at",
    ),
    "shadowbot_listing_action_batch_items": (
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
    ),
    "shadowbot_listing_result_receipts": (
        "result_id",
        "batch_id",
        "execution_attempt_id",
        "instruction_hash",
        "manifest_sha256",
        "result_sha256",
        "source_result_path",
        "accepted_at",
        "ack_state",
        "ack_updated_at",
        "last_projection_error",
    ),
    "listing_sync_snapshots": (
        "snapshot_id",
        "batch_id",
        "platform_name",
        "execution_attempt_id",
        "scan_started_at",
        "scan_completed_at",
        "online_scan_started_at",
        "online_scan_completed_at",
        "waiting_scan_started_at",
        "waiting_scan_completed_at",
        "online_scan_complete",
        "waiting_scan_complete",
        "snapshot_complete",
        "online_end_marker_verified",
        "waiting_end_marker_verified",
        "instruction_hash",
        "result_id",
        "status",
        "error_code",
        "evidence_manifest_sha256",
    ),
    "listing_sync_snapshot_items": (
        "snapshot_item_id",
        "snapshot_id",
        "internal_sku",
        "product_name",
        "grade",
        "page_identity_key",
        "online_occurrences",
        "waiting_occurrences",
        "listing_location",
        "online_row_identities_json",
        "waiting_row_identities_json",
        "online_observed_price",
        "waiting_observed_price",
        "online_observed_inventory",
        "waiting_observed_inventory",
        "diagnostic_code",
        "online_observed_at",
        "waiting_observed_at",
    ),
    "listing_anomaly_cases": (
        "anomaly_case_id",
        "snapshot_id",
        "snapshot_item_id",
        "platform_name",
        "internal_sku",
        "page_identity_key",
        "affected_internal_skus_json",
        "anomaly_subject_key",
        "dedupe_key",
        "reason_code",
        "diagnostic_message",
        "resolution_policy",
        "blocked_actions_json",
        "created_at",
        "cleared_at",
        "cleared_by_snapshot_id",
        "review_task_id",
    ),
}

V13_INDEX_SPECS: Mapping[str, tuple[str, ...]] = {
    "ix_shadowbot_batch_registry_type": ("batch_type", "created_at"),
    "ix_shadowbot_listing_action_batches_status": ("status", "created_at"),
    "ux_shadowbot_listing_action_batch_items_batch_sku": (
        "batch_id",
        "internal_sku",
    ),
    "ux_shadowbot_listing_action_batch_items_operation_id": ("operation_id",),
    "ux_shadowbot_listing_action_batch_items_attempt_id": (
        "item_execution_attempt_id",
    ),
    "ix_shadowbot_listing_result_receipts_batch_id": ("batch_id",),
    "ix_listing_sync_snapshots_platform_completed": (
        "platform_name",
        "scan_completed_at",
    ),
    "ix_listing_sync_snapshot_items_snapshot": ("snapshot_id",),
    "ix_listing_sync_snapshot_items_internal_sku": (
        "internal_sku",
        "snapshot_id",
    ),
    "ux_listing_anomaly_cases_open_dedupe": ("dedupe_key",),
    "ix_listing_anomaly_cases_snapshot": ("snapshot_id",),
    "ix_listing_anomaly_cases_review": ("review_task_id",),
}

V14_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "tasks": (
        "origin_type",
        "origin_ref_id",
        "approval_policy",
        "policy_version",
        "platform_trade_date",
        "seller_operation_date",
        "seller_phase",
        "time_policy_version",
    ),
    "operational_time_policies": (
        "policy_version",
        "timezone_name",
        "platform_cutoff_local_time",
        "seller_cutoff_local_time",
        "peak_start_local_time",
        "effective_from",
        "effective_to",
        "created_at",
        "created_by",
        "supersedes_policy_version",
    ),
    "automation_jobs": (
        "job_id",
        "job_type",
        "display_name",
        "enabled",
        "schedule_kind",
        "schedule_expression",
        "priority",
        "config_json",
        "created_at",
        "updated_at",
    ),
    "automation_runs": (
        "run_id",
        "job_id",
        "job_type",
        "logical_run_key",
        "run_status",
        "platform_name",
        "platform_trade_date",
        "seller_operation_date",
        "seller_phase",
        "time_policy_version",
        "scheduled_for",
        "started_at",
        "finished_at",
        "lease_owner",
        "lease_version",
        "lease_expires_at",
        "input_manifest_sha256",
        "output_manifest_sha256",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
    ),
    "automation_run_events": (
        "event_id",
        "run_id",
        "event_type",
        "from_status",
        "to_status",
        "payload_json",
        "created_at",
    ),
    "automation_run_links": (
        "parent_run_id",
        "child_run_id",
        "relation_type",
        "created_at",
    ),
    "product_observation_batches": (
        "observation_batch_id",
        "automation_run_id",
        "platform_name",
        "scan_type",
        "batch_status",
        "scan_started_at",
        "scan_completed_at",
        "requested_scope_json",
        "scope_complete",
        "end_marker_verified",
        "content_sha256",
        "time_policy_version",
        "error_code",
        "error_message",
        "created_at",
    ),
    "product_observation_items": (
        "observation_item_id",
        "observation_batch_id",
        "internal_sku",
        "platform_product_name",
        "grade",
        "observed_price",
        "observed_inventory",
        "observed_online",
        "observed_at",
        "platform_trade_date",
        "seller_operation_date",
        "seller_phase",
        "page_identity_key",
        "mapping_status",
        "mapping_version",
        "evidence_sha256",
    ),
    "order_observation_batches": (
        "observation_batch_id",
        "automation_run_id",
        "platform_name",
        "requested_platform_trade_date",
        "trade_day_status",
        "capability_result",
        "batch_status",
        "scan_started_at",
        "scan_completed_at",
        "requested_range_json",
        "scope_complete",
        "end_marker_verified",
        "content_sha256",
        "time_policy_version",
        "error_code",
        "error_message",
        "created_at",
    ),
    "order_observation_items": (
        "observation_item_id",
        "observation_batch_id",
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
    ),
    "sales_estimate_segments": (
        "estimate_segment_id",
        "platform_name",
        "internal_sku",
        "platform_trade_date",
        "interval_started_at",
        "interval_ended_at",
        "inventory_before",
        "inventory_after",
        "known_inventory_adjustment",
        "known_adjustment_source_refs_json",
        "estimated_sold_qty",
        "estimation_eligible",
        "estimation_reason",
        "quality_level",
        "mapping_version",
        "supporting_observation_ids_json",
        "algorithm_version",
        "created_at",
    ),
    "platform_trade_day_summaries": (
        "summary_id",
        "summary_series_id",
        "version_no",
        "supersedes_summary_id",
        "is_current",
        "platform_name",
        "platform_trade_date",
        "seller_operation_date",
        "seller_phase",
        "scope_type",
        "scope_key",
        "fact_source",
        "quality_level",
        "summary_status",
        "sold_qty",
        "order_count",
        "transaction_amount_total",
        "quality_reason",
        "source_proportions_json",
        "input_manifest_sha256",
        "mapping_version",
        "algorithm_version",
        "time_policy_version",
        "finalized_at",
        "created_at",
        "updated_at",
    ),
    "platform_trade_day_summary_events": (
        "event_id",
        "summary_id",
        "from_status",
        "to_status",
        "trigger_type",
        "trigger_ref_id",
        "fact_source_before",
        "fact_source_after",
        "quality_level_before",
        "quality_level_after",
        "input_manifest_sha256",
        "changed_at",
        "changed_by",
        "reason",
    ),
    "platform_trade_day_summary_inputs": (
        "summary_id",
        "input_manifest_sha256",
        "input_type",
        "input_ref_id",
        "input_sha256",
        "created_at",
    ),
    "operational_incidents": (
        "incident_id",
        "dedupe_key",
        "category",
        "source_type",
        "source_ref_id",
        "severity",
        "incident_status",
        "blocks_finalization",
        "platform_name",
        "platform_trade_date",
        "seller_operation_date",
        "subject_type",
        "subject_key",
        "title",
        "description",
        "first_detected_at",
        "last_detected_at",
        "resolved_at",
        "created_at",
        "updated_at",
    ),
    "incident_notification_state": (
        "incident_id",
        "channel",
        "notification_count",
        "last_notified_at",
        "next_notification_at",
        "escalation_state",
        "payload_sha256",
        "updated_at",
    ),
}

V15_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "operational_incidents": ("occurrence_count",),
    "operational_incident_events": (
        "event_id",
        "event_key",
        "incident_id",
        "event_type",
        "occurred_at",
        "source_type",
        "source_ref_id",
        "from_status",
        "to_status",
        "severity",
        "event_payload_json",
        "created_at",
    ),
}

V16_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "emergency_offline_policies": (
        "policy_version",
        "platform_name",
        "emergency_ratio",
        "approved_by",
        "approved_at",
        "created_at",
        "retired_at",
    ),
}

V17_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "inventory_authority_state": (
        "authority_key",
        "authority_mode",
        "bootstrap_snapshot_sha256",
        "bootstrap_idempotency_key",
        "bootstrap_completed_at",
        "bootstrap_completed_by",
        "version",
        "created_at",
        "updated_at",
    ),
    "inventory_balances": (
        "internal_sku",
        "current_qty",
        "version",
        "last_transaction_id",
        "updated_at",
    ),
    "inventory_transactions": (
        "transaction_id",
        "internal_sku",
        "inventory_before",
        "inventory_delta",
        "inventory_after",
        "transaction_type",
        "source_type",
        "source_ref_id",
        "reason",
        "actor",
        "seller_operation_date",
        "platform_name",
        "platform_trade_date",
        "supporting_refs_json",
        "idempotency_key",
        "request_sha256",
        "balance_version_after",
        "occurred_at",
        "recorded_at",
    ),
    "inventory_sales_baselines": (
        "platform_name",
        "platform_trade_date",
        "internal_sku",
        "selected_fact_source",
        "quality_level",
        "selected_sold_qty",
        "source_ref_id",
        "source_sha256",
        "mapping_version",
        "supporting_refs_json",
        "inventory_transaction_id",
        "version",
        "updated_at",
    ),
    "inventory_alert_policies": (
        "policy_key",
        "scope_type",
        "scope_key",
        "enabled",
        "threshold_qty",
        "repeat_interval_minutes",
        "version",
        "updated_by",
        "created_at",
        "updated_at",
    ),
}

V14_INDEX_SPECS: Mapping[str, tuple[str, ...]] = {
    "ux_operational_time_policies_current": ("timezone_name",),
    "ix_automation_jobs_type_enabled": ("job_type", "enabled"),
    "ux_automation_runs_logical_key": ("logical_run_key",),
    "ix_automation_runs_status_scheduled": ("run_status", "scheduled_for"),
    "ix_automation_run_events_run": ("run_id", "created_at"),
    "ix_automation_run_links_child": ("child_run_id", "relation_type"),
    "ix_product_observation_batches_run": ("automation_run_id",),
    "ix_product_observation_items_batch": ("observation_batch_id",),
    "ix_product_observation_items_sku_trade_date": (
        "internal_sku",
        "platform_trade_date",
        "observed_at",
    ),
    "ix_order_observation_batches_run": ("automation_run_id",),
    "ix_order_observation_batches_trade_date": (
        "platform_name",
        "requested_platform_trade_date",
        "scan_completed_at",
    ),
    "ix_order_observation_items_batch": ("observation_batch_id",),
    "ix_order_observation_items_trade_date": (
        "platform_trade_date",
        "internal_sku",
        "order_created_at",
    ),
    "ix_sales_estimate_segments_scope": (
        "platform_name",
        "platform_trade_date",
        "internal_sku",
    ),
    "ux_trade_day_summaries_current": ("summary_series_id",),
    "ix_trade_day_summaries_scope": (
        "platform_name",
        "platform_trade_date",
        "scope_type",
        "scope_key",
    ),
    "ix_trade_day_summary_events_summary": ("summary_id", "changed_at"),
    "ix_trade_day_summary_inputs_ref": ("input_type", "input_ref_id"),
    "ux_operational_incidents_open_dedupe": ("dedupe_key",),
    "ix_operational_incidents_status": (
        "incident_status",
        "severity",
        "last_detected_at",
    ),
    "ix_incident_notification_state_due": ("next_notification_at",),
}

V15_INDEX_SPECS: Mapping[str, tuple[str, ...]] = {
    "ux_operational_incident_events_key": ("event_key",),
    "ix_operational_incident_events_incident": (
        "incident_id",
        "occurred_at",
        "event_id",
    ),
}

V16_INDEX_SPECS: Mapping[str, tuple[str, ...]] = {
    "ux_emergency_offline_policies_active": ("platform_name",),
}

V17_INDEX_SPECS: Mapping[str, tuple[str, ...]] = {
    "ix_inventory_transactions_sku_recorded": (
        "internal_sku",
        "recorded_at",
        "transaction_id",
    ),
    "ix_inventory_transactions_trade_date": (
        "platform_name",
        "platform_trade_date",
        "internal_sku",
    ),
    "ux_inventory_alert_policies_scope": ("scope_type", "scope_key"),
}

V14_TASK_ORIGIN_VALUES = frozenset(
    {"MANUAL", "AUTOMATION", "SYSTEM_EMERGENCY", "LEGACY"}
)
V14_AUTOMATION_RUN_STATUS_VALUES = frozenset(
    {
        "SCHEDULED",
        "RUNNING",
        "SUCCESS",
        "PARTIAL",
        "FAILED",
        "MISSED",
        "MERGED",
        "SKIPPED",
        "CANCELLED",
    }
)
V14_INCIDENT_CATEGORY_VALUES = frozenset(
    {
        "PLATFORM_LOGIN",
        "PLATFORM_NETWORK",
        "PAGE_STRUCTURE",
        "SCAN_INCOMPLETE",
        "WORKER_UNAVAILABLE",
        "QUEUE_BACKLOG",
        "PRODUCT_MAPPING",
        "PRICE_ANOMALY",
        "INVENTORY_ANOMALY",
        "ORDER_PAGE_UNAVAILABLE",
        "ORDER_DATA_INCONSISTENT",
        "SALES_ESTIMATE_LOW_CONFIDENCE",
        "NOTIFICATION_FAILURE",
        "WRITE_UNKNOWN",
    }
)
V15_INCIDENT_CATEGORY_VALUES = V14_INCIDENT_CATEGORY_VALUES | frozenset(
    {
        "AUTOMATION_SERVICE",
        "RUNTIME_STORAGE",
        "QUEUE_IMPORT",
        "TRADE_DAY_TIME",
        "LISTING_STATE",
        "MASTER_DATA",
        "SETTLEMENT_PROCESSING",
        "REVIEW_CHANNEL",
    }
)
V15_INCIDENT_EVENT_TYPE_VALUES = frozenset(
    {
        "DETECTED",
        "REDETECTED",
        "STATUS_CHANGED",
        "SEVERITY_CHANGED",
        "ACK",
        "RECOVERY_RECORDED",
        "REVIEW_RECORDED",
        "TASK_RECORDED",
    }
)
V14_INCIDENT_STATUS_VALUES = frozenset(
    {
        "OPEN",
        "RETRYING",
        "WAITING_HUMAN",
        "ACKNOWLEDGED",
        "AUTO_PROTECTING",
        "RESOLVED",
        "CLOSED",
    }
)
V14_FACT_SOURCE_VALUES = frozenset({"ORDER_OBSERVED", "SCAN_ESTIMATED"})
V14_QUALITY_VALUES = frozenset(
    {
        "ORDER_COMPLETE",
        "ORDER_PARTIAL",
        "SCAN_ESTIMATED_HIGH",
        "SCAN_ESTIMATED_MEDIUM",
        "SCAN_ESTIMATED_LOW",
        "UNAVAILABLE",
    }
)
V14_SUMMARY_STATUS_VALUES = frozenset(
    {"PROVISIONAL", "OBSERVED", "RECONCILED", "FINAL"}
)
V14_SELLER_PHASE_VALUES = frozenset(
    {"NORMAL_SALES", "PEAK_SALES", "DELIVERY_OVERLAP"}
)

V13_WRITE_LOCK_STATUS_VALUES = frozenset(
    {"ACTIVE", "UNKNOWN", "REVIEW_BLOCKED", "RELEASED"}
)
V13_OPERATION_ACTION_VALUES = frozenset(
    {"update_price", "set_online", "set_offline"}
)
V13_BATCH_TYPE_VALUES = frozenset(
    {"update_price", "set_online", "set_offline", "sync_status"}
)

V5_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "shadowbot_execution_attempts": (
        "instruction_hash",
        "request_file_sha256",
        "queue_request_path",
    ),
    "retry_authorizations": (
        "retry_authorization_id",
        "operation_id",
        "source_execution_attempt_id",
        "authorization_type",
        "authorized_by",
        "evidence_type",
        "evidence_hash",
        "approved_payload_hash",
        "status",
        "max_uses",
        "consumed_by_execution_attempt_id",
        "expires_at",
        "reason",
        "created_at",
        "consumed_at",
    ),
}

V6_REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "notification_outbox": (
        "notification_id",
        "notification_key",
        "notification_type",
        "related_task_id",
        "related_review_task_id",
        "recipient_type",
        "recipient_ref",
        "channel",
        "priority",
        "payload_json",
        "status",
        "attempt_count",
        "max_attempts",
        "next_attempt_at",
        "deadline_at",
        "lease_owner_token",
        "lease_version",
        "lease_expires_at",
        "sent_at",
        "provider_message_id",
        "last_error_code",
        "last_error_message",
        "created_at",
        "updated_at",
    ),
    "notification_delivery_attempts": (
        "delivery_attempt_id",
        "notification_id",
        "attempt_no",
        "status",
        "lease_owner_token",
        "lease_version",
        "request_fingerprint",
        "started_at",
        "completed_at",
        "provider_status_code",
        "provider_message_id",
        "response_fingerprint",
        "error_code",
        "error_message",
    ),
}

NOTIFICATION_OUTBOX_STATUS_VALUES = frozenset(
    {
        "PENDING",
        "LEASED",
        "SENDING",
        "RETRY_WAIT",
        "SENT",
        "UNKNOWN_DELIVERY",
        "FAILED",
        "EXPIRED",
        "CANCELLED",
    }
)
DELIVERY_ATTEMPT_STATUS_VALUES = frozenset(
    {"STARTED", "ACKNOWLEDGED", "TEMP_FAILED", "PERM_FAILED", "UNKNOWN"}
)

RETRY_AUTHORIZATION_STATUS_VALUES = frozenset({"ACTIVE", "CONSUMED", "EXPIRED", "REVOKED"})
RETRY_AUTHORIZATION_INDEX_SPECS: Mapping[str, tuple[tuple[str, ...], bool]] = {
    "ix_retry_authorizations_operation_id": (("operation_id",), False),
    "ix_retry_authorizations_status": (("status",), False),
    "ix_retry_authorizations_expires_at": (("expires_at",), False),
    "ux_retry_authorizations_evidence_hash": (("evidence_hash",), True),
    "ux_retry_authorizations_consumed_by_execution_attempt_id": (
        ("consumed_by_execution_attempt_id",),
        True,
    ),
}
RETRY_AUTHORIZATION_INDEXES = frozenset(RETRY_AUTHORIZATION_INDEX_SPECS)

NOTIFICATION_OUTBOX_INDEX_SPECS: Mapping[str, tuple[tuple[str, ...], bool]] = {
    "ux_notification_outbox_key": (("notification_key",), True),
    "ix_notification_outbox_claim": (
        ("status", "priority", "next_attempt_at", "deadline_at", "created_at"),
        False,
    ),
    "ix_notification_outbox_lease_expires_at": (("lease_expires_at",), False),
    "ix_notification_delivery_attempts_notification_id": (("notification_id",), False),
}
NOTIFICATION_OUTBOX_INDEXES = frozenset(NOTIFICATION_OUTBOX_INDEX_SPECS)


@dataclass(frozen=True, slots=True)
class RuntimeSchemaHealth:
    """Structured result returned by runtime schema health checks."""

    ok: bool
    actual_version: int | None
    applied_versions: tuple[int, ...]
    version_matches: bool
    missing_tables: tuple[str, ...]
    missing_columns: Mapping[str, tuple[str, ...]]
    missing_indexes: tuple[str, ...]
    constraint_errors: tuple[str, ...]
    error: str | None = None

    def __bool__(self) -> bool:
        return self.ok

    @property
    def summary(self) -> str:
        if self.ok:
            return f"runtime schema v{LATEST_RUNTIME_SCHEMA_VERSION} healthy"
        parts: list[str] = []
        if not self.version_matches:
            parts.append(
                f"version expected {LATEST_RUNTIME_SCHEMA_VERSION}, actual {self.actual_version or 0}"
            )
        if self.missing_tables:
            parts.append("missing tables: " + ", ".join(self.missing_tables))
        if self.missing_columns:
            parts.append(
                "missing columns: "
                + "; ".join(f"{table}({', '.join(columns)})" for table, columns in self.missing_columns.items())
            )
        if self.missing_indexes:
            parts.append("missing indexes: " + ", ".join(self.missing_indexes))
        if self.constraint_errors:
            parts.append("constraints: " + ", ".join(self.constraint_errors))
        if self.error:
            parts.append(self.error)
        return "; ".join(parts) or "runtime schema unhealthy"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "actual_version": self.actual_version,
            "applied_versions": list(self.applied_versions),
            "version_matches": self.version_matches,
            "missing_tables": list(self.missing_tables),
            "missing_columns": {table: list(columns) for table, columns in self.missing_columns.items()},
            "missing_indexes": list(self.missing_indexes),
            "constraint_errors": list(self.constraint_errors),
            "error": self.error,
            "summary": self.summary,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.as_dict()


def inspect_runtime_schema(connection: sqlite3.Connection) -> RuntimeSchemaHealth:
    """Inspect a SQLite connection without mutating it.

    The check is deliberately exact: a database with a migration row for v6
    but a missing table, column, index, or constraint is unhealthy.
    """

    try:
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        tables = {str(row[0]) for row in table_rows}
        missing_tables = tuple(sorted(REQUIRED_RUNTIME_TABLES - tables))

        applied_versions: tuple[int, ...] = ()
        if "runtime_schema_migrations" in tables:
            rows = connection.execute(
                "SELECT schema_version FROM runtime_schema_migrations ORDER BY schema_version"
            ).fetchall()
            applied_versions = tuple(int(row[0]) for row in rows)
        actual_version = max(applied_versions) if applied_versions else None
        version_matches = applied_versions == RUNTIME_SCHEMA_VERSIONS and actual_version == LATEST_RUNTIME_SCHEMA_VERSION

        missing_columns: dict[str, tuple[str, ...]] = {}
        for table, required in {
            **V5_REQUIRED_COLUMNS,
            **V6_REQUIRED_COLUMNS,
            **V7_REQUIRED_COLUMNS,
            **V8_REQUIRED_COLUMNS,
            **V9_REQUIRED_COLUMNS,
            **V10_REQUIRED_COLUMNS,
            **V11_REQUIRED_COLUMNS,
            **V12_REQUIRED_COLUMNS,
            **V13_REQUIRED_COLUMNS,
            **V14_REQUIRED_COLUMNS,
            **V15_REQUIRED_COLUMNS,
            **V16_REQUIRED_COLUMNS,
            **V17_REQUIRED_COLUMNS,
        }.items():
            if table not in tables:
                continue
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            absent = tuple(column for column in required if column not in columns)
            if absent:
                missing_columns[table] = absent

        constraint_errors: list[str] = []
        if "listing_status" in tables:
            listing_sql_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'listing_status'"
            ).fetchone()
            listing_sql = str(listing_sql_row[0] or "") if listing_sql_row else ""
            if not re.search(
                r"CHECK\s*\(\s*platform_stock_qty\s*>=\s*0\s*\)",
                listing_sql,
                re.IGNORECASE,
            ):
                constraint_errors.append("listing_status.platform_stock_qty lacks CHECK >= 0")
        missing_indexes: tuple[str, ...]
        missing_index_names: set[str] = set()
        if "retry_authorizations" in tables and "retry_authorizations" not in missing_tables:
            missing_index_names.update(_check_retry_authorization_constraints(connection, constraint_errors))
        else:
            missing_index_names.update(RETRY_AUTHORIZATION_INDEXES)
        if (
            "notification_outbox" in tables
            and "notification_delivery_attempts" in tables
            and "notification_outbox" not in missing_tables
            and "notification_delivery_attempts" not in missing_tables
        ):
            missing_index_names.update(_check_notification_outbox_constraints(connection, constraint_errors))
        else:
            missing_index_names.update(NOTIFICATION_OUTBOX_INDEXES)
        for index_name, expected_columns in V12_INDEX_SPECS.items():
            index_row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()
            if index_row is None:
                missing_index_names.add(index_name)
                continue
            actual_columns = tuple(
                str(row[2])
                for row in connection.execute(f"PRAGMA index_info({index_name})").fetchall()
            )
            if actual_columns != expected_columns:
                constraint_errors.append(
                    f"{index_name} columns expected {expected_columns}, actual {actual_columns}"
                )
        if not missing_tables:
            missing_index_names.update(
                _check_v13_constraints(connection, constraint_errors)
            )
            missing_index_names.update(
                _check_v14_constraints(connection, constraint_errors)
            )
            missing_index_names.update(
                _check_v15_constraints(connection, constraint_errors)
            )
            missing_index_names.update(
                _check_v16_constraints(connection, constraint_errors)
            )
            missing_index_names.update(
                _check_v17_constraints(connection, constraint_errors)
            )
        missing_indexes = tuple(sorted(missing_index_names))

        ok = not (
            missing_tables
            or missing_columns
            or missing_indexes
            or constraint_errors
            or not version_matches
        )
        return RuntimeSchemaHealth(
            ok=ok,
            actual_version=actual_version,
            applied_versions=applied_versions,
            version_matches=version_matches,
            missing_tables=missing_tables,
            missing_columns=missing_columns,
            missing_indexes=missing_indexes,
            constraint_errors=tuple(constraint_errors),
        )
    except sqlite3.Error as exc:
        return RuntimeSchemaHealth(
            ok=False,
            actual_version=None,
            applied_versions=(),
            version_matches=False,
            missing_tables=tuple(sorted(REQUIRED_RUNTIME_TABLES)),
            missing_columns={},
            missing_indexes=tuple(
                sorted(
                    RETRY_AUTHORIZATION_INDEXES
                    | NOTIFICATION_OUTBOX_INDEXES
                    | frozenset(V12_INDEX_SPECS)
                    | frozenset(V13_INDEX_SPECS)
                    | frozenset(V14_INDEX_SPECS)
                    | frozenset(V15_INDEX_SPECS)
                )
            ),
            constraint_errors=(),
            error=f"sqlite error: {type(exc).__name__}",
        )


def check_runtime_schema(connection: sqlite3.Connection) -> RuntimeSchemaHealth:
    """Compatibility alias for callers that name the operation a check."""

    return inspect_runtime_schema(connection)


def runtime_schema_health(connection: sqlite3.Connection) -> RuntimeSchemaHealth:
    """Alias for integrations that use health-check terminology."""

    return inspect_runtime_schema(connection)


def assert_runtime_schema(connection: sqlite3.Connection) -> RuntimeSchemaHealth:
    """Raise a diagnostic error unless the connection has the exact latest shape."""

    result = inspect_runtime_schema(connection)
    if not result.ok:
        raise RuntimeError(result.summary)
    return result


def _check_v13_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    missing_indexes: list[str] = []
    for index_name, expected_columns in V13_INDEX_SPECS.items():
        row = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(
                f"PRAGMA index_info('{index_name}')"
            ).fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(
                f"{index_name} columns expected {expected_columns}, actual {actual_columns}"
            )
    anomaly_indexes = {
        str(row[1]): row
        for row in connection.execute(
            "PRAGMA index_list('listing_anomaly_cases')"
        ).fetchall()
    }
    anomaly_dedupe = anomaly_indexes.get(
        "ux_listing_anomaly_cases_open_dedupe"
    )
    if anomaly_dedupe is not None:
        if int(anomaly_dedupe[2]) != 1 or int(anomaly_dedupe[4]) != 1:
            errors.append(
                "ux_listing_anomaly_cases_open_dedupe must be unique and partial"
            )
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = "
            "'ux_listing_anomaly_cases_open_dedupe'"
        ).fetchone()
        index_sql = str(sql_row[0] or "") if sql_row else ""
        if not re.search(
            r"WHERE\s+cleared_at\s+IS\s+NULL",
            index_sql,
            re.IGNORECASE,
        ):
            errors.append(
                "ux_listing_anomaly_cases_open_dedupe must cover open cases only"
            )

    operation_info = {
        str(row[1]): row
        for row in connection.execute(
            "PRAGMA table_info(shadowbot_operations)"
        ).fetchall()
    }
    for nullable_price in ("expected_old_price", "target_price"):
        row = operation_info.get(nullable_price)
        if row is None or int(row[3]) != 0:
            errors.append(
                f"shadowbot_operations.{nullable_price} must be nullable"
            )
    operation_sql = _table_sql(connection, "shadowbot_operations")
    _check_exact_status_values(
        operation_sql,
        column="action_type",
        expected=V13_OPERATION_ACTION_VALUES,
        label="shadowbot_operations.action_type",
        errors=errors,
    )
    write_lock_sql = _table_sql(connection, "shadowbot_write_locks")
    _check_exact_status_values(
        write_lock_sql,
        column="status",
        expected=V13_WRITE_LOCK_STATUS_VALUES,
        label="shadowbot_write_locks.status",
        errors=errors,
    )
    registry_sql = _table_sql(connection, "shadowbot_batch_registry")
    _check_exact_status_values(
        registry_sql,
        column="batch_type",
        expected=V13_BATCH_TYPE_VALUES,
        label="shadowbot_batch_registry.batch_type",
        errors=errors,
    )

    foreign_key_requirements = {
        "shadowbot_write_locks": {
            ("operation_id", "shadowbot_operations", "operation_id"),
            (
                "item_execution_attempt_id",
                "shadowbot_execution_attempts",
                "execution_attempt_id",
            ),
            ("batch_id", "shadowbot_batch_registry", "batch_id"),
        },
        "shadowbot_listing_action_batches": {
            ("batch_id", "shadowbot_batch_registry", "batch_id"),
        },
        "shadowbot_listing_action_batch_items": {
            ("batch_id", "shadowbot_listing_action_batches", "batch_id"),
            ("source_task_id", "tasks", "task_id"),
            ("operation_id", "shadowbot_operations", "operation_id"),
            (
                "item_execution_attempt_id",
                "shadowbot_execution_attempts",
                "execution_attempt_id",
            ),
        },
        "shadowbot_listing_result_receipts": {
            ("batch_id", "shadowbot_listing_action_batches", "batch_id"),
        },
        "listing_sync_snapshots": {
            ("batch_id", "shadowbot_listing_action_batches", "batch_id"),
            (
                "result_id",
                "shadowbot_listing_result_receipts",
                "result_id",
            ),
        },
        "listing_sync_snapshot_items": {
            ("snapshot_id", "listing_sync_snapshots", "snapshot_id"),
        },
        "listing_anomaly_cases": {
            ("snapshot_id", "listing_sync_snapshots", "snapshot_id"),
            (
                "snapshot_item_id",
                "listing_sync_snapshot_items",
                "snapshot_item_id",
            ),
            ("review_task_id", "review_tasks", "review_task_id"),
            (
                "cleared_by_snapshot_id",
                "listing_sync_snapshots",
                "snapshot_id",
            ),
        },
    }
    for table, required in foreign_key_requirements.items():
        actual = {
            (str(row[3]), str(row[2]), str(row[4]))
            for row in connection.execute(
                f"PRAGMA foreign_key_list('{table}')"
            ).fetchall()
        }
        for column, target_table, target_column in sorted(required - actual):
            errors.append(
                f"missing foreign key {table}.{column} -> "
                f"{target_table}({target_column})"
            )

    for source_table in (
        "shadowbot_commit_batches",
        "shadowbot_listing_action_batches",
    ):
        missing_registry = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM {source_table} AS source
                LEFT JOIN shadowbot_batch_registry AS registry
                  ON registry.batch_id = source.batch_id
                WHERE registry.batch_id IS NULL
                """
            ).fetchone()[0]
        )
        if missing_registry:
            errors.append(
                f"{source_table} has {missing_registry} batch rows without registry"
            )
    try:
        foreign_key_rows = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    except sqlite3.DatabaseError:
        errors.append("PRAGMA foreign_key_check could not validate malformed keys")
    else:
        if foreign_key_rows:
            errors.append(
                f"PRAGMA foreign_key_check returned {len(foreign_key_rows)} row(s)"
            )
    return tuple(sorted(missing_indexes))


def _check_v14_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    missing_indexes: list[str] = []
    for index_name, expected_columns in V14_INDEX_SPECS.items():
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(
                f"PRAGMA index_info('{index_name}')"
            ).fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(
                f"{index_name} columns expected {expected_columns}, "
                f"actual {actual_columns}"
            )

    for table, index_name, where_pattern in (
        (
            "operational_time_policies",
            "ux_operational_time_policies_current",
            r"WHERE\s+effective_to\s+IS\s+NULL",
        ),
        (
            "platform_trade_day_summaries",
            "ux_trade_day_summaries_current",
            r"WHERE\s+is_current\s*=\s*1",
        ),
        (
            "operational_incidents",
            "ux_operational_incidents_open_dedupe",
            r"WHERE\s+resolved_at\s+IS\s+NULL",
        ),
    ):
        index_rows = {
            str(row[1]): row
            for row in connection.execute(
                f"PRAGMA index_list('{table}')"
            ).fetchall()
        }
        index_row = index_rows.get(index_name)
        if index_row is None:
            continue
        if int(index_row[2]) != 1 or int(index_row[4]) != 1:
            errors.append(f"{index_name} must be unique and partial")
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        index_sql = str(sql_row[0] or "") if sql_row else ""
        if not re.search(where_pattern, index_sql, re.IGNORECASE):
            errors.append(f"{index_name} has the wrong partial predicate")

    task_sql = _table_sql(connection, "tasks")
    _check_exact_status_values(
        task_sql,
        column="origin_type",
        expected=V14_TASK_ORIGIN_VALUES,
        label="tasks.origin_type",
        errors=errors,
    )
    untraceable_task_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM tasks
            WHERE origin_type IN ('MANUAL', 'AUTOMATION')
              AND trim(COALESCE(origin_ref_id, '')) = ''
            """
        ).fetchone()[0]
    )
    if untraceable_task_count:
        errors.append(
            "MANUAL and AUTOMATION tasks must have a traceable origin_ref_id"
        )

    automation_run_sql = _table_sql(connection, "automation_runs")
    _check_exact_status_values(
        automation_run_sql,
        column="run_status",
        expected=V14_AUTOMATION_RUN_STATUS_VALUES,
        label="automation_runs.run_status",
        errors=errors,
    )

    order_batch_sql = _table_sql(
        connection,
        "order_observation_batches",
    )
    _check_exact_status_values(
        order_batch_sql,
        column="trade_day_status",
        expected=frozenset({"OPEN", "CLOSED"}),
        label="order_observation_batches.trade_day_status",
        errors=errors,
    )
    order_item_sql = _table_sql(
        connection,
        "order_observation_items",
    )
    _check_exact_status_values(
        order_item_sql,
        column="trade_day_status",
        expected=frozenset({"OPEN", "CLOSED"}),
        label="order_observation_items.trade_day_status",
        errors=errors,
    )
    order_item_info = {
        str(row[1]): row
        for row in connection.execute(
            "PRAGMA table_info(order_observation_items)"
        ).fetchall()
    }
    retired_order_columns = {
        "ordered_qty",
        "effective_qty",
        "cancelled_qty",
        "cancellation_derivation_method",
        "seller_received_amount",
        "purchase_sequence",
        "source_row_fingerprint",
    }
    retained_retired_columns = retired_order_columns.intersection(
        order_item_info
    )
    if retained_retired_columns:
        errors.append(
            "order_observation_items retains retired provisional columns: "
            + ", ".join(sorted(retained_retired_columns))
        )
    for required_not_null in (
        "platform_name",
        "trade_day_status",
        "order_identity_fingerprint",
        "order_qty",
        "order_transaction_amount",
    ):
        row = order_item_info.get(required_not_null)
        if row is None or int(row[3]) != 1:
            errors.append(
                "order_observation_items."
                f"{required_not_null} must be NOT NULL"
            )
    order_unique_identities = {
        tuple(
            str(index_row[2])
            for index_row in connection.execute(
                f"PRAGMA index_info('{str(index[1])}')"
            ).fetchall()
        )
        for index in connection.execute(
            "PRAGMA index_list('order_observation_items')"
        ).fetchall()
        if int(index[2]) == 1
    }
    required_order_identity = (
        "observation_batch_id",
        "order_identity_fingerprint",
        "occurrence_no",
    )
    if required_order_identity not in order_unique_identities:
        errors.append(
            "order_observation_items must uniquely preserve "
            "batch, identity fingerprint and occurrence_no"
        )

    summary_sql = _table_sql(connection, "platform_trade_day_summaries")
    summary_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(platform_trade_day_summaries)"
        ).fetchall()
    }
    if "seller_received_amount" in summary_columns:
        errors.append(
            "platform_trade_day_summaries retains retired "
            "seller_received_amount"
        )
    _check_exact_status_values(
        summary_sql,
        column="quality_level",
        expected=V14_QUALITY_VALUES,
        label="platform_trade_day_summaries.quality_level",
        errors=errors,
    )
    _check_exact_status_values(
        summary_sql,
        column="summary_status",
        expected=V14_SUMMARY_STATUS_VALUES,
        label="platform_trade_day_summaries.summary_status",
        errors=errors,
    )
    _check_exact_status_values(
        summary_sql,
        column="seller_phase",
        expected=V14_SELLER_PHASE_VALUES,
        label="platform_trade_day_summaries.seller_phase",
        errors=errors,
    )
    fact_source_match = re.search(
        r"\bfact_source\b\s+[^,]*?\bCHECK\s*\(\s*"
        r"fact_source\s+IS\s+NULL\s+OR\s+fact_source\s+IN\s*"
        r"\((?P<values>[^)]*)\)\s*\)",
        summary_sql,
        re.IGNORECASE | re.DOTALL,
    )
    fact_source_values = {
        value.replace("''", "'")
        for value in re.findall(
            r"'((?:''|[^'])*)'",
            fact_source_match.group("values")
            if fact_source_match
            else "",
        )
    }
    if fact_source_values != V14_FACT_SOURCE_VALUES:
        errors.append(
            "platform_trade_day_summaries.fact_source CHECK must allow "
            "NULL or exactly " + ", ".join(sorted(V14_FACT_SOURCE_VALUES))
        )
    if not re.search(
        r"fact_source\s+IS\s+NULL.*?"
        r"quality_level\s*=\s*'UNAVAILABLE'.*?"
        r"sold_qty\s+IS\s+NULL.*?"
        r"order_count\s+IS\s+NULL.*?"
        r"transaction_amount_total\s+IS\s+NULL",
        summary_sql,
        re.IGNORECASE | re.DOTALL,
    ):
        errors.append(
            "platform_trade_day_summaries lacks the UNAVAILABLE NULL-fact "
            "constraint"
        )
    if not re.search(
        r"summary_status\s*<>\s*'FINAL'\s+OR\s+"
        r"quality_level\s*=\s*'ORDER_COMPLETE'",
        summary_sql,
        re.IGNORECASE | re.DOTALL,
    ):
        errors.append(
            "platform_trade_day_summaries FINAL must require ORDER_COMPLETE"
        )

    required_triggers = {
        "trg_trade_day_summary_initial_status": (
            "PROVISIONAL",
            "OBSERVED",
        ),
        "trg_trade_day_summary_status_transition": (
            "PROVISIONAL",
            "OBSERVED",
            "RECONCILED",
            "FINAL",
        ),
        "trg_trade_day_summary_final_immutable": (
            "FINAL",
            "immutable",
            "is_current",
            "summary_series_id",
            "platform_trade_date",
            "scope_key",
            "created_at",
        ),
        "trg_operational_time_policy_no_overlap_insert": (
            "effective_from",
            "effective_to",
            "overlap",
        ),
        "trg_operational_time_policy_no_overlap_update": (
            "effective_from",
            "effective_to",
            "overlap",
        ),
        "trg_operational_time_policy_immutable_update": (
            "effective_to",
            "immutable",
            "supersedes_policy_version",
        ),
        "trg_operational_time_policy_no_delete": (
            "cannot be deleted",
        ),
        "trg_operational_time_policy_successor_adjacent": (
            "supersedes_policy_version",
            "adjacent",
        ),
        "trg_tasks_traceable_origin_insert": (
            "MANUAL",
            "AUTOMATION",
            "origin_ref_id",
        ),
        "trg_tasks_traceable_origin_update": (
            "MANUAL",
            "AUTOMATION",
            "origin_ref_id",
        ),
        "trg_tasks_origin_immutable": (
            "origin_type",
            "origin_ref_id",
            "immutable",
        ),
    }
    for table_name in V14_APPEND_ONLY_TABLES:
        required_triggers[
            f"trg_{table_name}_append_only_update"
        ] = ("append-only",)
        required_triggers[
            f"trg_{table_name}_append_only_delete"
        ] = ("append-only",)
    for trigger_name, required_terms in required_triggers.items():
        trigger_row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'trigger' AND name = ?
            """,
            (trigger_name,),
        ).fetchone()
        trigger_sql = str(trigger_row[0] or "") if trigger_row else ""
        if not trigger_sql:
            errors.append(f"missing trigger {trigger_name}")
            continue
        for term in required_terms:
            if term.lower() not in trigger_sql.lower():
                errors.append(
                    f"{trigger_name} lacks required term {term}"
                )

    policy_sql = _table_sql(connection, "operational_time_policies")
    if not re.search(
        r"timezone_name\s+[^,]*CHECK\s*\(\s*"
        r"timezone_name\s*=\s*'Asia/Shanghai'\s*\)",
        policy_sql,
        re.IGNORECASE | re.DOTALL,
    ):
        errors.append(
            "operational_time_policies.timezone_name must be Asia/Shanghai"
        )
    for column in ("effective_from", "effective_to"):
        if f"substr({column}, -6) = '+00:00'" not in policy_sql:
            errors.append(
                f"operational_time_policies.{column} must require UTC storage"
            )
    current_policy_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM operational_time_policies
            WHERE effective_to IS NULL
            """
        ).fetchone()[0]
    )
    if current_policy_count != 1:
        errors.append(
            "operational_time_policies must have exactly one current policy"
        )

    incident_sql = _table_sql(connection, "operational_incidents")
    _check_exact_status_values(
        incident_sql,
        column="category",
        expected=V15_INCIDENT_CATEGORY_VALUES,
        label="operational_incidents.category",
        errors=errors,
    )
    _check_exact_status_values(
        incident_sql,
        column="incident_status",
        expected=V14_INCIDENT_STATUS_VALUES,
        label="operational_incidents.incident_status",
        errors=errors,
    )
    if not re.search(
        r"CHECK\s*\(\s*blocks_finalization\s+IN\s*\(\s*0\s*,\s*1\s*\)"
        r"\s*\)",
        incident_sql,
        re.IGNORECASE,
    ):
        errors.append(
            "operational_incidents.blocks_finalization must be boolean"
        )
    if not re.search(
        r"incident_status\s+IN\s*\(\s*'RESOLVED'\s*,\s*'CLOSED'\s*\)"
        r".*?resolved_at\s+IS\s+NOT\s+NULL"
        r".*?incident_status\s+NOT\s+IN\s*\(\s*'RESOLVED'\s*,\s*'CLOSED'\s*\)"
        r".*?resolved_at\s+IS\s+NULL",
        incident_sql,
        re.IGNORECASE | re.DOTALL,
    ):
        errors.append(
            "operational_incidents status and resolved_at must be consistent"
        )

    foreign_key_requirements = {
        "operational_time_policies": {
            (
                "supersedes_policy_version",
                "operational_time_policies",
                "policy_version",
            ),
        },
        "automation_runs": {
            ("job_id", "automation_jobs", "job_id"),
            (
                "time_policy_version",
                "operational_time_policies",
                "policy_version",
            ),
        },
        "automation_run_events": {
            ("run_id", "automation_runs", "run_id"),
        },
        "automation_run_links": {
            ("parent_run_id", "automation_runs", "run_id"),
            ("child_run_id", "automation_runs", "run_id"),
        },
        "product_observation_batches": {
            ("automation_run_id", "automation_runs", "run_id"),
            (
                "time_policy_version",
                "operational_time_policies",
                "policy_version",
            ),
        },
        "product_observation_items": {
            (
                "observation_batch_id",
                "product_observation_batches",
                "observation_batch_id",
            ),
        },
        "order_observation_batches": {
            ("automation_run_id", "automation_runs", "run_id"),
            (
                "time_policy_version",
                "operational_time_policies",
                "policy_version",
            ),
        },
        "order_observation_items": {
            (
                "observation_batch_id",
                "order_observation_batches",
                "observation_batch_id",
            ),
        },
        "platform_trade_day_summaries": {
            (
                "supersedes_summary_id",
                "platform_trade_day_summaries",
                "summary_id",
            ),
            (
                "time_policy_version",
                "operational_time_policies",
                "policy_version",
            ),
        },
        "platform_trade_day_summary_events": {
            (
                "summary_id",
                "platform_trade_day_summaries",
                "summary_id",
            ),
        },
        "platform_trade_day_summary_inputs": {
            (
                "summary_id",
                "platform_trade_day_summaries",
                "summary_id",
            ),
        },
        "incident_notification_state": {
            (
                "incident_id",
                "operational_incidents",
                "incident_id",
            ),
        },
    }
    for table, required in foreign_key_requirements.items():
        actual = {
            (str(row[3]), str(row[2]), str(row[4]))
            for row in connection.execute(
                f"PRAGMA foreign_key_list('{table}')"
            ).fetchall()
        }
        for column, target_table, target_column in sorted(required - actual):
            errors.append(
                f"missing foreign key {table}.{column} -> "
                f"{target_table}({target_column})"
            )

    policy_row = connection.execute(
        """
        SELECT timezone_name, platform_cutoff_local_time,
               seller_cutoff_local_time, peak_start_local_time,
               effective_from, supersedes_policy_version
        FROM operational_time_policies
        WHERE policy_version = 'CN_SINGLE_PLATFORM_2026_V1'
        """
    ).fetchone()
    expected_policy = (
        "Asia/Shanghai",
        "18:00:00",
        "20:00:00",
        "16:00:00",
        "2025-12-31T16:00:00+00:00",
        None,
    )
    if policy_row is None or tuple(policy_row) != expected_policy:
        errors.append(
            "CN_SINGLE_PLATFORM_2026_V1 operational time policy is missing "
            "or has incorrect frozen semantics"
        )

    return tuple(sorted(missing_indexes))


def _check_v15_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    missing_indexes: list[str] = []
    for index_name, expected_columns in V15_INDEX_SPECS.items():
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(
                f"PRAGMA index_info('{index_name}')"
            ).fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(
                f"{index_name} columns expected {expected_columns}, "
                f"actual {actual_columns}"
            )

    index_rows = {
        str(row[1]): row
        for row in connection.execute(
            "PRAGMA index_list('operational_incident_events')"
        ).fetchall()
    }
    event_key_index = index_rows.get("ux_operational_incident_events_key")
    if event_key_index is not None and (
        int(event_key_index[2]) != 1 or int(event_key_index[4]) != 0
    ):
        errors.append(
            "ux_operational_incident_events_key must be unique and non-partial"
        )

    incident_sql = _table_sql(connection, "operational_incidents")
    _check_exact_status_values(
        incident_sql,
        column="category",
        expected=V15_INCIDENT_CATEGORY_VALUES,
        label="operational_incidents.category",
        errors=errors,
    )
    if not re.search(
        r"occurrence_count\s+[^,]*CHECK\s*\(\s*occurrence_count\s*>=\s*1\s*\)",
        incident_sql,
        re.IGNORECASE | re.DOTALL,
    ):
        errors.append("operational_incidents.occurrence_count must be >= 1")

    event_sql = _table_sql(connection, "operational_incident_events")
    _check_exact_status_values(
        event_sql,
        column="event_type",
        expected=V15_INCIDENT_EVENT_TYPE_VALUES,
        label="operational_incident_events.event_type",
        errors=errors,
    )
    if "json_valid(event_payload_json)" not in event_sql.replace(" ", ""):
        compact_event_sql = re.sub(r"\s+", "", event_sql).lower()
        if "json_valid(event_payload_json)" not in compact_event_sql:
            errors.append(
                "operational_incident_events.event_payload_json must be valid JSON"
            )

    actual_foreign_keys = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute(
            "PRAGMA foreign_key_list('operational_incident_events')"
        ).fetchall()
    }
    required_foreign_key = (
        "incident_id",
        "operational_incidents",
        "incident_id",
    )
    if required_foreign_key not in actual_foreign_keys:
        errors.append(
            "missing foreign key operational_incident_events.incident_id -> "
            "operational_incidents(incident_id)"
        )

    for table_name in V15_APPEND_ONLY_TABLES:
        for suffix in ("update", "delete"):
            trigger_name = f"trg_{table_name}_append_only_{suffix}"
            trigger_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                (trigger_name,),
            ).fetchone()
            trigger_sql = str(trigger_row[0] or "") if trigger_row else ""
            if "append-only" not in trigger_sql.lower():
                errors.append(f"missing trigger {trigger_name}")

    return tuple(sorted(missing_indexes))


def _check_v16_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    missing_indexes: list[str] = []
    for index_name, expected_columns in V16_INDEX_SPECS.items():
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(
                f"PRAGMA index_info('{index_name}')"
            ).fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(
                f"{index_name} columns expected {expected_columns}, "
                f"actual {actual_columns}"
            )
        index_sql = re.sub(r"\s+", " ", str(row[0] or "")).upper()
        if (
            "UNIQUE INDEX" not in index_sql
            or "APPROVED_AT IS NOT NULL" not in index_sql
            or "RETIRED_AT IS NULL" not in index_sql
        ):
            errors.append(
                "ux_emergency_offline_policies_active must be a partial unique "
                "index for approved, non-retired policies"
            )

    table_sql = re.sub(
        r"\s+",
        " ",
        _table_sql(connection, "emergency_offline_policies"),
    ).upper()
    if not re.search(
        r"CHECK\s*\(\s*EMERGENCY_RATIO\s*=\s*'0\.80'\s*\)",
        table_sql,
    ):
        errors.append(
            "emergency_offline_policies.emergency_ratio must be fixed at 0.80"
        )
    if "APPROVED_BY IS NULL AND APPROVED_AT IS NULL" not in table_sql:
        errors.append(
            "emergency_offline_policies approval fields must be coherent"
        )
    if "RETIRED_AT IS NULL OR APPROVED_AT IS NOT NULL" not in table_sql:
        errors.append(
            "emergency_offline_policies cannot retire an unapproved policy"
        )

    for trigger_name in (
        "trg_emergency_offline_policies_lifecycle_update",
        "trg_emergency_offline_policies_no_delete",
    ):
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
            (trigger_name,),
        ).fetchone()
        if row is None or "emergency policy" not in str(row[0] or "").lower():
            errors.append(f"missing trigger {trigger_name}")

    return tuple(sorted(missing_indexes))


def _check_v17_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    missing_indexes: list[str] = []
    for index_name, expected_columns in V17_INDEX_SPECS.items():
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(
                f"PRAGMA index_info('{index_name}')"
            ).fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(
                f"{index_name} columns expected {expected_columns}, "
                f"actual {actual_columns}"
            )

    authority = connection.execute(
        "SELECT authority_mode, version FROM inventory_authority_state "
        "WHERE authority_key = 'REAL_INVENTORY'"
    ).fetchone()
    authority_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM inventory_authority_state"
        ).fetchone()[0]
    )
    if authority is None or authority_count != 1:
        errors.append(
            "inventory_authority_state must contain exactly REAL_INVENTORY"
        )
    elif str(authority[0]) not in {"PRE_CUTOVER", "DB_AUTHORITY"}:
        errors.append("inventory_authority_state has invalid authority_mode")

    transaction_sql = re.sub(
        r"\s+",
        " ",
        _table_sql(connection, "inventory_transactions"),
    ).lower()
    if "inventory_after = inventory_before + inventory_delta" not in transaction_sql:
        errors.append("inventory transaction arithmetic constraint is missing")
    if "inventory_after >= 0" not in transaction_sql:
        errors.append("inventory transaction non-negative constraint is missing")
    for table_name in V17_APPEND_ONLY_TABLES:
        for suffix in ("update", "delete"):
            trigger_name = f"trg_{table_name}_append_only_{suffix}"
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                (trigger_name,),
            ).fetchone()
            if row is None or "append-only" not in str(row[0] or "").lower():
                errors.append(f"missing trigger {trigger_name}")

    actual_foreign_keys = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute(
            "PRAGMA foreign_key_list('inventory_sales_baselines')"
        ).fetchall()
    }
    required_foreign_key = (
        "inventory_transaction_id",
        "inventory_transactions",
        "transaction_id",
    )
    if required_foreign_key not in actual_foreign_keys:
        errors.append(
            "missing foreign key inventory_sales_baselines."
            "inventory_transaction_id -> inventory_transactions(transaction_id)"
        )
    default_policy_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM inventory_alert_policies "
            "WHERE scope_type = 'DEFAULT' AND scope_key = '*'"
        ).fetchone()[0]
    )
    if default_policy_count != 1:
        errors.append(
            "inventory_alert_policies must contain exactly one default policy"
        )
    return tuple(sorted(missing_indexes))


def _table_sql(connection: sqlite3.Connection, table: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return str(row[0] or "") if row else ""


def _check_exact_status_values(
    table_sql: str,
    *,
    column: str,
    expected: frozenset[str],
    label: str,
    errors: list[str],
) -> None:
    match = re.search(
        rf"\b{column}\b\s+[^,]*?\bCHECK\s*\(\s*{column}\s+IN\s*"
        r"\((?P<values>[^)]*)\)\s*\)",
        table_sql,
        re.IGNORECASE | re.DOTALL,
    )
    actual = {
        value.replace("''", "'")
        for value in re.findall(
            r"'((?:''|[^'])*)'",
            match.group("values") if match else "",
        )
    }
    if actual != expected:
        errors.append(
            f"{label} CHECK must allow exactly " + ", ".join(sorted(expected))
        )


def _check_retry_authorization_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    table_info = connection.execute("PRAGMA table_info(retry_authorizations)").fetchall()
    by_name = {str(row[1]): row for row in table_info}
    primary_key = by_name.get("retry_authorization_id")
    if primary_key is None or int(primary_key[5]) != 1:
        errors.append("retry_authorizations.retry_authorization_id is not the primary key")

    foreign_keys = connection.execute("PRAGMA foreign_key_list(retry_authorizations)").fetchall()
    # SQLite PRAGMA foreign_key_list columns are (id, seq, table, from, to,
    # on_update, on_delete, match).  The referenced column is part of the
    # schema contract: accepting only the target table would let a malformed
    # v5 database point at an unrelated column and still report healthy.
    foreign_key_specs = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in foreign_keys
    }
    for column, target_table, target_column in (
        ("operation_id", "shadowbot_operations", "operation_id"),
        (
            "source_execution_attempt_id",
            "shadowbot_execution_attempts",
            "execution_attempt_id",
        ),
    ):
        if (column, target_table, target_column) not in foreign_key_specs:
            errors.append(
                f"missing foreign key {column} -> {target_table}({target_column})"
            )

    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'retry_authorizations'"
    ).fetchone()
    table_sql = str(sql_row[0] or "") if sql_row else ""
    if not re.search(r"\bCHECK\s*\(\s*max_uses\s*=\s*1\s*\)", table_sql, re.IGNORECASE):
        errors.append("retry_authorizations.max_uses lacks CHECK (max_uses = 1)")

    status_match = re.search(
        r"\bstatus\b\s+[^,]*?\bCHECK\s*\(\s*status\s+IN\s*\((?P<values>[^)]*)\)\)",
        table_sql,
        re.IGNORECASE | re.DOTALL,
    )
    status_values = tuple(
        value.replace("''", "'").upper()
        for value in re.findall(r"'((?:''|[^'])*)'", status_match.group("values"))
    ) if status_match else ()
    expected_status_values = tuple(sorted(RETRY_AUTHORIZATION_STATUS_VALUES))
    if (
        not status_match
        or len(status_values) != len(expected_status_values)
        or set(status_values) != RETRY_AUTHORIZATION_STATUS_VALUES
    ):
        errors.append(
            "retry_authorizations.status CHECK must allow exactly "
            + ", ".join(expected_status_values)
        )

    index_rows = {
        str(row[1]): row
        for row in connection.execute("PRAGMA index_list('retry_authorizations')").fetchall()
    }
    missing_indexes: list[str] = []
    for index_name, (expected_columns, expected_unique) in RETRY_AUTHORIZATION_INDEX_SPECS.items():
        row = index_rows.get(index_name)
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_unique = int(row[2]) == 1
        if actual_unique != expected_unique:
            errors.append(
                f"{index_name} unique={actual_unique}, expected {expected_unique}"
            )
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(
                f"{index_name} columns={actual_columns}, expected {expected_columns}"
            )
    return tuple(sorted(missing_indexes))


def _check_notification_outbox_constraints(
    connection: sqlite3.Connection,
    errors: list[str],
) -> tuple[str, ...]:
    """Validate required keys, foreign keys, status/numeric checks, and indexes."""

    outbox_info = connection.execute("PRAGMA table_info(notification_outbox)").fetchall()
    outbox_columns = {str(row[1]): row for row in outbox_info}
    if not outbox_columns.get("notification_id") or int(outbox_columns["notification_id"][5]) != 1:
        errors.append("notification_outbox.notification_id is not the primary key")

    attempt_info = connection.execute("PRAGMA table_info(notification_delivery_attempts)").fetchall()
    attempt_columns = {str(row[1]): row for row in attempt_info}
    if not attempt_columns.get("delivery_attempt_id") or int(attempt_columns["delivery_attempt_id"][5]) != 1:
        errors.append("notification_delivery_attempts.delivery_attempt_id is not the primary key")

    foreign_key_specs = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute("PRAGMA foreign_key_list(notification_outbox)").fetchall()
    }
    for column, target in (
        ("related_task_id", ("tasks", "task_id")),
        ("related_review_task_id", ("review_tasks", "review_task_id")),
    ):
        if (column, *target) not in foreign_key_specs:
            errors.append(f"missing foreign key {column} -> {target[0]}({target[1]})")
    attempt_foreign_keys = {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in connection.execute("PRAGMA foreign_key_list(notification_delivery_attempts)").fetchall()
    }
    if ("notification_id", "notification_outbox", "notification_id") not in attempt_foreign_keys:
        errors.append(
            "missing foreign key notification_id -> notification_outbox(notification_id)"
        )

    table_sql_rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
        "AND name IN ('notification_outbox', 'notification_delivery_attempts')"
    ).fetchall()
    table_sql = {str(row[0]): str(row[1] or "") for row in table_sql_rows}
    for table, column, expected in (
        (
            "notification_outbox",
            "status",
            NOTIFICATION_OUTBOX_STATUS_VALUES,
        ),
        (
            "notification_delivery_attempts",
            "status",
            DELIVERY_ATTEMPT_STATUS_VALUES,
        ),
    ):
        match = re.search(
            rf"\b{column}\b\s+[^,]*?\bCHECK\s*\(\s*{column}\s+IN\s*\((?P<values>[^)]*)\)\)",
            table_sql.get(table, ""),
            re.IGNORECASE | re.DOTALL,
        )
        actual = {
            value.replace("''", "'").upper()
            for value in re.findall(r"'((?:''|[^'])*)'", match.group("values"))
        } if match else set()
        if actual != expected:
            errors.append(
                f"{table}.status CHECK must allow exactly "
                + ", ".join(sorted(expected))
            )

    numeric_checks = (
        ("notification_outbox", "attempt_count", r"attempt_count\s*>=\s*0"),
        ("notification_outbox", "max_attempts", r"max_attempts\s*>\s*0"),
        ("notification_outbox", "lease_version", r"lease_version\s*>=\s*0"),
        ("notification_delivery_attempts", "attempt_no", r"attempt_no\s*>\s*0"),
        ("notification_delivery_attempts", "lease_version", r"lease_version\s*>=\s*0"),
    )
    for table, column, pattern in numeric_checks:
        if not re.search(rf"\bCHECK\s*\(\s*{pattern}\s*\)", table_sql.get(table, ""), re.IGNORECASE):
            errors.append(f"{table}.{column} lacks required numeric CHECK")

    index_rows = {
        str(row[1]): row
        for table in ("notification_outbox", "notification_delivery_attempts")
        for row in connection.execute(f"PRAGMA index_list('{table}')").fetchall()
    }
    missing_indexes: list[str] = []
    for index_name, (expected_columns, expected_unique) in NOTIFICATION_OUTBOX_INDEX_SPECS.items():
        row = index_rows.get(index_name)
        if row is None:
            missing_indexes.append(index_name)
            continue
        actual_unique = int(row[2]) == 1
        if actual_unique != expected_unique:
            errors.append(f"{index_name} unique={actual_unique}, expected {expected_unique}")
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        )
        if actual_columns != expected_columns:
            errors.append(f"{index_name} columns={actual_columns}, expected {expected_columns}")

    unique_attempt_key = False
    for row in connection.execute("PRAGMA index_list('notification_delivery_attempts')").fetchall():
        if int(row[2]) != 1:
            continue
        index_name = str(row[1])
        actual_columns = tuple(
            str(index_row[2])
            for index_row in connection.execute(f"PRAGMA index_info('{index_name}')").fetchall()
        )
        if actual_columns == ("notification_id", "attempt_no"):
            unique_attempt_key = True
            break
    if not unique_attempt_key:
        errors.append(
            "notification_delivery_attempts lacks UNIQUE(notification_id, attempt_no)"
        )
    return tuple(sorted(missing_indexes))
