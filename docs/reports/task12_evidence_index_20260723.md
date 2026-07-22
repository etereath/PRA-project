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

## 5. 审查核对项

1. 重新计算 `.result.json` SHA-256，与本索引一致。
2. 校验同目录 request/result/phase 的 batch、attempt、instruction 和 manifest 绑定。
3. 检查逐商品计数满足总数恒等式。
4. 检查成功项 `actual_price=target_price` 且 `side_effect_state=VERIFIED`。
5. 检查失败样本在提交前为 `NOT_STARTED`，剩余项没有被执行。
6. 对照 SQLite 任务、批次、逐商品和 `listing_status` 回读。
7. 确认归档中的敏感配置、本机路径和临时日志不会未经审查进入 GitHub。
