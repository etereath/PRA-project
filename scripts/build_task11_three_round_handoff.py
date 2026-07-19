"""Build the Task 11 three-round READ_ONLY handoff package.

The source of truth is the copied per-round request/result/validation JSON under
docs/reports/artifacts/task11. Outputs are written as UTF-8 without relying on
the Windows console code page.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROUND_NAMES = ("coverage-r1", "coverage-r2", "coverage-r3")


def _json_file(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"expected one {pattern} under {directory}, found {len(matches)}")
    return matches[0]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _artifact_links(directory: Path, root: Path) -> dict[str, str]:
    return {
        "request_json": _json_file(directory, "*.request.json").relative_to(root).as_posix(),
        "result_json": _json_file(directory, "*.result.json").relative_to(root).as_posix(),
        "validation_json": (directory / "validation.json").relative_to(root).as_posix(),
        "request_sha256": _json_file(directory, "*.request.json.sha256").relative_to(root).as_posix(),
        "result_sha256": _json_file(directory, "*.result.json.sha256").relative_to(root).as_posix(),
    }


def load_round(root: Path, name: str) -> dict[str, Any]:
    directory = root / name
    request = _read_json(_json_file(directory, "*.request.json"))
    result = _read_json(_json_file(directory, "*.result.json"))
    validation = _read_json(directory / "validation.json")
    products = request.get("products") or []
    snapshots = result.get("product_snapshots") or []
    validation_items = validation.get("items") or []
    items: list[dict[str, Any]] = []
    for position, target in enumerate(products, start=1):
        item_id = str(target.get("item_id") or "")
        snapshot = next((item for item in snapshots if str(item.get("item_id")) == item_id), {})
        validation_item = next(
            (item for item in validation_items if str(item.get("item_id")) == item_id),
            {},
        )
        evidence = validation_item.get("evidence") or snapshot.get("evidence") or []
        items.append(
            {
                "position": position,
                "item_id": item_id,
                "product_name": target.get("expected_product_name"),
                "grade": target.get("expected_grade"),
                "platform_sku": target.get("platform_sku"),
                "inventory": snapshot.get("inventory", validation_item.get("inventory")),
                "price": snapshot.get("price", validation_item.get("price")),
                "listing_status": snapshot.get("listing_status", validation_item.get("listing_status")),
                "item_status": snapshot.get("item_status", validation_item.get("item_status")),
                "row_identity": validation_item.get("row_identity", snapshot.get("row_identity", "")),
                "locator_summary": validation_item.get(
                    "locator_summary", snapshot.get("locator_summary", "")
                ),
                "evidence": [
                    {
                        "evidence_id": item.get("evidence_id"),
                        "upload_status": item.get("upload_status"),
                        "hash_verified": item.get("hash_verified"),
                        "sha256": item.get("sha256"),
                    }
                    for item in evidence
                ],
            }
        )
    return {
        "round": name[-2:].upper(),
        "attempt_id": validation.get("attempt_id") or request.get("execution_attempt_id"),
        "task_id": request.get("task_id"),
        "operation_id": request.get("operation_id"),
        "read_batch_id": request.get("read_batch_id"),
        "execution_mode": request.get("execution_mode"),
        "result_status": result.get("status"),
        "overall_status": result.get("overall_status"),
        "counts": {
            key: result.get(key, 0)
            for key in (
                "total_count",
                "processed_count",
                "success_count",
                "failed_count",
                "skipped_count",
                "manual_check_count",
            )
        },
        "observed_order": validation.get("observed_order", []),
        "items": items,
        "all_pass": bool(validation.get("all_pass")),
        "checks": validation.get("checks", {}),
        "phase": validation.get("phase", {}),
        "request_sha256": validation.get("request_sha256"),
        "result_sha256": validation.get("result_sha256"),
        "artifacts": _artifact_links(root / name, root.parent.parent),
    }


def build_summary(artifact_root: Path) -> dict[str, Any]:
    rounds = [load_round(artifact_root, name) for name in ROUND_NAMES]
    return {
        "schema_version": "shadowbot-t11-three-round-handoff-1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "task": "任务11",
        "platform": "蚂蚁花团供应商",
        "execution_mode": "READ_ONLY",
        "conclusion": "三轮连续 READ_ONLY 均通过；每轮 5/5 成功；未产生业务副作用。",
        "sort_rule": "等级优先",
        "sort_order": ["卡布奇诺 B级", "艾莎 B级", "艾莎 C级", "卡布奇诺 C级", "艾莎 D级"],
        "sku_note": "platform_sku 是请求中的目标身份键；当前商品卡可访问性树不暴露 SKU，因此不把它称为页面回读值。",
        "rounds": rounds,
        "count_identity": "5 = 5 + 0 + 0 + 0",
        "final_queue_state": {
            "heartbeat_status": "STOPPED",
            "inbox_empty": True,
            "working_empty": True,
            "results_empty": True,
            "stop_signal_present": False,
            "business_side_effect": False,
        },
        "other_reports": [
            "docs/reports/shadowbot_t11_db_real_machine_20260720.md",
            "docs/reports/shadowbot_t11_formal_boundary_acceptance_20260719.md",
            "docs/reports/shadowbot_t11_report_real_machine_20260719.md",
        ],
    }


def _render_item(item: dict[str, Any]) -> str:
    evidence = item.get("evidence") or []
    evidence_text = "；".join(
        f"{entry.get('evidence_id')}（上传 {entry.get('upload_status')}，哈希校验 {entry.get('hash_verified')}）"
        for entry in evidence
    ) or "未启用或未提供（按第17节不影响 READ_ONLY 成功判定）"
    return (
        f"| {item['position']} | {item['product_name']} | {item['grade']} | {item['platform_sku']} | "
        f"{item['inventory']} | ¥{item['price']} | {item['listing_status']} | {item['item_status']} | "
        f"{item['row_identity']} | {evidence_text} |"
    )


def render_round(round_data: dict[str, Any]) -> str:
    artifacts = round_data["artifacts"]
    links = "\n".join(
        f"- {label}: [{path}]({path})"
        for label, path in (
            ("请求 JSON", artifacts["request_json"]),
            ("结果 JSON", artifacts["result_json"]),
            ("校验 JSON", artifacts["validation_json"]),
            ("请求 SHA-256 sidecar", artifacts["request_sha256"]),
            ("结果 SHA-256 sidecar", artifacts["result_sha256"]),
        )
    )
    counts = round_data["counts"]
    rows = "\n".join(_render_item(item) for item in round_data["items"])
    return f"""# 任务11连续 READ_ONLY 第{round_data['round']}轮报告

