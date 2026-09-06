# 任务 13.5-7E 控制面合同与复用矩阵

更新时间：2026-08-13
Review Profile：R3；真实平台执行仍沿用既有 R4 门禁

## 1. 范围与不变量

7E 只补齐运营控制面，不新增平台动作、执行链、写锁、审批状态或 Runtime 表。正式动作仍只有
`UPDATE_PRICE`、`SET_ONLINE`、`SET_OFFLINE`；`SYNC_STATUS` 只用于既有只读对账链。

- Web 人工创建只写 Runtime Task；创建成功不写 Queue、不启动 Worker、不触碰平台。
- 真实执行必须经过 `prepare_execution` 与 `submit_execution` 两阶段，并要求认证主体具有
  `SUBMIT_EXECUTION`。
- 执行只接受明确且不重复的 Task ID；禁止“执行全部 pending”。
- Route 只负责认证、CSRF、解析、Application Service 和 PRG；不得直接拼 Queue、调用 Runner
  或 publisher。
- Web GET 不初始化或迁移数据库。7E 的所有写入口只接受启动时固定的 Runtime DB、主数据和
  Queue 路径。
- 本任务不修改真实 Runtime DB，不投递真实 Queue，不启动 Worker，也不执行真实平台写动作。

## 2. 编码前复用矩阵

| 能力 | 分类 | 7E 处理 |
| --- | --- | --- |
| Runtime Task、来源、状态、dedupe 和批量事务 | 原样复用 | 使用 `Task`、`RuntimeTaskService` 的规范化语义和 `SQLiteRuntimeRepository.insert_tasks` 原子写入；不建候选任务表 |
| 商品主数据 | 原样复用 | 使用 `load_products()`；`product_name` 作为当前品种维度，`grade` 作为等级维度 |
| 平台商品映射 | 原样复用 | 使用 `compile_product_mapping_workbook()` 及其 `VERIFIED/UNMAPPED/AMBIGUOUS/DISABLED` 语义 |
| 当前平台价格/状态 | 参数化复用 | 从既有 `listing_status` 权威投影按平台、品种、等级唯一读取，并绑定观察时间与来源引用 |
| 数据库真实库存 | 原样复用 | 使用 v17 `InventoryRepository`/真实库存余额；上架平台目标库存不得超过真实库存 |
| 改价执行 | 原样复用 | 继续使用 v4 `prepare_task_commit_batch()` 与 `publish_task_commit_batch()` |
| 上下架执行 | 原样复用 | 继续使用 v5 `propose_listing_action_batch()` 与 `publish_listing_action_batch()` |
| 优先级、Review、Automation UI 租约、共享写锁、UNKNOWN/RECONCILE | 原样复用 | 由 v4/v5 预检和发布事务再次检查；7E 不复制状态机 |
| 人工任务范围展开与预览 | 确需新增 | 新增薄 Application Service，将品种/等级/平台多选展开为结构化逐项预览 |
| 统一执行授权 | 抽取公共能力 | 新增薄 Application Service，仅编排 v4/v5；digest 绑定精确 Task、任务版本和最新事实 |
| Web/Mobile Review 处置 | 原样复用 | 使用既有 `resolve_mobile_review_atomic()` 事务与错误码；桌面和手机共享同一处置服务 |
| Automation Job/Run/Event、租约、Scheduler | 原样复用 | 使用现有 `AutomationRepository`、Job/Run 账本和 Handler；不建设 Web 内调度循环 |
| Automation 配置版本切换 | 确需新增 | 同一排程版本不可变；变更排程时创建确定性新版 Job，并在同一事务停用旧版 |
| 库存预警 | 原样复用 | 使用 v17 `InventoryAlertService` 和策略表；只改 allowlist 字段，不创建平台动作 |

新增实现若与上表冲突，必须先补充不可参数化证据；不得长期保留同职责平行链。

## 3. 人工任务合同

### 3.1 输入

