# PRA 当前实现责任图

角色：Current Implementation Evidence。生产代码基线：`08041bfe25a7f31f032564a2abca35e5eb5f5330`（PR #45 合并 main）。本 PR 仅修改文档，未部署、未验证真实 Runtime。原 G2 核对结论与增量见[原报告](../reports/task13_6_2_g2_architecture_handoff_review_20260906.md)及[增量报告](../reports/task13_6_2_g2_incremental_parallel_absorption_review_20260906.md)。

本文回答代码实际怎样工作；[业务合同](../business_contract.md)回答应该怎样经营；[目标职责](task13_6_target_responsibility_and_gap_matrix.md)回答下一步由谁承担。

## 1. 正式入口与现有调用链

| 入口/宿主 | 现有职责 | 当前边界 |
|---|---|---|
| `app/operations_web/app.py` 与 `composition.py` | 认证、固定依赖组合、人工业务入口、只读经营页面 | Web request 不承担长期执行推进 |
| `/management/tasks/preview`、`/management/tasks/create` | ManualTaskApplicationService 展开范围、重验、原子保存 Task | 创建不生成 ShadowBot 请求、不写 Queue、不启动 Worker |
| `/management/executions/prepare`、`/management/executions/submit` | ExecutionAuthorizationApplicationService：精确主体/Task 集合、digest、最新事实重验、v4/v5 publish | 已有正式人工执行授权；尚未形成统一业务 continuation owner |
| `scripts/run_automation_service.py` | Job/Run/Event、lease、定时读取、旧 Settlement/Plan、可选 Incident/恢复维护 | 正式组合不注册普通销售写 handler；新 provider-centric Health 尚未实现 |
| `scripts/run_shadowbot_queue_services.py` | Importer、Watchdog、登录/验证码监视、Review reminder、Outbox、heartbeat | 是目标 Coordinator 的优先宿主，当前循环尚未装配该业务组件 |
| ShadowBot Worker | 既有 v4 UPDATE_PRICE、v5 SET_ONLINE/SET_OFFLINE、写前核对/写后回读、phase/evidence | 实际 UI 执行边界；不负责上层经营决策 |

当前 Web 主入口为 `/today`、`/database`、`/management`、`/system`；Quality 位于 `/database/quality`（具体子路由以源码为准）。旧 Dashboard/Business Inputs 文档不能作为现行导航。

## 2. 人工动作：哪些箭头已经存在

人工 create → Runtime Task；人工 prepare/submit → 授权审计 → v4/v5 publisher → batch/operation/attempt → 文件 Queue → Worker → result → Importer → operation/attempt/Task 投影。Watchdog 处理超时；UNKNOWN 通过既有唯一 RECONCILE 收口。

这证明相关入口和组件已存在，不证明新 one-shot Intent、supersession 和跨重启业务持续 owner 已完整实现。

待人工授权的 PENDING Task 不应由后台擅自执行；目标责任是 Human + Operations Web，允许明确 submit/supersede/cancel/expire。问题在于不能把“已保存 Task”或“页面可见”当成已完成授权后的持续恢复责任。

### 最终确认窗口

`execution_authorization.py` 的 `_preparations`、`_idempotency` 在 Web 内存。未提交 preparation 可在重启后失效并重新确认。当前 submit 会先 `_record_authorization_audit()`，再调用 v4/v5 publish；最终确认后、可靠执行账本/Queue 交接前的崩溃窗口是 IG-08 gap。不能仅靠审计记录推断已发布或未发布，也不能盲重发。

### 已有业务冲突

- `manual_task_orchestration.py` 查询同 SKU+平台的 pending/running/manual_review Task 并阻断新建；目标要求先记录有效新 Intent。
- Manual preview 与 Execution Authorization 均仍有 `target_inventory > balance.current_qty` 拒绝；这是旧 Exposure/实物混淆，13.7 需调整两处，同时保留其他执行校验。
- Queue Service 对多个组件已有局部 try/except，但不据此宣称所有组件/异常均隔离。新增 Coordinator 需满足自身及单 attempt 故障隔离，不扩大宿主故障面。