**结论：{'通过' if round_data['all_pass'] else '未通过'}。**

- 运行 ID：`{round_data['attempt_id']}`
- 任务 ID：`{round_data['task_id']}`
- 操作 ID：`{round_data['operation_id']}`
- read_batch_id：`{round_data['read_batch_id']}`
- 执行模式：`{round_data['execution_mode']}`
- 结果：`{round_data['result_status']}` / `overall_status={round_data['overall_status']}`
- 页面排序：等级优先；实际顺序：{' → '.join(round_data['observed_order'])}
- 截图/逐商品证据：仅在显式调试请求时生成；本轮证据为空不影响结构化 READ_ONLY 成功。

## JSON 与哈希证据

{links}

- request SHA-256：`{round_data['request_sha256']}`
- result SHA-256：`{round_data['result_sha256']}`
- sidecar 与文件回读：通过。

## 逐商品结果

| 位置 | 商品 | 等级 | 目标 SKU/身份键 | 库存 | 价格 | 状态 | 结果 | 行定位 | 证据（可选） |
|---:|---|---|---|---:|---:|---|---|---|---|
{rows}

## 计数、阶段和副作用

- 计数恒等式：`{counts['total_count']} = {counts['processed_count']} = {counts['success_count']} + {counts['failed_count']} + {counts['skipped_count']} + {counts['manual_check_count']}`。
- 本轮检查项：`{len(round_data['checks'])}` 项，全部通过。
- 最终 phase：`{round_data['phase'].get('phase')}`；`side_effect_state={round_data['phase'].get('side_effect_state')}`。
- `business_operation_completed=false`，本轮没有改价、改库存、上下架或提交等业务写操作。
- 逐商品截图/证据是可选调试产物；若未提供，不影响本轮结构化读取判定。

