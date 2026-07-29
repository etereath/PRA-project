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
from app.services.platform_mapping_input import (
    apply_platform_input,
    platform_options_from_rows,
)


OBSERVED_AT = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)


def _row(
    mapping_id: str,
    *,
    product_name: str,
    grade: str,
    status: str,
    sku: str = "",
    candidate_sku: str = "",
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
        "candidate_internal_sku": candidate_sku,
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


def test_disabled_mapping_preserves_candidate_without_resolving_sku() -> None:
    compiled = compile_product_mapping_rows(
        [
            _row(
                "MAP-CANDIDATE",
                product_name="艾莎",
                grade="A级",
                status="DISABLED",
                candidate_sku="AISHA-A-70-Z",
            )
        ],
        source_workbook_sha256="e" * 64,
    )

    resolution = compiled.resolve(
        platform_name="蚂蚁花团供应商",
        platform_product_name="艾莎",
        grade="A级",
        observed_at=OBSERVED_AT,
    )
    payload = json.loads(compiled.immutable_json.decode("utf-8"))

    assert resolution.mapping_status is ProductMappingStatus.DISABLED
    assert resolution.internal_sku is None
    assert payload["records"][0]["internal_sku"] is None
    assert (
        payload["records"][0]["candidate_internal_sku"]
        == "AISHA-A-70-Z"
    )


def test_disabled_platform_is_not_reenabled_by_verified_product_row() -> None:
    rows = [
        {
            "mapping_id": "PLATFORM-01",
            "mapping_kind": "PLATFORM",
            "platform_name": "测试平台",
            "mapping_status": "disabled",
        },
        {
            "mapping_id": "PRODUCT-01",
            "mapping_kind": "PRODUCT",
            "platform_name": "测试平台",
            "platform_product_name": "艾莎",
            "grade": "A级",
            "internal_sku": "AISHA-A",
            "mapping_status": "VERIFIED",
        },
    ]

    assert "测试平台" not in platform_options_from_rows(rows)


def test_active_platform_appears_once_and_product_rows_are_preserved() -> None:
    product_row = {
        "mapping_id": "PRODUCT-01",
        "mapping_kind": "PRODUCT",
        "platform_name": "测试平台",
        "platform_product_name": "艾莎",
        "grade": "A级",
        "internal_sku": "AISHA-A",
        "mapping_status": "VERIFIED",
        "remark": "保留商品映射",
    }
    rows = [
        {
            "mapping_id": "PLATFORM-01",
            "mapping_kind": "PLATFORM",
            "platform_name": "测试平台",
            "mapping_status": "active",
        },
        product_row,
    ]

    options = platform_options_from_rows(rows)
    result = apply_platform_input(rows, {"platform_name": "新增平台"})

    assert options.count("测试平台") == 1
    assert result.rows[1]["mapping_kind"] == "PRODUCT"
    assert result.rows[1]["internal_sku"] == "AISHA-A"
    assert result.rows[1]["remark"] == "保留商品映射"
    assert result.rows[-1]["mapping_kind"] == "PLATFORM"


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