结构化输入只允许：

- `varieties[]`：至少一个品种；
- `grades[]`：至少一个等级；
- `platforms[]`：至少一个平台；
- `action`：`SET_PRICE`、`CHANGE_PRICE`、`SET_OFFLINE`、`SET_ONLINE`；
- `price_value`：`SET_PRICE` 的绝对价格或 `CHANGE_PRICE` 的有符号差额；
- `target_inventory`：只允许 `SET_ONLINE`，为非负整数；
- `excluded_item_keys[]`：仅排除本次预览中的明确项目；
- `idempotency_key`：一次创建请求的稳定键。

不接受任意 Task JSON、Task 状态、priority、origin、actor、数据库路径、工作簿路径或 Queue
路径。操作者只从认证 principal 取得。

### 3.2 预览和创建

服务端按品种 × 等级 × 平台展开有效商品，并逐项给出：SKU、平台、当前价格/状态、真实库存、
动作、目标价格/平台库存、基础成本、映射状态、价格新鲜度和阻断原因。

- `SET_PRICE`：目标价格等于输入值；
- `CHANGE_PRICE`：目标价格等于当前价格加有符号差额，负数代表降价；
- `SET_OFFLINE`：不携带价格或库存；
- `SET_ONLINE`：必须同时携带目标价格和平台目标库存。

目标价格必须为正且不低于商品 `base_cost`；`SET_ONLINE.target_inventory` 不得超过数据库真实
库存。映射不唯一、价格不可用或过期、平台状态不可用、存在同身份开放任务、真实库存不可用
均阻断该项。创建时重新生成预览并比较 `preview_digest`；任一事实变化时整批拒绝，不能部分
创建。合法项目通过单一数据库事务写入 MANUAL Task，`origin_ref_id` 和 `dedupe_key` 绑定创建
请求；精确重放返回同一批 Task，同键异内容拒绝。

## 4. 执行授权合同

```text
prepare_execution(principal, exact_task_ids, idempotency_key)
  -> capability + latest-fact revalidation
  -> v4 prepare 或 v5 propose
  -> confirmation_digest + exact manifest + expires_at

submit_execution(principal, exact_task_ids, confirmation_digest, idempotency_key)
  -> capability + identity + digest + latest-fact revalidation
  -> existing v4/v5 publisher
```

- 一个授权批次只能包含同平台、同动作族任务；改价走 v4，上架或下架分别走 v5。
- `confirmation_digest` 绑定排序后的 Task ID、动作/目标值、Task `updated_at`、映射版本、当前
  平台事实、基础成本、真实库存版本及准备批次 ID，并有短期有效期。
- 准备数据保存在既有 v4 PREPARED 批次或由 v5 proposal 的确定性摘要重建；7E 不新增授权表。
- 提交必须使用同一 principal 与同一幂等键；表单 `actor`/`confirmed_by` 一律忽略。
- Task、价格、映射、成本、真实库存、Review、优先级、写锁、Automation UI 租约或
  UNKNOWN/RECONCILE 任一变化，整批拒绝并要求重新准备。
- publisher 的 `confirmed_by` 只能是认证 principal；Queue/Importer 失败、UNKNOWN 和唯一
  RECONCILE 沿用既有恢复链。

## 5. Review 合同

- 桌面入口要求 `HANDLE_REVIEW`，手机入口要求有效、未过期且未使用的 Review Token。
- 两个入口均调用既有原子处置事务；Review、Token、源 Task、必要的新 Task 和 Outbox 必须
  同事务成功或失败。
- 重复点击、过期、错绑、非法动作和并发更新使用既有稳定错误码并给中文反馈。
- 人工复核结果优先于尚未取得最终点击栅栏的紧急动作；7E 不新增第二种竞态判定。

## 6. Automation 配置合同