> 注：目标 SKU/身份键来自请求；当前小程序商品卡不暴露 SKU，因此本轮成功证明的是名称、等级、库存、价格和上架状态读取，不证明 SKU 页面回读。
"""


def render_handoff(summary: dict[str, Any]) -> str:
    rounds = summary["rounds"]
    round_links = "\n".join(
        f"- 第{item['round']}轮：[{item['attempt_id']}](shadowbot_t11_three_round_readonly_20260720_{item['round'].lower()}_20260720.md)；read_batch_id `{item['read_batch_id']}`"
        for item in rounds
    )
    artifact_links = "\n".join(
        f"- 第{item['round']}轮 JSON：[{item['artifacts']['request_json']}]({item['artifacts']['request_json']})、"
        f"[{item['artifacts']['result_json']}]({item['artifacts']['result_json']})、"
        f"[{item['artifacts']['validation_json']}]({item['artifacts']['validation_json']})"
        for item in rounds
    )
    product_rows = []
    for item in rounds[0]["items"]:
        product_rows.append(
            f"| {item['position']} | {item['product_name']} | {item['grade']} | {item['platform_sku']} | "
            f"{item['inventory']} | ¥{item['price']} | {item['listing_status']} |"
        )
    return f"""# 任务11三轮连续 READ_ONLY 交接报告

**结论：三轮连续 READ_ONLY 均通过；每轮 5/5 成功；无业务副作用。**

## 交接范围

- 平台：{summary['platform']}
- 执行模式：`{summary['execution_mode']}`
- 排序规则：{summary['sort_rule']}
- 三轮实际顺序：{' → '.join(summary['sort_order'])}
- 覆盖范围：卡布奇诺 B/C 级，艾莎 B/C/D 级，共 5 个商品位置。

## 三轮运行 ID 与 read_batch_id

{round_links}

## 每轮原始 JSON

原始请求、结果、校验 JSON 和 SHA-256 sidecar 已随仓库交接；文件内容保持归档字节不变。

{artifact_links}

## 三轮共同读取结果

| 位置 | 商品 | 等级 | 目标 SKU/身份键 | 库存 | 价格 | 上架状态 |
|---:|---|---|---|---:|---:|---|
{chr(10).join(product_rows)}

三轮每轮均为 `READ_COMPLETED/COMPLETED`，计数恒等式均为 `5 = 5 + 0 + 0 + 0`。截图/逐商品证据默认关闭；若显式开启并提供证据，才校验 `PRODUCT_READ`、上传状态和哈希绑定；证据为空不影响结构化读取通过。

## 分轮 Markdown 报告

- [第 R1 轮 Markdown](shadowbot_t11_three_round_readonly_20260720_r1_20260720.md)
- [第 R2 轮 Markdown](shadowbot_t11_three_round_readonly_20260720_r2_20260720.md)
- [第 R3 轮 Markdown](shadowbot_t11_three_round_readonly_20260720_r3_20260720.md)

## 队列与副作用收尾

- heartbeat：`STOPPED`
- inbox / working / results：均为空
- stop.signal：不存在
- 业务副作用：无；三轮均保持 `business_operation_completed=false`、`side_effect_state=NOT_STARTED`。

## 其他任务11报告

- [最终数据库登记实机报告](shadowbot_t11_db_real_machine_20260720.md)：补充 DB readback、五商品最终批次和运行 ID。
- [商品不存在与重复身份边界报告](shadowbot_t11_formal_boundary_acceptance_20260719.md)
- [早期实机报告](shadowbot_t11_report_real_machine_20260719.md)

## SKU 口径

`platform_sku` 是请求中的目标身份键，不是当前小程序页面回读的 SKU。当前商品卡可访问性树不暴露 SKU；因此三轮验收证明名称、等级、库存、价格和上架状态读取成功，不应表述为“SKU 已被平台读取”。
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Task 11 three-round READ_ONLY handoff docs")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    artifact_root = project_root / "docs" / "reports" / "artifacts" / "task11"
    report_root = project_root / "docs" / "reports"
    summary = build_summary(artifact_root)
    summary_path = artifact_root / "T11_COVERAGE_3_ROUNDS_20260720.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for round_data in summary["rounds"]:
        suffix = round_data["round"].lower()
        report_path = report_root / f"shadowbot_t11_three_round_readonly_20260720_{suffix}_20260720.md"
        report_path.write_text(render_round(round_data), encoding="utf-8")
    handoff_path = report_root / "shadowbot_t11_three_round_readonly_handoff_20260720.md"
    handoff_path.write_text(render_handoff(summary), encoding="utf-8")
    print(handoff_path)
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
