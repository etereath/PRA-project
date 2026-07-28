# 平台商品上下架执行报告

- 结果：VERIFIED
- 动作：`set_offline`
- 批次 ID：`BATCH-T13-OPTIMIZED-SET-OFFLINE-20260726-02`
- 运行 ID：`ATTEMPT-T13-OPTIMIZED-SET-OFFLINE-20260726-02`
- 已验证商品数：2
- UNKNOWN：0
- 部分生效：0

## 逐商品结果

| SKU | 商品 | 等级 | 结果 | 价格 | 库存 | 错误 |
|---|---|---|---|---:|---:|---|
| AISHA-E-45-Z | 艾莎 | E级 | VERIFIED | 7.50 | 2 | - |
| CAPPUCCINO-E-45-Z | 卡布奇诺 | E级 | VERIFIED | 8.00 | 1 | - |

## 数据库回写

- 已投影 SKU：AISHA-E-45-Z, CAPPUCCINO-E-45-Z
- 失败后两页扫描：未生成或未完整，不投影页面事实。
- 写锁依据逐商品结果释放、保留 UNKNOWN，或转为 REVIEW_BLOCKED。

