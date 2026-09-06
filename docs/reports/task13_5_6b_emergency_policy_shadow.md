# 任务 13.5-6B 极简紧急策略与影子判定实施报告

## 1. 当前状态

本阶段已完成本地实现。边界固定为 Runtime Schema v16 的一张极简
`emergency_offline_policies` 表，以及不创建任务、不发布队列、不产生平台副作用的
shadow/dry-run 判定。

`automatic_emergency_offline=false` 保持不变。`SYSTEM_EMERGENCY` 专用授权、v5 发布、
平台点击前撤销重验和受控真实下架均属于 13.5-6C，不在本阶段实现。

## 2. 编码前复用矩阵

| 能力 | 分类 | 处理 |
| --- | --- | --- |
| Runtime SQLite 连接、`BEGIN IMMEDIATE`、迁移版本和健康检查 | 原样复用 | 继续使用 `SQLiteRuntimeRepository` 与 `runtime_schema`，只追加 v16 表、索引、触发器和检查 |
| v15 Incident 主行、事件链和 `MASTER_DATA / PRICE_ANOMALY` 类别 | 原样复用 | 不增加 Incident 表、状态或类别；缺失/非法成本通过既有 `IncidentManagementService` fail closed |
| S4 Review、初始 Outbox `sent_at`、下一完整 `ONLINE_PULSE` 资格 | 原样复用 | 继续调用 6A-1 的 `IncidentReviewService.evaluate_online_pulse_eligibility`，不复制日期、计划槽或扫描查询 |
| 商品观察的 `VERIFIED` 映射、完整批次、在线状态和可读价格门禁 | 原样复用 | 使用既有 Pulse 资格查询；不新建页面读取或扫描体系 |
| Decimal 金额规范和 JSON canonical hash 习惯 | 参数化复用 | 新的纯计算解释器使用 `Decimal`，策略 canonical hash 使用既有稳定 JSON 规则 |
| 版本化不可变记录模式 | 抽取并沿用 | 参照 `operational_time_policies` 的版本替代、禁止删除和健康检查方式，缩减为单平台极简策略语义 |
| `emergency_offline_policies` 持久化 | 确需新增 | v16 复杂度预算明确允许的唯一新表；固定 `emergency_ratio=0.80`，不保存自由表达式或成本副本 |
| 价格分级与 shadow 判定解释器 | 确需新增 | 纯计算服务；shadow 与未来 6C 授权共用同一判定结果，当前调用方只能读取结果 |

### 2.1 明确不重新开发

- 不增加第二套 Automation、Review、通知、Watchdog、Worker、写锁、Importer 或
  RECONCILE。
- 不新建通用策略引擎、条件 JSON、账号范围、优先级、冷却、每日次数上限或自动重新上架。
- 不为 shadow 创建普通任务、`SYSTEM_EMERGENCY` 任务、Automation 授权事件或
  ShadowBot 队列文件。
- 不重新实现 18:00 交易日、下一计划槽、完整 Pulse、映射或在线状态判断。

## 3. 冻结的 6B 输出语义

纯解释器必须区分：

- 价格风险等级：`S1 / S2 / S3 / S4`；
- 是否属于唯一自动保护 allowlist：只有 `S4` 极端低价为真；
- 当前事实是否满足业务资格；
- 是否仅被生产功能开关阻断；
- 全部 fail-closed 原因；
- 策略版本、canonical hash、成本、阈值、第二次观察价格和所复用 Pulse 证据。

shadow 结果即使显示“除功能开关外已满足”，也不得宣称已授权，不得生成任务或平台动作。

## 4. 实现结果

- Runtime Schema 最新版本提升为 v16，只新增 `emergency_offline_policies`；数据库以
  `CHECK (emergency_ratio = '0.80')` 固定比例，并保证同平台最多一个已批准、未退休版本。
- 策略先创建为未批准草稿；批准后业务字段不可原地修改，只允许一次退休。替换在同一
  `BEGIN IMMEDIATE` 中退休当前版本并批准同平台后继版本；禁止删除历史版本。
- `EmergencyOfflinePolicyInterpreter` 使用 Decimal 精确计算，边界固定为：成本及以上的
  异常首次 S1、重复 S2，`0.80C < P < C` 为 S3，`P <= 0.80C` 为 S4；只有 S4 进入
  自动保护 allowlist。
- `EmergencyOfflineShadowService` 原样调用 6A-1 的完整 Pulse 资格入口，并从权威商品
  工作簿执行“读取前字节 → 解析 → 读取后字节”的一致性读取。成本来源只保存文件名和
  内容 SHA-256，不信任调用方直接提供的成本。
- 商品不存在、工作簿不可用/读取中变化、成本缺失/非法或来源不可追溯时 fail closed，
  并复用 v15 `MASTER_DATA` Incident 与 append-only Event；不增加表或状态。
- 影子判定读取既有同平台/SKU 共享写锁，并区分 `ACTIVE / UNKNOWN / REVIEW_BLOCKED`；
  Review、下一完整 Pulse、映射、在线状态和价格可读性继续由既有 6A-1 查询给出。
- 6B 应用服务将功能开关固定视为关闭。即使其余条件全部满足，结果也只显示
  `eligible_without_feature_flag=true` 和 `FEATURE_FLAG_DISABLED`，
  `authorization_eligible=false`。

## 5. 零副作用证明

专项测试在判定前后回读：

- `tasks = 0`；
- `automation_run_events = 0`；
- `shadowbot_operations = 0`。

本阶段没有 `SYSTEM_EMERGENCY` Repository 入口、没有队列发布、没有 v5 请求，也没有
真实平台动作。未来 6C 必须复用同一解释器，但需要另外完成专用授权事务、人工竞态和
受控真实下架门禁。

## 6. 当前验证

- v16 Schema、策略 Repository 和影子解释器专项：`33 passed`；
- v14→v16、v15→v16、Incident、Review、Automation 与 6B 受影响回归：
  `107 passed`；
- 完整 pytest：`1064 passed, 3 skipped, 97 subtests passed`；
- 隔离系统冒烟：`16 passed, 0 failed`，新库精确匹配 v16；
- 本次目标文件 ruff `E9/F/I`、`git diff --check` 和 UTF-8 编码回读：通过。

仓库全量 ruff 仍存在本任务开始前的历史基线问题，因此本阶段只对目标文件执行既定
`E9/F/I` 门禁，没有批量格式化或修改无关模块。