只允许施工计划 6.4 的固定方案与字段范围。`LISTING_STATUS_SCAN`、`ORDER_SCAN` 是
`CHILD_ONLY`，不得独立启停或配置排程。18:00/20:00 相关时间只从当前
`OperationalTimePolicy` 派生。

时间策略换版本不属于 Web 配置。管理员必须先建立并校验 SQLite 逻辑备份，再使用唯一协调
维护入口，在同一事务内替换 Policy 与五个相关定时 Job successor；旧 Job 的启停状态、销售
计划输入偏移、每日任务后置偏移和来源 allowlist 必须保留。任一步失败整体回滚，且不允许未来
生效时间导致新 Job 提前运行。

排程身份继续冻结为 `job_id + schedule`。仅切换启用状态或不影响排程的 allowlist 配置时可
更新当前 Job；频率或 offset 改变时，必须用规范化配置 SHA-256 生成确定性新 `job_id`，在
同一事务写入新版并停用旧版。Scheduler 只读取启用版本。

受控补跑只允许已有 READ_ONLY 或幂等业务 Handler；请求必须明确 Job、目标交易日和幂等键，
写入既有 Automation Run 后由独立 Automation Service 执行。Web 不持有租约、不运行循环，
也不接受 Cron、脚本名、路径或任意参数。

## 7. CLI 正式归宿

| 入口/能力 | 正式归宿 | CLI 结论 |
| --- | --- | --- |
| `preview-tasks` | Web 人工任务预览 | 仅测试 |
| `generate-runtime-tasks` | Automation + Web 受控补跑 | 测试/管理员恢复 |
| `list-tasks`、`show-task-history` | Web 数据库/业务管理 | 保留诊断 |
| `list-review-tasks`、`resolve-review-task` | Web/Mobile Review | 仅隔离测试/恢复 |
| `expire-review-tasks --apply` | Review 超时 Automation Handler | 仅管理员修复 |
| `notification-worker` | 独立通知服务 | 保留启动/诊断 |
| `serve-web` | 新运营 Web | 保留启动 |
| `init-runtime-db`、`health` | 显式维护/系统状态 | 保留管理员 CLI |
| `templates`、`validate`、`import-data` | 数据维护 | 保留 |
| `generate-tasks` | 已替代的 Excel 候选链 | 日常使用明确拒绝，历史入口后续 7F 删除 |
| `mock-ai-decision`、`simulate-execution` | 测试 | 隔离保留 |
| `list-manual-tasks`、`resolve-manual-task` | 已替代的旧人工链 | 日常使用明确拒绝，后续 7F 删除 |
| `start_shadowbot_reconcile` | 唯一 RECONCILE 恢复服务 | CLI 仅管理员恢复/验收 |
| `confirm_shadowbot_manual_handled` | Review/Incident 正式人工处置 | CLI 仅管理员恢复/验收 |

`evaluate_business_rules.py` 单独归类：`listing_rules` 迁入每日任务生成的 allowlist；
`capacity_warning` 与 `cold_storage` 属包装/冷库 ERP，延期且只保留诊断；`platform_sync` 是 Mock
平台实验能力，只保留隔离测试。不得以“每日任务生成”名义把后三者接入生产 Automation。

## 8. 验收边界

专项测试必须覆盖：多选展开、四类动作、预览后事实漂移、排除项、低于成本、库存不足、映射
异常、开放任务冲突、精确重放/同键异内容、创建零 Queue 副作用；授权 capability、伪造 actor、
换批/replay、精确 Task、v4/v5 复用、Queue 失败；桌面/手机 Review 原子性；Automation allowlist、
边界值、版本替换、child 禁配、时间策略派生、Policy 与五个 Job 原子换版/失败回滚及受控补跑。
每日规则 Task 还必须回读 `platform_trade_date`、`seller_operation_date` 和
`time_policy_version`。Ready for review 前再运行受影响集成、
完整 pytest、系统冒烟和 Windows/Linux CI；真实平台写验收必须另获用户明确批次授权。
