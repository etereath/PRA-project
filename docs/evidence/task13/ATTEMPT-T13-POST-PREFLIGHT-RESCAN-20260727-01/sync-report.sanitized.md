# 平台商品状态同步报告

- 结果：成功
- 批次 ID：`BATCH-T13-POST-PREFLIGHT-RESCAN-20260727-01`
- 运行 ID：`ATTEMPT-T13-POST-PREFLIGHT-RESCAN-20260727-01`
- 快照 ID：`SNAPSHOT-dfac7a5eb363ba8aa270e0e1`
- 平台：蚂蚁花团供应商
- 扫描范围：上架中、待上架
- 完整性：两页扫描及结束标记均已确认

## 商品结果

| SKU | 商品 | 等级 | 页面位置 | 上架中/待上架次数 | 状态投影 | 处理 |
|---|---|---|---|---:|---|---|
| 未映射 | 卡布奇诺（10枝） | A级 | ambiguous | 0/1 | 保留原值/未投影 | 已创建或更新人工复核 |
| 未映射 | 卡布奇诺（10枝） | C级 | ambiguous | 0/1 | 保留原值/未投影 | 已创建或更新人工复核 |
| CAPPUCCINO-A-70-Z | 卡布奇诺 | A级 | waiting_only | 0/1 | offline | 正常 |
| CAPPUCCINO-B-60-Z | 卡布奇诺 | B级 | neither | 0/0 | offline | 已创建或更新人工复核 |
| CAPPUCCINO-C-55-Z | 卡布奇诺 | C级 | neither | 0/0 | offline | 已创建或更新人工复核 |
| CAPPUCCINO-D-50-Z | 卡布奇诺 | D级 | neither | 0/0 | 保留原值/未投影 | 已创建或更新人工复核 |
| CAPPUCCINO-E-45-Z | 卡布奇诺 | E级 | waiting_only | 0/1 | offline | 正常 |
| 未映射 | 短枝单头玫—卡布奇诺 | O级 | ambiguous | 0/1 | 保留原值/未投影 | 已创建或更新人工复核 |
| 未映射 | 短枝单头玫—艾莎 | O级 | ambiguous | 0/1 | 保留原值/未投影 | 已创建或更新人工复核 |
| ZIXIA-0-FG-Z | 紫霞仙子 | 0级 | neither | 0/0 | 保留原值/未投影 | 已创建或更新人工复核 |
| ZIXIA-B-FG-Z | 紫霞仙子 | B级 | neither | 0/0 | 保留原值/未投影 | 已创建或更新人工复核 |
| 未映射 | 艾莎（10枝） | A级 | ambiguous | 0/1 | 保留原值/未投影 | 已创建或更新人工复核 |
| AISHA-A-70-Z | 艾莎 | A级 | waiting_only | 0/1 | offline | 正常 |
| AISHA-B-60-Z | 艾莎 | B级 | online_only | 1/0 | online | 正常 |
| AISHA-C-55-Z | 艾莎 | C级 | online_only | 1/0 | online | 正常 |
| AISHA-D-50-Z | 艾莎 | D级 | waiting_only | 0/1 | offline | 正常 |
| AISHA-E-45-Z | 艾莎 | E级 | waiting_only | 0/1 | offline | 正常 |

## 数据库结果

- 状态投影：9 项
- 当前异常：10 项
- 新建 Review：0 项
- 自动清除 Review：0 项
- 新建通知 Outbox：0 项
- 取消未发送通知：0 项
- 价格与库存：完整快照的最新页面观察值已投影到 `listing_status`，原始证据仍保留在快照商品项中。

