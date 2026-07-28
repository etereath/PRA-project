# 任务13 T13-1 状态模型与 v5 合同冻结

## 1. 阶段结论

2026-07-25，人工审查已接受 T13-0 的全部页面探索条目，项目进入 T13-1。

本阶段已把页面事实、上下架状态、自动化门禁和两阶段副作用拆分为独立合同，并实现不依赖数据库的 v5 合同原语与校验器。任务12 的 v4 改价合同保持不变。

本文件只冻结 T13-1 的设计和本地代码边界，不代表任务13整体完成，不修改任务状态，也不证明真实上架或下架已经验收。

## 2. 已接受的页面结构

事实来源：

- [T13-0 页面结构探索报告](task13_ui_discovery_report.md)
- [T13-0 选择器清单](../evidence/task13/t13_0_ui_discovery_selector_manifest.json)

页面名称和结束标记冻结为：

| 页面 | 正常结束标记 | 过渡标记 |
|---|---|---|
| 上架中 | `没有更多了` | 无固定依赖 |
| 待上架 | `没有更多了` | `加载中...` |

两页商品行公共基址：

```text
row_base = 1 + 16 × (row_number - 1)
```

| 页面 | 库存 | 价格 | 动作按钮 |
|---|---:|---:|---:|
| 上架中 | `row_base + 6` | `row_base + 9` | 下架 `row_base + 14` |
| 待上架 | `row_base + 6` | `row_base + 8` | 上架 `row_base + 13` |

行号只在一次完整扫描内用于排序和重新定位，不进入任务合同，也不作为商品身份。

SET_ONLINE 的业务必填值是目标价格和目标库存。第一版使用列表中的独立价格、库存修改窗口；各窗口点击“确认”才形成资料写入边界。上架或下架入口只打开确认窗口，确认窗口中的最终“确认”才形成上下架状态写入边界。

## 3. 四维状态模型

### 3.1 平台可售事实

`online_status` 只允许：

```text
online
offline
```

### 3.2 页面位置事实

`listing_location` 只允许：

```text
online_only
waiting_only
both
neither
ambiguous
```

投影规则：

| `listing_location` | `online_status` | 自动化处置 |
|---|---|---|
| `online_only` | `online` | 无其他阻断时可执行 |
| `waiting_only` | `offline` | 无其他阻断时可执行 |
| `both` | `online` | 创建 Review，禁止自动写入 |
| `neither` | `offline` | 创建 Review，禁止自动写入 |
| `ambiguous` | 保留原值 | 创建 Review，禁止自动写入 |

`automation_disposition` 是由最新有效页面位置、开放 Review 和活动写锁实时派生的展示值，不建立第二个长期事实源。

### 3.3 operation 与副作用

operation 结果：

```text
VERIFIED
NOT_APPLIED
PARTIALLY_APPLIED
NEEDS_RECONCILIATION
```

写锁状态：

```text
ACTIVE
UNKNOWN
REVIEW_BLOCKED
RELEASED
```

`manual_review` 不是平台上下架状态。平台位置无法分类时使用页面异常 Review；最终确认已点击但副作用无法确认时使用 `NEEDS_RECONCILIATION + UNKNOWN`，并保留唯一 RECONCILE 写锁。

`PARTIALLY_APPLIED` 使用 `REVIEW_BLOCKED`。人工处置冻结为：

| 人工决定 | 原 operation | 写锁 |
|---|---|---|
| 接受当前平台结果且已人工处理 | `resolution_status=MANUAL_HANDLED` | 与处置记录在同一事务释放 |
| 批准明确的纠正操作 | 标记 `CORRECTIVE_ACTION_AUTHORIZED` 或 `SUPERSEDED` | 在同一事务创建新 operation 并把锁转移给它 |
| 暂时无法处理 | 保持 `PARTIALLY_APPLIED` | 保持 `REVIEW_BLOCKED` |

创建普通新任务不能绕过或释放 `REVIEW_BLOCKED`，也不能先释放锁、稍后再创建纠正任务。

## 4. v4 与 v5 版本边界

任务12继续使用：

```text
contract_version = 4
schema_version = shadowbot-commit-batch-request-1.2
```

任务13新增：

```text
contract_version = 5
manifest = shadowbot-listing-action-manifest-1.0
request = shadowbot-listing-action-batch-request-1.0
result = shadowbot-listing-action-batch-result-1.0
phase = shadowbot-listing-action-batch-phase-1.0
snapshot = shadowbot-listing-sync-snapshot-1.0
anomaly = shadowbot-listing-anomaly-1.0
gate_summary = shadowbot-listing-action-gate-summary-1.0
```

v5 的动作只允许：

```text
set_online
set_offline
sync_status
```

一个批次只能包含一种动作。v5 代码不得修改 v4 字段、哈希或执行语义。

## 5. v5 请求合同

所有写入商品项必须绑定：

- `source_task_id`
- `operation_id`
- `item_execution_attempt_id`
- `internal_sku`
- `expected_product_name`
- `expected_grade`
- `page_identity_key`
- `write_identity_key`
- `item_payload_sha256`

