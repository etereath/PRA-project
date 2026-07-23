# 任务12实机证据索引

本文为[任务12最终交接报告](task12_final_handoff_20260723.md)提供可核验的 Run ID、用途、结果文件位置和 SHA-256。哈希按 2026-07-23 本地运行归档中的实际结果文件字节计算。

运行归档根目录：

```text
D:\PRA_Runtime\shadowbot_queue\archive\<execution_attempt_id>
```

每个目录至少应保留同名 `.request.json`、`.result.json`、`.phase.json` 和对应校验文件。运行归档可能包含本机路径或运行环境信息，进入 GitHub PR 前必须先审查和脱敏，不得直接复制整个运行目录。

## 1. 成功动作基线

| Run ID | 商品 | 结果文件 SHA-256 |
| --- | --- | --- |
| `ATTEMPT-BATCH-T12-OPTIMIZED-COMMIT-20260721-02-01` | 卡布奇诺 B级，24.20 → 24.30 | `01c5b2fc3b82c14d89cff20a0ee3e74c3005b59080205d03433120b985ed4de9` |
| `ATTEMPT-BATCH-T12-OPTIMIZED-COMMIT-20260721-02-02` | 艾莎 B级，9.20 → 9.30 | `864319c8d67ca225feb87f403fa20e32d4891d7ca1de985e8fc19e29ec02054c` |
| `ATTEMPT-BATCH-T12-OPTIMIZED-COMMIT-20260721-02-03` | 卡布奇诺 C级，26.90 → 27.00 | `5183f58cd4444abcb7ef1f505637f4a840fef4f94c8c1d3b60f7a0de1f0ceba1` |
| `ATTEMPT-BATCH-T12-OPTIMIZED-COMMIT-20260721-02-04` | 艾莎 D级，15.10 → 15.20 | `9469c4c2bf1c1206fcd528cbae07b613fb34cae5b5fe5c60af46cd037ef9c516` |

用途：证明后续任务12是在已成功的提交动作链路上增加批次预扫描和 v4 合同，而不是重新实现另一套 COMMIT。

## 2. v4 正式闭环证据

| 批次 | Run ID | 结果 | 用途 | 结果文件 SHA-256 |
| --- | --- | --- | --- | --- |
| `BATCH-T12-FORMAL-COMMIT-20260722-02` | `ATTEMPT-c8976769bbcf471b` | 2/2 `VERIFIED` | 单次投递、页面行 1/4、跳过中间商品、数据库回写 | `7bf2109ff9ddae3ba08492e41640787082e62ddebf0f5059f35e6c9ab20b1b69` |
| `BATCH-T12-FORMAL-COMMIT-20260722-03` | `ATTEMPT-0a4da7c0645f4f85` | `OLD_PRICE_CHANGED/NOT_STARTED` | 全批次旧价门禁、0 次提交、完整页面快照 | `856dfd7ab8738f87e2531a11bcf5db622c6cdb64abceb8aff0c13856d8b7fa06` |
| `BATCH-T12-POST-CLEANUP-VALIDATION-20260722-01` | `ATTEMPT-b6c493e6eac54972` | 4/4 `VERIFIED` | 核心去重后的成功回归 | `89570859305b4252c9ee1079dbca7e53109eef950dba962d086d077ba878e01c` |
| `BATCH-T12-LATEST-TASKS-COMMIT-20260722-01` | `ATTEMPT-00631957fe0b4cc9` | 4/4 `VERIFIED` | 任务中心四商品常规队列 | `782a98eac9d208e6aeb16a6fba25a9ee00017bd2679e0e6c867256d8712b0367` |

仓库内人工报告：

- [双商品正式成功报告](../../outputs/task12/BATCH-T12-FORMAL-COMMIT-20260722-02.report.md)
- [五商品旧价阻断报告](../../outputs/task12/BATCH-T12-FORMAL-COMMIT-20260722-03.report.md)

## 3. 视口与性能证据

| 批次 | Run ID | 结果/耗时 | 用途 | 结果文件 SHA-256 |
| --- | --- | --- | --- | --- |
| `BATCH-T12-FAST-PATH-VALIDATION-20260722-01` | `ATTEMPT-354799e7788a4b92` | `ELEMENT_NOT_FOUND/NOT_STARTED`，27.531 秒 | 暴露页面保留上轮滚动位置；证明失败在副作用前 | `bf48a1dd61924016d438115a68b65a8b9b1d5c2bb55d0341156fd81b326becf7` |
| `BATCH-T12-FAST-PATH-VALIDATION-20260723-02` | `ATTEMPT-a023675861d24d34` | 4/4 `VERIFIED`，104.984 秒 | 修复视口恢复；保留冷态成功样本 | `fbea4d26cf7936b13fbbf10c888250e2a3594eb1960f3bec777aefde3d774401` |
| `BATCH-T12-WARM-FAST-PATH-20260723-01` | `ATTEMPT-52710408e5e1488a` | 4/4 `VERIFIED`，51.094 秒 | 最终暖态性能样本 | `0c4943a005ed18391b2819bd5f775ec6a3b44dde9ae06485a8129306a5feb25c` |

