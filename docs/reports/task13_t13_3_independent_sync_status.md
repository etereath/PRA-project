# 任务13 T13-3：独立 SYNC_STATUS 实施报告

## 结论

T13-3 的合同、发布、两页完整扫描、结果校验、原子导入和人工可读报告已经完成，并通过一轮独立实机 `SYNC_STATUS` READ_ONLY。

实机确认小程序的商品列表支持键盘导航。Worker 先点击第一件商品名称使列表获得焦点，再发送 `HOME` 建立顶部基准、发送 `END` 触发懒加载并确认“没有更多了”、最后发送 `HOME` 回顶并读取完整元素树。无需新增专用 `wx-scroll-view` 元素。

此前只读取首件商品的直接原因不是滚动失败，而是误把两页行索引步长都设为 `16`。实机复核确认“上架中”步长为 `16`，“待上架”步长为 `15`；拆分页面专属步长后，待上架页一次完整读取到 11 件商品。

本阶段没有点击价格、库存、上架、下架或最终确认按钮。任务状态仍等待后续阶段和最终审查，本报告不修改任务状态。

## 已实现能力

### 1. 单次两页完整扫描

- “上架中”使用任务11/12已经验证的 `1、17、33…` 商品行索引。
- “待上架”使用实机复核后的 `1、16、31…` 商品行索引。
- “上架中”价格偏移继续使用 `+9`。
- “待上架”价格偏移使用 T13-0 已确认的 `+8`，库存偏移仍为 `+6`。
- 一次刷新后先扫描“上架中”，再切换到“待上架”。
- 每页先聚焦商品名称并用 `HOME` 确认顶部身份，再用 `END` 触发完整加载，以“没有更多了”为结束条件，最后 `HOME` 回顶读取完整元素树。
- 任一页面无法确认顶部身份、结束标记或完整元素树时，整批生成失败快照。

### 2. 映射和页面位置计算

- Worker 使用与请求 `mapping_source_version` 哈希一致的 `product_identity_mapping.json`。
- 以“商品名称 + 等级”聚合两页观察，再映射到库存 SKU。
- 对映射清单中的商品补齐 `neither` 观察。
- 生成 `online_only / waiting_only / both / neither / ambiguous`。
- 未映射、映射冲突和页面重复身份不会被强行绑定到一个 SKU。

### 3. 原子导入

完整快照在一个事务中依次完成：

1. 接受结果回执；
2. 写入快照主表；
3. 写入快照商品项；
4. 投影 `online_status`；
5. 创建、更新或清除 `listing_anomaly_cases`；
6. 创建或取消 `review_tasks`；
7. 创建或取消 `notification_outbox`；
8. 更新 v5 批次结果；
9. 提交。

故障注入测试证明：状态投影后若发生异常，回执、快照、状态、异常和 Review 会一起回滚，不留下部分状态。

### 4. 状态与库存边界

- `online_only` 投影为 `online`。
- `waiting_only` 投影为 `offline`。
- `both` 投影为 `online`，同时创建页面异常 Review。
- `neither` 投影为 `offline`，同时创建页面异常 Review。
- `ambiguous` 保留原 `online_status`，同时创建页面异常 Review。
- 页面库存只写入 `listing_sync_snapshot_items` 作为证据。
- `listing_status.platform_stock_qty` 不会被 T13 `SYNC_STATUS` 覆盖。
- 任务11/12既有 READ_ONLY 库存投影逻辑没有修改。

### 5. 异常、Review 和通知

当前覆盖：

- `UNMAPPED_PRODUCT`
- `IDENTITY_MAPPING_CONFLICT`
- `ABSENT_FROM_BOTH_LISTS`
- `DUPLICATE_PAGE_IDENTITY`
- `PRESENT_IN_BOTH_LISTS`

页面异常 Review 统一携带 `blocked_actions`。新的完整快照证明异常消失时：

- 异常事实写入清除时间和清除快照；
- Review 使用 `cancelled`；
- 写入 `AUTO_CLEARED_BY_SNAPSHOT` 解决信息；
- 撤销未使用的 Review token；
- 取消仍未发送的通知 Outbox。

