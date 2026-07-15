# Mobile Review 单事务实现说明

## 事务边界

Mobile Review 的 POST resolve 入口现在只在事务前执行无副作用的动作解析、Token HMAC 计算和请求 payload 格式校验。最终状态判断与写入由 `SQLiteRuntimeRepository.resolve_mobile_review_atomic(...)` 完成：

1. 通过同一 SQLite 连接执行 `BEGIN IMMEDIATE`。
2. 在该连接内重新读取 `review_tokens`、`review_tasks` 和关联 `tasks`。
3. 校验 Token 绑定关系、未使用、未撤销、未过期、动作许可和 review 仍为 `pending`。
4. 用条件 `UPDATE` 消费 Token，并要求 `rowcount = 1`。
5. 用条件 `UPDATE ... WHERE review_status = 'pending'` 写入 review resolution。
6. 用条件 `UPDATE ... WHERE task_status = 当前状态` 推进源 task，并要求 `rowcount = 1`。
7. 在同一连接写入 `task_status_history`，然后统一提交。

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
| `REVIEW_ALREADY_RESOLVED` | review 已被处理或被并发请求先处理 |
| `ACTION_NOT_ALLOWED` | 动作不在 Token 的 `allowed_actions` 中 |
| `ACTION_NOT_ALLOWED_FOR_REVIEW_TYPE` | 动作不属于 Mobile Review 支持集合 |
| `CONCURRENT_UPDATE` | 条件更新失败或 SQLite 写锁竞争 |

错误码通过 `MobileReviewTransactionError.code` 暴露；Web 页面仍使用现有统一失效提示，避免向移动端泄露内部数据库细节。

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
- 重复提交返回 `TOKEN_ALREADY_USED`，不会新增 history。

本任务不扩大为全库 WAL、`busy_timeout` 或通用退避策略；这些仍属于任务 8。
