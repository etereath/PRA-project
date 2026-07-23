from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.shadowbot_commit_batch import (
    validate_manifest,
    validate_request as validate_v4_request,
)
from app.services.shadowbot_executor import compute_instruction_hash as compute_legacy_hash
from app.shadowbot_contract_primitives import derive_v4_batch_semantics, v4_result_counts


DEFAULT_ROOT = Path("docs/evidence/task12")
REQUIRED_V4_BINDINGS = (
    "item_id",
    "source_task_id",
    "operation_id",
    "item_execution_attempt_id",
    "write_identity_key",
    "page_identity_key",
    "internal_sku",
    "expected_product_name",
    "expected_grade",
    "expected_old_price",
    "target_price",
    "item_payload_sha256",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_bundle(bundle_dir: Path) -> dict[str, Any]:
    required = {
        "request": bundle_dir / "request.sanitized.json",
        "result": bundle_dir / "result.sanitized.json",
        "phase": bundle_dir / "phase.sanitized.json",
        "manifest": bundle_dir / "manifest.json",
    }
    for path in required.values():
        _check(path.is_file(), f"missing evidence file: {path}")
    request = _read_json(required["request"])
    result = _read_json(required["result"])
    phase = _read_json(required["phase"])
    manifest = _read_json(required["manifest"])

    validate_manifest(manifest)
    validate_v4_request(request, check_expiry=False)
    request_sha = _sha256(required["request"])
    for document, label in ((result, "result"), (phase, "phase")):
        _check(
            document.get("request_file_sha256") == request_sha,
            f"{label} request_file_sha256 does not bind the sanitized request",
        )
        for field in (
            "execution_attempt_id",
            "instruction_hash",
            "batch_id",
            "manifest_sha256",
        ):
            _check(
                str(document.get(field) or "") == str(request.get(field) or ""),
                f"{label} {field} does not bind the request",
            )
    _check(
        request.get("manifest_sha256") == manifest.get("manifest_sha256"),
        "request does not bind manifest",
    )

    request_items = {
        str(item["source_task_id"]): item for item in request.get("items") or []
    }
    result_items = {
        str(item["source_task_id"]): item for item in result.get("items") or []
    }
    _check(request_items.keys() == result_items.keys(), "result item set differs from request")
    for task_id, expected in request_items.items():
        supplied = result_items[task_id]
        for field in REQUIRED_V4_BINDINGS:
            _check(
                str(supplied.get(field) or "") == str(expected.get(field) or ""),
                f"result item binding mismatch: {task_id}.{field}",
            )
    counts = v4_result_counts(list(result_items.values()))
    _check(result.get("counts") == counts, "result count identity is invalid")
    semantics = derive_v4_batch_semantics(counts)
    for field, expected in semantics.items():
        _check(result.get(field) == expected, f"result top-level semantic mismatch: {field}")

    ordinals = [
        int(item["execution_ordinal"])
        for item in result_items.values()
        if item.get("execution_ordinal") is not None
    ]
    _check(
        sorted(ordinals) == list(range(1, len(ordinals) + 1)),
        "result execution ordinals are not unique and contiguous",
    )
    report: dict[str, Any] = {
        "bundle_id": bundle_dir.name,
        "batch_id": request["batch_id"],
        "execution_attempt_id": request["execution_attempt_id"],
        "batch_status": result["batch_status"],
        "counts": counts,
        "execution_order": [
            {
                "execution_ordinal": item.get("execution_ordinal"),
                "source_task_id": item["source_task_id"],
                "internal_sku": item["internal_sku"],
                "status": item["status"],
            }
            for item in sorted(
                result_items.values(),
                key=lambda value: (
                    value.get("execution_ordinal") is None,
                    value.get("execution_ordinal") or 0,
                ),
            )
        ],
        "sanitized_sha256": {
            name: _sha256(path) for name, path in required.items()
        },
        "checks": {
            "manifest_valid": True,
            "request_contract_valid": True,
            "request_result_phase_binding_valid": True,
            "item_binding_valid": True,
            "count_identity_valid": True,
            "top_level_semantics_valid": True,
            "execution_order_valid": True,
        },
    }

    reconcile_request_path = bundle_dir / "reconcile.request.sanitized.json"
    reconcile_result_path = bundle_dir / "reconcile.result.sanitized.json"
    reconcile_phase_path = bundle_dir / "reconcile.phase.sanitized.json"
    reconcile_paths = (
        reconcile_request_path,
        reconcile_result_path,
        reconcile_phase_path,
    )
    if any(path.exists() for path in reconcile_paths):
        _check(all(path.is_file() for path in reconcile_paths), "reconcile evidence is incomplete")
        reconcile_request = _read_json(reconcile_request_path)
        reconcile_result = _read_json(reconcile_result_path)
        reconcile_phase = _read_json(reconcile_phase_path)
        _check(
            reconcile_request.get("instruction_hash")
            == compute_legacy_hash(reconcile_request),
            "reconcile request instruction hash is invalid",
        )
        reconcile_request_sha = _sha256(reconcile_request_path)
        for document, label in (
            (reconcile_result, "reconcile result"),
            (reconcile_phase, "reconcile phase"),
        ):
            _check(
                document.get("request_file_sha256") == reconcile_request_sha,
                f"{label} does not bind sanitized reconcile request",
            )
            for field in (
                "execution_attempt_id",
                "operation_id",
                "instruction_hash",
            ):
                _check(
                    str(document.get(field) or "")
                    == str(reconcile_request.get(field) or ""),
                    f"{label} {field} mismatch",
                )
        unknown_items = [
            item for item in result_items.values() if item.get("status") == "UNKNOWN"
        ]
        _check(len(unknown_items) == 1, "reconcile bundle must contain one UNKNOWN item")
        _check(
            reconcile_request.get("source_execution_attempt_id")
            == unknown_items[0].get("item_execution_attempt_id"),
            "reconcile source attempt does not bind UNKNOWN item",
        )
        _check(
            reconcile_result.get("status") in {"VERIFIED", "NOT_APPLIED"},
            "reconcile result did not resolve the side effect",
        )
        report["reconcile"] = {
            "execution_attempt_id": reconcile_request["execution_attempt_id"],
            "source_execution_attempt_id": reconcile_request[
                "source_execution_attempt_id"
            ],
            "status": reconcile_result["status"],
            "sanitized_sha256": {
                "request": reconcile_request_sha,
                "result": _sha256(reconcile_result_path),
                "phase": _sha256(reconcile_phase_path),
            },
        }
        report["checks"]["unknown_to_reconcile_binding_valid"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    bundle_dirs = sorted(
        path for path in args.root.iterdir() if path.is_dir() and path.name.startswith("ATTEMPT-")
    )
    _check(len(bundle_dirs) >= 2, "normal and UNKNOWN evidence bundles are required")
    reports = [validate_bundle(path) for path in bundle_dirs]
    _check(
        any(report["batch_status"] == "VERIFIED" for report in reports),
        "VERIFIED evidence bundle is missing",
    )
    _check(
        any(
            report["batch_status"] == "UNKNOWN" and "reconcile" in report
            for report in reports
        ),
        "UNKNOWN -> RECONCILE evidence bundle is missing",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "bundle_count": len(reports),
                "bundles": [report["bundle_id"] for report in reports],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
