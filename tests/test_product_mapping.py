from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.enums import ProductMappingStatus
from app.services.product_mapping import (
    ProductMappingError,
    compile_product_mapping_rows,
    normalize_mapping_text,
    write_immutable_product_mapping,
)


OBSERVED_AT = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)


def _row(
    mapping_id: str,
    *,
    product_name: str,
    grade: str,
    status: str,
    sku: str = "",
    effective_from: str = "",
    effective_to: str = "",
) -> dict[str, object]:
    return {
        "mapping_id": mapping_id,
        "mapping_kind": "PRODUCT",
        "platform_name": "蚂蚁花团供应商",
        "platform_product_name": product_name,
        "normalized_platform_product_name": "",
        "grade": grade,
        "internal_sku": sku,
        "mapping_status": status,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "remark": "测试映射",
    }


@pytest.mark.parametrize(
    ("status", "sku", "expected_sku"),
    [
        ("VERIFIED", "AISHA-A", "AISHA-A"),
        ("UNMAPPED", "", None),
        ("AMBIGUOUS", "", None),
        ("DISABLED", "", None),
    ],
)
def test_resolve_supports_all_frozen_mapping_statuses(
    status: str,
    sku: str,
    expected_sku: str | None,
) -> None:
    compiled = compile_product_mapping_rows(
        [
            _row(
                f"MAP-{status}",
                product_name="  艾莎　",
                grade=" A ",
                status=status,
                sku=sku,
            )
        ],
        source_workbook_sha256="a" * 64,
    )

    resolution = compiled.resolve(
        platform_name="蚂蚁花团供应商",
        platform_product_name="艾莎",
        grade="A",
        observed_at=OBSERVED_AT,
    )

    assert resolution.mapping_status is ProductMappingStatus(status)
    assert resolution.internal_sku == expected_sku


def test_platform_registry_rows_are_not_compiled_as_product_mappings() -> None:
    compiled = compile_product_mapping_rows(
        [
            {
                "mapping_id": "PLATFORM-01",
                "mapping_kind": "PLATFORM",
                "platform_name": "寻梦",
                "mapping_status": "active",
            }
        ],
        source_workbook_sha256="b" * 64,
    )

    assert compiled.records == ()


def test_overlapping_effective_ranges_cannot_target_multiple_skus() -> None:
    with pytest.raises(ProductMappingError, match="multiple SKUs"):
        compile_product_mapping_rows(
            [
                _row(
                    "MAP-1",
                    product_name="艾莎",
                    grade="A",
                    status="VERIFIED",
                    sku="AISHA-A-1",
                    effective_from="2026-07-01T00:00:00+00:00",
                    effective_to="2026-08-01T00:00:00+00:00",
                ),
                _row(
                    "MAP-2",
                    product_name="艾莎",
                    grade="A",
                    status="VERIFIED",
                    sku="AISHA-A-2",
                    effective_from="2026-07-15T00:00:00+00:00",
                    effective_to="2026-09-01T00:00:00+00:00",
                ),
            ],
            source_workbook_sha256="c" * 64,
        )


def test_immutable_json_records_source_hash_and_uses_stable_utf8(
    tmp_path,
) -> None:
    rows = [
        _row(
            "MAP-1",
            product_name="艾莎",
            grade="A",
            status="VERIFIED",
            sku="AISHA-A",
        )
    ]
    first = compile_product_mapping_rows(
        rows,
        source_workbook_sha256="d" * 64,
    )
    second = compile_product_mapping_rows(
        list(reversed(rows)),
        source_workbook_sha256="d" * 64,
    )
    output_path = tmp_path / "product_mapping.json"

    write_immutable_product_mapping(first, output_path)
    raw = output_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))

    assert first.mapping_version == second.mapping_version
    assert raw == first.immutable_json
    assert payload["source_workbook_sha256"] == "d" * 64
    assert payload["records"][0]["platform_product_name"] == "艾莎"
    assert hashlib.sha256(raw).hexdigest() == first.mapping_version
    assert normalize_mapping_text(" ＡIＳＨＡ　玫瑰 ") == "aisha 玫瑰"
