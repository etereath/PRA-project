"""Deterministic identities shared by Automation Job version changes."""

from __future__ import annotations

import hashlib
import json


def automation_configuration_version(
    *,
    job_type: str,
    schedule_kind: str,
    schedule_expression: str,
    config: dict[str, object],
) -> str:
    normalized = {
        "job_type": job_type,
        "platform_name": str(config.get("platform_name") or ""),
        "schedule_kind": schedule_kind,
        "schedule_expression": schedule_expression,
        "interval_offset_minutes": config.get("interval_offset_minutes"),
        "settlement_offset_minutes": config.get(
            "settlement_offset_minutes"
        ),
        "plan_input_offset_minutes": config.get(
            "plan_input_offset_minutes"
        ),
        "sales_plan_input_offset_minutes": config.get(
            "sales_plan_input_offset_minutes"
        ),
        "source_allowlist": config.get("source_allowlist"),
        "time_policy_version": config.get("time_policy_version"),
        "upstream_configuration_version": config.get(
            "upstream_configuration_version"
        ),
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def versioned_automation_job_id(job_type: str, version: str) -> str:
    return (
        "AUTOMATION-"
        + job_type.replace("_", "-")
        + "-V-"
        + version.removeprefix("sha256:")[:12].upper()
    )