## 4. READ_ONLY 证据

| Run ID / Read batch ID | 范围和结果 | 结果文件 SHA-256 |
| --- | --- | --- |
| `ATTEMPT-T12-LOGIN-FAST-READ-20260721-01` / `READ-BATCH-T12-LOGIN-FAST-20260721-01` | 旧目标字段策略，5/5，16.228 秒 | `8e8fb587c0b973a6ece533d3fa529fc7b0265c98fdbe6dea7eb355bbb8b4967d` |
| `ATTEMPT-PLATFORM-ENDMARKER-READONLY-20260722-01` / `READ-BATCH-PLATFORM-ENDMARKER-20260722-01` | 当前完整页面快照，4/4，1 次扫描、0 次滚动、27.445 秒 | `267c2cf7f478b80902126bcd3408080a4127a2e09ce7df37a4731e8b50b8f576` |

两个样本读取范围不同。16.228 秒可以作为旧目标字段路径的历史性能基线，不能直接覆盖当前完整页面快照的验收口径。

## 5. 审查修复版实机证据

| 批次 | Run ID | 结果 | 用途 | 结果文件 SHA-256 |
| --- | --- | --- | --- | --- |
| `BATCH-T12-REMEDIATION-COMMIT-20260723-01` | `ATTEMPT-52c584afca044d79` | 4/4 `VERIFIED` | v12 原子导入、技术回执、ACK、逐项独立回读和写锁释放 | `4a7d2df8bbc9c98553844b538f648d41ff56fe79ce922ea7b47cb2f60969b826` |
| `BATCH-T12-CONTROLLED-UNKNOWN-20260723-02` | `ATTEMPT-0f30900b398045cc` | 1/1 `UNKNOWN` | 提交点击后受控制造不可判定结果；没有追加恢复 COMMIT | `3c28f255fd0b675337d1e20f130b4602b1f292a5adbae2c933a5ac2ad666b84a` |
| 同上 | `RECONCILE-046a063ae885fcb4f352` | `VERIFIED`，实际价格 10.30 | 确定性唯一 RECONCILE、任务恢复和 UNKNOWN 写锁释放 | `50316f907638e7fa96b0a0d30852ca2b991991c8fe5b6fc099f4a12a79eb0890` |

正常批次计数恒等式：

```text
total=4
= attempted(4) + not_attempted(0)
= verified(4) + failed(0) + unknown(0) + not_applied(0) + not_attempted(0)
```

正常批次按页面顺序执行卡布奇诺 E级、艾莎 C级、艾莎 D级和艾莎 B级，
分别独立回读为 12.60、7.90、9.10 和 10.10。受控 UNKNOWN 的艾莎 B级从
10.20 提交到 10.30，COMMIT 保持 UNKNOWN，唯一 RECONCILE 回读 10.30 后将
operation 归并为 `VERIFIED` 并释放写锁。

RECONCILE 截图：

```text
C:\Users\etere\AppData\Local\ShadowBot\evidence\vertical_slice\RECONCILE-046a063ae885fcb4f352_reconcile.png
SHA-256: 2329e5c823c4459d613d7dfd4f42ca5df5a39b9555357cdacff9b6f65d9cb141
```

复审使用的脱敏 request/result/phase/manifest/receipt 和 UNKNOWN→RECONCILE
原始链已进入仓库，统一入口为
[`docs/evidence/task12/index.md`](../evidence/task12/index.md)。该目录由
`scripts/export_task12_sanitized_evidence.py` 生成；CI 使用
`scripts/verify_task12_sanitized_evidence.py` 复算脱敏 request SHA、
instruction/manifest/batch/attempt 绑定、逐项身份、执行序号、计数恒等式和
UNKNOWN→RECONCILE 来源关系。上表绝对路径和人工哈希只保留为历史原始归档
定位，不再是 PR 复审的唯一证据。

## 6. 审查核对项

1. 在干净 checkout 运行 `python scripts/verify_task12_sanitized_evidence.py`。
2. 校验仓库内 request/result/phase 的 batch、attempt、instruction 和 manifest 绑定。
3. 检查逐商品计数满足总数恒等式。
4. 检查成功项 `actual_price=target_price` 且 `side_effect_state=VERIFIED`。
5. 检查失败样本在提交前为 `NOT_STARTED`，剩余项没有被执行。
6. 对照 SQLite 任务、批次、逐商品和 `listing_status` 回读。
7. 确认脱敏证据中本机/UNC 路径和 Worker 设备标识已经替换，业务身份、价格、
   时间和哈希字段仍保留。
8. 确认受控 UNKNOWN 只有一个 COMMIT Run ID 和一个确定性 RECONCILE ID，
   历史 UNKNOWN 事实未被覆盖，最终 operation/任务/写锁/平台状态投影一致。
