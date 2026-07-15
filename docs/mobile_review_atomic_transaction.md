# Mobile Review 单事务实现说明

## 事务边界

Mobile Review 的 POST resolve 入口现在只在事务前执行无副作用的动作解析、Token HMAC 计算和请求 payload 格式校验。最终状态判断与写入由 `SQLiteRuntimeRepository.resolve_mobile_review_atomic(...)` 完成：

1. 通过同一 SQLite 连接执行 `BEGIN IMMEDIATE`。
2. 在该连接内重新读取 `review_tokens`、`review_tasks` 和关联 `tasks`。
3. 校验 Token 绑定关系、未使用、未撤销、未过期、动作许可和 review 仍为 `pending`。
4. 在消费 Token 前确认 `source_task_id` 非空、关联 task 存在，且当前状态属于该 action 的允许起始状态。
5. 用条件 `UPDATE` 消费 Token，并要求 `rowcount = 1`。
6. 用条件 `UPDATE ... WHERE review_status = 'pending'` 写入 review resolution。
7. 用条件 `UPDATE ... WHERE task_status = 当前状态` 推进源 task，并要求 `rowcount = 1`。
8. 在同一连接写入 `task_status_history`。
9. 在提交前把事务内读取的行转换为领域对象；转换失败也会回滚。
10. 领域对象转换成功后统一提交。

任何异常都会回滚 Token、review、task 和 history。事务内部不调用会自行打开新连接的旧 service/repository 方法。

## 稳定业务错误码

| 错误码 | 含义 |
| --- | --- |
| `TOKEN_NOT_FOUND` | Token 不存在 |
| `TOKEN_REVIEW_MISMATCH` | Token 与请求的 review 不匹配 |
| `TOKEN_EXPIRED` | Token 已过期 |
| `TOKEN_REVOKED` | Token 已撤销 |
| `TOKEN_ALREADY_USED` | Token 已消费 |
| `REVIEW_NOT_FOUND` | review 不存在 |
| `SOURCE_TASK_NOT_FOUND` | review 关联的源 task 不存在或未提供 |
| `REVIEW_ALREADY_RESOLVED` | review 已被处理或被并发请求先处理 |
| `ACTION_NOT_ALLOWED` | 动作不在 Token 的 `allowed_actions` 中 |
| `ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE` | 动作不属于 Mobile Review 支持集合 |
| `INVALID_ADJUSTMENT` | `adjusted` payload 不符合调整字段约束 |
| `CONCURRENT_UPDATE` | 条件更新失败或 SQLite 写锁竞争 |

错误码通过 `MobileReviewTransactionError.code` 暴露；Web 页面仍使用现有统一失效提示，避免向移动端泄露内部数据库细节。

SQLite 并发错误只依据 `sqlite_errorcode` 和 `sqlite_errorname` 判定
`SQLITE_BUSY*` / `SQLITE_LOCKED*`，不依赖本地化异常文本。其他 `OperationalError`
继续原样抛出。Web POST 会把业务错误映射为 HTTP 状态：Token 失效类为 `403` 或
`410`，已处理/并发为 `409`，动作不允许为 `403`，动作类型或调整 payload 无效为
`422`；响应正文继续使用统一提示。

## adjusted payload 与源任务

`adjusted` 必须携带 `adjustment` 对象，只允许 `target_price`、`target_status` 和
`result_message`。其中至少需要调整价格或状态；价格会规范化为非负十进制字符串，文本
字段会去除首尾空白并限制长度。规范化后的 payload 写入 review，同时在同一事务内更新源
task 的目标字段和 `decision_trace.mobile_review_adjustment`，并将源 task 保持/推进为
`pending`，使后续流程可以继续执行。状态历史和这些字段更新与 Token 消费、review
resolution 同时提交。

## 调用链变化

旧链路：

`validate_token` → `resolve_review_task`（独立连接）→ `record_resolve_usage`（独立连接）

新链路：

`解析/HMAC/payload 校验` → `resolve_mobile_review_atomic`（单连接、单事务）

详情页 GET 仍只记录 `last_used_at`；后台 Web Session 的非 Mobile Review 复核流程保持原有 service 边界。

## 验收测试

测试文件：`tests/test_mobile_review_atomic_transaction.py`

- 四种动作：`approved`、`rejected`、`adjusted`、`cancelled`。
- 两个独立 `sqlite3.Connection` 竞争同一 Token，只允许一个成功。
- 两个不同 Token 竞争同一 review，只允许一个 resolution 成功。
- Token 更新后、review 更新后、task 更新后、history 插入前/后故障注入，均证明全量回滚。
- 结果对象转换前故障注入，证明提交前转换失败也会全量回滚。
- `adjusted` payload 规范化、源 task 字段更新、非法调整拒绝，以及 Web 层 `403/409/410/422` 状态映射。
- 关联源 task 缺失、源 task 状态不兼容和完全未知 action 均在消费 Token 前拒绝，返回稳定错误码且不产生业务状态变化。
- 使用不同错误文本但相同 SQLite busy/locked 错误码的分类，以及“文本含 locked 但错误码非并发”的反例。
- 重复提交返回 `TOKEN_ALREADY_USED`，不会新增 history。

本任务不扩大为全库 WAL、`busy_timeout` 或通用退避策略；这些仍属于任务 8。