## 3. 经营事实与数据接线

| 能力 | 当前生产事实 | 与新业务的差距 |
|---|---|---|
| Light listing observation | 已有 ProductObservation、Pulse、Mapping、只读导入 | 可复用；不等价于统一 Current Sales Commitment |
| Order Observation | 已有当前/历史订单读取、日期与 scope、tail/空页、hash、多重集合、导入 | rollover 后 direct provider 的复用资产；冻结期 CurrentTradeDaySalesObservation 尚无正式实现 |
| 订单研究字段 | `order_created_at`、qty、amount 已持久化；页面单价通过 Decimal×数量得 amount | `purchase_sequence` 未在正式合同采集/持久化；其名字出现在 retired provisional columns 不代表已实现；`occurrence_no` 不是复购次数 |
| 时间 | `operational_time.py`/policy 仍保留 18:00 platform day + 20:00 seller day | 20:00 第二销售日界已经被业务裁决替代；代码尚未 cutover |
| Settlement/Summary | 旧 20:00 Settlement、PROVISIONAL→OBSERVED→RECONCILED→FINAL、late revision、Plan/DailyTask 接线仍在 | 不是新 Daily Closing；普通订单导入与旧结算/计划/库存接线需要明确退役 |
| Web `today()` | `OperationalSummaryRepository` 当前 PLATFORM Summary 提供今日已售/金额/均价 | 新 Current Operating State 生效时必须同 gate 切读，不能只新增后台事实 |
| DB Real Inventory | authority、balance、append-only transaction、bootstrap、盘点/损耗/更正、sales baseline 已有 | 保留唯一实物账本；销售净差应用仍绑定旧 Summary，需在 no-double-count 契约后切换 |
| Supply | HarvestForecast/工作簿输入已有 | Forecast→Harvest→Packaged + 独立 Carryover 的统一事实/选择责任尚缺 |
| Incident/Review/Outbox | persistence、dedupe、event、恢复、手机复核和通知已有 | 现有 emergency_protection S3/S4 处置属于价格保护；不能当作 Observation S4 授权 |

## 4. 13.7 接手的差距

目标矩阵中逐项维护 REUSE/ADAPT/MISSING/RETIRE/DEFER。主要缺口是业务责任重接：one-shot Intent、已授权持久 continuation、Exposure 解耦、Commitment/provider、Closing、Supply、Observation Health、旧 authority 与 Web 读模型 cutover。不是重写 v4/v5、Queue、Worker、Importer 或实物库存底座。

## 5. 验证证据的适用范围

本次 main 的 [Core CI](https://github.com/etereath/PRA-project/actions/runs/34019822157) 及 PR #45 head [Core CI](https://github.com/etereath/PRA-project/actions/runs/33995743603) 均为 Windows/Linux success。源码测试明确涵盖人工创建无 Queue 副作用、旧库存上限、精确主体/digest 和 v4/v5 调用；部分发布测试使用替身，不能据此宣称完整真实 journey 已通过。

Task12/13 的 READ_ONLY、受控 COMMIT、UNKNOWN/RECONCILE 历史证据只证明其绑定 SHA/平台/范围。仓库合并不证明用户现场部署已更新。本 PR 没有启动真实服务或执行实机操作。

主要源码：[Manual Task](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/manual_task_orchestration.py)、[Authorization](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/execution_authorization.py)、[Queue Service](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/scripts/run_shadowbot_queue_services.py)、[Web 查询](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/operations_web/queries.py)、[Runtime Schema](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/runtime_schema.py)、[Time](https://github.com/etereath/PRA-project/blob/08041bfe25a7f31f032564a2abca35e5eb5f5330/app/services/operational_time.py)。
