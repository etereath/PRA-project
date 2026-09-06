# 平台商品上下架执行报告

- 结果：UNKNOWN
- 动作：`set_offline`
- 批次 ID：`BATCH-T13-CONTROLLED-UNKNOWN-20260726-01`
- 运行 ID：`ATTEMPT-T13-CONTROLLED-UNKNOWN-20260726-01`
- 已验证商品数：1
- UNKNOWN：1
- 部分生效：0

## 逐商品结果

| SKU | 商品 | 等级 | 结果 | 价格 | 库存 | 错误 |
|---|---|---|---|---:|---:|---|
| AISHA-B-60-Z | 艾莎 | B级 | NOT_ATTEMPTED | - | - | - |
| AISHA-C-55-Z | 艾莎 | C级 | VERIFIED | 6.00 | 20 | - |
| AISHA-D-50-Z | 艾莎 | D级 | NEEDS_RECONCILIATION | - | - | CONTROLLED_AFTER_ACTION_CLICK_UNKNOWN |

## 数据库回写

- 已投影 SKU：AISHA-C-55-Z
- 失败后两页扫描：未生成或未完整，不投影页面事实。
- 写锁依据逐商品结果释放、保留 UNKNOWN，或转为 REVIEW_BLOCKED。

