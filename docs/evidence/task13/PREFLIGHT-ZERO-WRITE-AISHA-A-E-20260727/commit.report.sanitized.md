# 平台商品上下架执行报告

- 结果：FAILED
- 动作：`set_online`
- 批次 ID：`BATCH-T13-PREFLIGHT-ZERO-WRITE-20260727-01`
- 运行 ID：`ATTEMPT-591f3a642e2b43f9`
- 已验证商品数：0
- UNKNOWN：0
- 部分生效：0

## 逐商品结果

| SKU | 商品 | 等级 | 结果 | 价格 | 库存 | 错误 |
|---|---|---|---|---:|---:|---|
| AISHA-A-70-Z | 艾莎 | A级 | NOT_ATTEMPTED | - | - | - |
| AISHA-E-45-Z | 艾莎 | E级 | NOT_APPLIED | - | - | LISTING_DATA_MISMATCH |

## 数据库回写

- 已投影 SKU：无
- 失败后两页扫描：未生成或未完整，不投影页面事实。
- 写锁依据逐商品结果释放、保留 UNKNOWN，或转为 REVIEW_BLOCKED。