禁止在请求中携带 `row_index`、`ordinal`、`page_position` 等页面位置字段。Worker 必须在执行前完整扫描，以页面身份动态匹配全部目标，确认唯一后再按实时页面顺序生成执行计划。

SET_ONLINE 必须携带规范化的 `target_price` 和 `target_inventory`。SET_OFFLINE 不得伪造目标价格或库存。SYNC_STATUS 是 `READ_ONLY`，不得携带写任务、operation、gate summary 或开发确认。

开发测试可以携带固定批次确认文本：

```text
确认授权批次 {batch_id} 以上{count}项真实COMMIT
```

正式运行请求不得携带开发确认字段；它以任务中心有效任务和 action gate 为授权依据。

## 6. 请求、phase 和结果绑定

请求使用 `manifest_sha256` 和 `instruction_hash` 保护稳定指令。

phase 必须绑定：

- `batch_id`
- `execution_attempt_id`
- `instruction_hash`
- `manifest_sha256`
- `request_file_sha256`
- 当前商品的 task、operation、item attempt、SKU 和 item hash
- `phase_snapshot_sha256`

最终确认点击的 `ACTION_CLICKED` 还必须记录 `clicked_at`、`detail_effect_state` 和 `listing_effect_state`。

结果必须重新绑定 request 的批次、执行尝试、动作、manifest、instruction 和全部逐商品身份，并提供 `result_payload_sha256`。商品项缺失、重复、被替换或 hash 不一致时，Importer 必须拒绝结果。

Worker 不直接写 SQLite。最终确认点击前后只写耐久 phase：

```text
ACTION_INTENT_RECORDED
→ 最终确认
→ ACTION_CLICKED
```

Result Importer 或 Watchdog recovery 校验绑定后，才把 `clicked_at` 和 `operation_id` 投影为 `last_listing_change_at`、`last_listing_operation_id`。结果尚未导入期间由 `ACTIVE / UNKNOWN` 写锁阻止其他写操作。

## 7. SYNC_STATUS 快照与异常

完整快照必须分别证明：

- 上架中扫描已完成且结束标记可靠；
- 待上架扫描已完成且结束标记可靠；
- 两页各自的开始、完成时间；
- 每个页面身份在两页的出现次数；
- 页面行身份、价格、库存和观察时间；
- SKU 映射结果和诊断代码。

只有完整快照可以生成新的 `listing_location`。快照只有在：

```text
snapshot_complete = true
AND scan_started_at >= last_listing_change_at
```

时才可作为当前页面位置证据。任何确认的上架或下架写操作都会使旧位置快照失效；价格更新不会使位置快照失效。

异常合同支持无 SKU 和多 SKU 冲突，至少覆盖：

```text
UNMAPPED_PRODUCT
IDENTITY_MAPPING_CONFLICT
ABSENT_FROM_BOTH_LISTS
DUPLICATE_PAGE_IDENTITY
PRESENT_IN_BOTH_LISTS
```

异常项必须保存稳定主体键、去重键、页面身份、受影响 SKU、解决策略和 `blocked_actions`。`UNMAPPED_PRODUCT` 不绑定内部 SKU；`IDENTITY_MAPPING_CONFLICT` 必须记录多个受影响 SKU。

快照、状态投影、异常事实、Review 和初始通知在 T13-2/T13-3 落地时必须使用同一 SQLite 事务；T13-1 不创建相关表。

页面异常自动消失时，任务13第一版复用现有 Review 的 `cancelled`，并保存：

```json
{
  "resolution_type": "AUTO_CLEARED_BY_SNAPSHOT",
  "resolved_by_snapshot_id": "...",
  "previous_reason_code": "..."
}
```

不在任务13第一版扩大全局 Review 状态枚举。`shadowbot_partial_operation` 不得被快照自动取消，只能由人工明确处理。

## 8. action gate

统一入口：

```text
evaluate_automation_gate(action_type, internal_sku, gate_phase, ...)
```

门禁阶段：

```text
PRE_PUBLISH
POST_PUBLISH_PREFLIGHT
```

默认阻断：

- 与当前动作匹配的开放 Review；
- `ACTIVE / UNKNOWN / REVIEW_BLOCKED` 写锁；
- `both / neither / ambiguous`；
- SET_ONLINE 缺失有效完整两页快照；
- SET_OFFLINE 的“上架中”扫描不完整或身份不唯一。

POST_PUBLISH_PREFLIGHT 只允许当前 operation 自己持有的 `ACTIVE` 锁；其他锁仍阻断。

任务12 UPDATE_PRICE 的第一阶段门禁暂不强制新鲜完整两页快照，继续保留自己的实时“上架中”预扫描、唯一身份和旧价校验。

任务13 SYNC_STATUS 的库存只进入快照证据，不投影正式库存。本规则不修改任务11/12 已验收的 READ_ONLY 价格、平台库存、上下架状态和观察时间投影。SET_ONLINE 成功后可以用后置回读更新 `platform_stock_qty`，来源标记为 `SET_ONLINE_POSTCHECK`。