`shadowbot_partial_operation` 不在自动清除范围内。

### 6. 失败快照与 ACK

- 失败结果只写快照主表，不写商品项。
- 失败结果不投影 `online_status`。
- 失败结果不批量创建或清除异常和 Review。
- 数据库提交完成后才归档请求、结果和 phase，并写入 ACK。
- ACK 成功后，回执状态更新为 `WRITTEN`。

### 7. 人工可读报告

每次成功导入会生成 Markdown 报告，包含：

- 成功或失败结论；
- 批次 ID、运行 ID、快照 ID；
- 两页完整性；
- 逐商品 SKU、名称、等级、页面位置、出现次数和状态投影；
- 是否需要人工复核；
- 数据库投影、异常、Review 和通知计数；
- 库存未覆盖正式库存的说明。

报告不会把原始 JSON 逐字段抄写成正文。

## 主要代码

- `app/services/shadowbot_listing_sync.py`
- `app/services/shadowbot_listing_action_contract.py`
- `app/services/shadowbot_queue.py`
- `app/services/shadowbot_executor.py`
- `shadowbot/test2/vertical_slice_read_price.py`
- `shadowbot/test2/shadowbot_queue_worker.py`
- `scripts/run_shadowbot_listing_sync.py`
- `scripts/sync_shadowbot_test2.py`
- `tests/test_shadowbot_listing_sync.py`

## 验证结果

- T13-3 定向及旧队列合同测试：`98 passed`。
- 全量回归首次结果：`594 passed, 3 skipped, 97 subtests passed`，唯一失败是同步清单新增映射文件后，旧打包测试仍按 5 个文件计数。
- 修正打包测试后定向复核：`15 passed, 3 subtests passed`。
- 最终全量回归：`595 passed, 3 skipped, 97 subtests passed`。
- 增补“首次发现已映射商品时创建状态行但不投影库存”、回顶门禁和滚动视口选择器测试后：`19 passed`。
- 增加 T13-3 脱敏证据导出、独立校验器和 Windows/Linux CI 复算后，全量回归：`596 passed, 3 skipped, 97 subtests passed`。
- Python 编译检查通过。
- Ruff 检查通过。

## 实机探索结果

- 通过批次：`BATCH-T13-KEYBOARD-SYNC-20260725-04`。
- Run ID：`ATTEMPT-T13-KEYBOARD-SYNC-20260725-04`。
- Snapshot ID：`SNAPSHOT-dc0c443e6cab3fb8210bd39d`。
- 运行时间：`2026-07-25T08:27:05+00:00` 至 `2026-07-25T08:28:25+00:00`，约 80 秒。
- 页面观察：上架中 1 件，待上架 11 件；映射清单补齐 5 个 `neither`，快照共 17 项。
- 数据库导入：8 个 SKU 完成状态投影；创建 11 个异常事实、11 个 Review 和 11 个通知 Outbox。
- 异常构成：5 个已映射 SKU 在两页均不存在；6 个页面商品不在库存 SKU 映射中。
- request、phase、result、校验文件、ACK 和人工可读报告已归档到 `D:\PRA_Runtime\shadowbot_queue\archive\ATTEMPT-T13-KEYBOARD-SYNC-20260725-04`。
- 前三轮键盘方案尝试均未投影数据库：`01`、`02` 因不完整扫描被拒绝，`03` 因实验性全页枚举超时中止。
- 所有尝试均为 READ_ONLY，平台价格、库存和上下架状态没有被写入。
- 当前 Worker 保持长期监听，不因单轮 READ_ONLY 停止或重启。

## 尚未宣称完成的部分

- T13-3 脱敏 request/result/phase/receipt/ACK、数据库回读和人工报告已经进入 `docs/evidence/task13`，并由 `scripts/verify_task13_sanitized_evidence.py` 自动复算。
- T13-4 单商品 `SET_ONLINE` 及后续写操作尚未开始；T13-5 必须对 T13-4 成功上架的同一 `internal_sku` 执行 `SET_OFFLINE`。