SET_ONLINE 中 `online_only` 且价格库存都符合目标时为 `ALREADY_APPLIED`；任一异常或在线资料不符合目标，整批必须在首次写入前阻断。

SET_OFFLINE 只扫描“上架中”：目标唯一存在时执行，完整扫描后出现次数为 0 时为 `ALREADY_APPLIED`。

## 9. 两阶段副作用和计数

SET_ONLINE 分为：

1. 全部目标的资料保存与统一回读；
2. 依次执行最终上架确认与后置回读。

已保存资料但尚未上架不是 `NOT_ATTEMPTED`，而是 `PARTIALLY_APPLIED + REVIEW_BLOCKED`。最终确认已点击但无法回读是 `NEEDS_RECONCILIATION + UNKNOWN`。尚未发生任何写入的后续项才可以是 `NOT_ATTEMPTED` 或 `NOT_APPLIED`。

中断矩阵冻结为：

| 中断点 | 已知事实 | operation 结果 | 写锁 |
|---|---|---|---|
| 资料已保存并回读，尚未上架 | 资料已变更 | `PARTIALLY_APPLIED` | `REVIEW_BLOCKED` |
| 当前商品资料确认已点击但无法回读 | 资料副作用未知 | `NEEDS_RECONCILIATION` | `UNKNOWN` |
| 资料阶段尚未开始的后续项 | 无副作用 | `NOT_ATTEMPTED` 或 `NOT_APPLIED` | `RELEASED` |
| 上架已确认并完成回读 | 全部目标状态已生效 | `VERIFIED` | `RELEASED` |
| 上架最终确认已点击但无法回读 | 上架副作用未知 | `NEEDS_RECONCILIATION` | `UNKNOWN` |
| 前序 UNKNOWN 后的后续项已保存资料但未上架 | 已发生资料副作用 | `PARTIALLY_APPLIED` | `REVIEW_BLOCKED` |

SET_ONLINE RECONCILE 必须扫描两页并核对目标资料：

| 对账结果 | operation 结果 |
|---|---|
| 唯一在上架中，且价格库存符合目标 | `VERIFIED` |
| 唯一在待上架，尚未上线 | `NOT_APPLIED` 或保留已知的 `PARTIALLY_APPLIED` |
| 同时存在、两页均无、身份重复或扫描不完整 | 继续 `NEEDS_RECONCILIATION` |
| 已在线但价格或库存不符合目标 | 依据资料 phase 分类，不得直接 `VERIFIED` |

SET_OFFLINE RECONCILE 只完整扫描“上架中”：出现次数为 0 时 `VERIFIED`，唯一存在时 `NOT_APPLIED`，重复或扫描不完整时继续 `NEEDS_RECONCILIATION`。

批次优先级：

```text
存在 UNKNOWN -> batch_status = UNKNOWN
否则存在 PARTIALLY_APPLIED -> batch_status = PARTIAL
```

统一计数恒等式：

```text
verified_count
+ unknown_count
+ partial_effect_count
+ not_attempted_count
+ failed_count
= batch_target_count
```

## 10. 本地实现位置

- `app/shadowbot_listing_contract.py`：无数据库依赖的 v5 枚举、状态投影、快照有效性和计数语义。
- `app/services/shadowbot_listing_action_contract.py`：manifest、request、phase、result、snapshot 和 anomaly 校验。
- `app/services/listing_automation_gate.py`：按动作、Review 和写锁派生门禁。
- `tests/test_task13_listing_contract.py`：T13-1 定向合同测试。

## 11. 验证结果

2026-07-25 本地定向验证：

- T13-1 合同、状态和门禁：`24 passed`；
- 任务12 v4 合同与编排兼容：`23 passed`。

这些结果只证明本地合同和兼容边界，不等于 Runtime Schema v13、Importer、Watchdog 或影刀实机流程已完成。

## 12. T13-2 前的明确边界

本阶段未执行：

- Runtime Schema v13 建表或迁移；
- `shadowbot_batch_registry` 和 v5 账本持久化；
- Result Importer 或 Watchdog 的 v5 投影；
- ShadowBot Worker v5 部署；
- 真实上架、下架或资料保存；
- 任务13状态修改。

下一阶段应以本文件和已接受的 T13-0 页面事实为输入实施 Runtime Schema v13，并先完成 v12→v13 无损迁移和 v4 回归门禁。

T13-2 迁移验收至少包括：

- 现有 v4 operation 原 ID、任务、价格、attempt、checkpoint 和 approved payload hash 保持不变；
- v4 operation 回填 `action_type=update_price`，上下架字段保持空值；
- 现有 v4 批次回填公共 batch registry；
- `ACTIVE / UNKNOWN / RELEASED` 历史写锁原样保留，不得把 UNKNOWN 误释放；
- v12→v13 升级、重复迁移幂等、新库初始化和备份恢复后外键完整；
- v4 成功批次和 UNKNOWN→RECONCILE 账本仍可查询；
- 任务12现有合同、编排和证据校验继续通过。
