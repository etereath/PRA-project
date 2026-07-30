# 任务 13.5：单平台运营自动化闭环与 Web 主控重写实施计划

- 计划日期：2026-07-28；按父 Issue 合并正文修订于 2026-07-29
- 插入位置：任务 13 完成后、任务 14 开始前
- 当前状态：13.5-0 已通过 PR #21 合并；13.5-1
  [双时间轴、质量与日结合同](task13_5_1_quality_and_settlement_contract_review.md)
  已冻结并完成本地 v14 实现与验收；13.5-2 已通过 PR #23 合并；13.5-3 独立
  调度控制面见
  [Automation Service 合同](task13_5_3_automation_service_contract.md)。
  真实 Runtime DB 尚未迁移，真实扫描 handler 尚未部署
- 适用对象：PRA Web 主控端、SQLite 运行态、自动化服务、ShadowBot 单平台执行端
- 宏观权威：[GitHub Issue #20](https://github.com/etereath/PRA-project/issues/20)
- 对齐评估：[Issue #20 与本地实施计划对齐评估](task13_5_issue20_alignment_review.md)
- 核心原则：Issue #20 合并截至 2026-07-29 已采纳评论后的正文决定业务语义和阶段
  边界；历史评论仅用于追溯。本计划负责本仓库中的模块、迁移、测试和 Web 落地
- 开工基线：[13.5-0 基线冻结与正式开工清单](task13_5_0_kickoff_baseline.md)
- Web 审计：[Web 现状独立审计快照](task13_5_web_current_state_audit_20260729.md)

## 1. 任务背景

任务 12 和任务 13 已经完成单平台多商品改价、上下架、状态扫描、结果导入、
UNKNOWN 后唯一 RECONCILE、写锁和审计证据等核心动作闭环。但是，这些任务的目标
主要是证明“动作可以安全、可审查地执行”，没有完整解决日常运营问题：

- 一些能力通过独立脚本启动，缺少统一调度、运行状态、失败恢复和 Web 运维入口。
- 为完成实机验收而临时补充的流程已经可用，但尚未统一成正式模块和稳定数据合同。
- 商品扫描没有形成小扫描、完整扫描和交易日截单日结的自动运行制度。
- 订单管理页面尚未纳入只读采集和销售事实沉淀，不能支持可靠的销售预测。
- 自动规则产生的任务与运营人员人工创建的任务，在页面和运行责任上没有充分隔离。
- 异常分散在任务、Review、Outbox、执行日志、队列文件和页面异常表中，运营人员缺少
  一个统一的异常处置入口。
- 当前 Web 页面沿开发过程持续叠加，信息架构、文案和操作路径偏开发者视角，不能满足
  日常运营和故障值守需要。

因此，任务 13.5 要把已有执行能力、主控端、数据库和运营界面整理成可持续运行的
单平台业务闭环。普通真实写动作继续复用任务 12/13 的显式任务、授权、写锁和恢复链路；
唯一新增的无人值守写动作是满足版本化 S4 策略、完整二次观察和禁止条件检查后的
`SYSTEM_EMERGENCY` 紧急下架。任务 14 不再补做控制面，而是负责综合验收、正式授权和
观察版本冻结。

## 2. 目标和完成定义

任务 13.5 完成后，系统应具备以下能力：

1. 定时执行只读小扫描、完整扫描和交易日截单日结，且每次运行均可追踪、可重试、
   可审计。
2. 将商品状态、订单只读事实、销售汇总、异常和调度运行状态统一投影到 SQLite。
3. 自动脚本任务与人工任务在来源、队列、页面、权限边界和指标上明确分离。
4. 自动扫描和规则评估可以生成事实、建议、Review 或候选任务；调度器不得扫描全部
   `pending` 并发布 COMMIT。只有通过版本化 S4 策略的 `SYSTEM_EMERGENCY` 可以在
   二次观察后创建明确授权并复用 v5 执行紧急下架。
5. Web 主控端按运营工作流重写，运营人员无需理解内部合同、phase、hash 或脚本名称
   也能完成日常操作；技术细节保留在高级诊断页。
6. 异常具备统一分类、严重度、责任人、处置状态、去重指纹、证据和恢复记录。
7. 日结数据能够支持后续按品种、等级、时段、价格和库存约束进行销售预测。
8. 具备进入后续观察所需的最小运行安全：单实例保护、错过任务补偿、运行超时、告警、
   证据保留和可验证备份；完整长期运维收口留给任务 17。

任务 13.5 的“完成”必须同时满足代码回归、Web 验收、数据库迁移验证、自动调度观察、
只读实机证据和运维恢复演练。任何一项不能用另一项替代。

## 3. 范围边界

### 3.1 本任务包含

- 蚂蚁花团供应商微信小程序的定时只读扫描编排。
- “上架中”“待上架”和订单管理页面的只读采集。
- 每 10 分钟 `ONLINE_PULSE`、每小时 `FULL_MARKET_SCAN`、18:00 边界扫描和 20:00 日结。
- 平台交易日和卖家作业日双时间轴，以及跨边界逐条观察归属。
- 自动化服务、调度记录、运行租约、幂等、补偿和失败恢复。
- 自动任务与人工任务的来源和执行通道分离。
- 统一异常中心和运营告警。
- 销售事实、日结指标和预测特征准备。
- Web 信息架构、运营文案、页面布局和交互流程重写。
- 任务 12/13 现有脚本入口向正式服务入口的收口和兼容。
- 进入任务 14/15 验收所需的日志、证据、最小备份、清理保护和操作说明。

### 3.2 本任务不包含

- 第二平台适配或跨平台混合批次。
- 24 小时/7 天正式观察验收；由任务 15 承接。
- 第二平台或预测影子运行；由任务 16 承接。
- 完整运维看板、长期备份/保留策略、正式 Runbook 和项目收口；由任务 17 承接。
- AI 自动决策、AI 自动定价或普通业务任务自动审批。
- 除通过版本化 S4 紧急策略的 `SYSTEM_EMERGENCY` 外，无人值守发布改价、上架或下架。
- 修改订单、确认发货、退款、支付、资金对账等订单写操作。
- 客户姓名、电话、地址、聊天内容等个人信息采集。
- 用订单页面的展示值直接替代财务结算数据。
- 迁移 Excel 主数据到 SQLite。
- 为了重写 Web 而重写任务 12/13 的动作合同、点击链路、写锁、Importer 或唯一
  RECONCILE。
- 第二套任务表、第二套执行账本或绕过现有 service/repository 的直接写库实现。
- 完整企业级权限系统；本任务只补充满足运营和管理员分工的最小权限边界。

### 3.3 与任务 14 的边界

任务 13.5 完成控制服务、订单与商品观察、销售估算、日结、异常分级、紧急保护、脚本
收口、任务来源对齐和 Web 产品化。任务 14 只负责：

- 多品种、多动作和异常恢复的综合验收。
- 普通写动作的正式授权与策略验收。
- `SYSTEM_EMERGENCY` 策略的安全验收。
- 观察版本冻结和交付判定。

在任务 14 完成并审查前：

- 定时只读扫描和历史订单页读取可以无人值守执行。
- 自动规则可以生成 proposal、Review 或候选任务。
- 普通真实写动作仍要求操作人员明确选择一个或多个 `task_id`。
- `pending` 只表示候选状态，不代表执行授权；调度器不得批量发布。
- S4 紧急下架必须逐项满足版本化策略、二次观察和禁止条件，创建
  `SYSTEM_EMERGENCY` 授权及单一 `SET_OFFLINE` 任务；不得自动重新上架。
- 副作用状态不明必须进入现有唯一 RECONCILE。

## 4. 目标架构

```text
双时间轴与版本化调度配置
        │
        ▼
Automation Scheduler ──► Automation Run Ledger
        │                         │
        ├── 小扫描（只读）        ├── 运行状态 / 心跳 / 租约 / 告警
        ├── 完整扫描（只读）      └── 步骤结果 / 重试 / 证据
        ├── 订单同步（只读）
        ├── 18:00 边界扫描 / 20:00 日结
        └── 规则评估
                 │
                 ▼
        SQLite 运行态与经营事实
                 │
      ┌──────────┼──────────┐
      ▼          ▼          ▼
  任务中心    异常中心    销售分析
  人工通道    统一处置    日结/预测特征
  自动通道
      │
      ▼
 任务 14 综合验收与观察版本冻结
      │
      ▼
已验收的 ShadowBot 写动作链路
```

### 4.1 服务职责

建议将运行职责拆为以下长期模块：

| 模块 | 职责 | 是否允许平台写操作 |
| --- | --- | --- |
| `automation-scheduler` | 交易日历、触发、单实例租约、错过任务补偿 | 否 |
| `scan-worker` | 小扫描、完整扫描、订单管理页面只读采集 | 否 |
| `rule-worker` | evaluator 的 dry-run/apply、proposal 和 Review 创建 | 否 |
| `notification-worker` | Outbox 投递和失败重试 | 否 |
| `shadowbot-queue-services` | 文件队列 Importer、Watchdog、唯一 RECONCILE | 仅复用既有边界 |
| `command-executor` | 接收明确授权后的普通写任务或 `SYSTEM_EMERGENCY` | 是，必须有明确任务和授权 |
| Web 主控端 | 配置、观察、人工处置和审计 | 不直接执行平台动作 |

Web 请求线程不得承担长期调度、循环扫描或 ShadowBot Worker 生命周期。CLI 保留为
排障和手工补跑入口，但日常业务不再依赖操作人员打开终端执行脚本。

## 5. 自动扫描与日结流程

### 5.1 交易日和时间配置

所有业务时间使用 `Asia/Shanghai`，并同时保存两个日期：

- 平台交易日：以 18:00 为边界，区间为前一自然日 18:00（含）至当前自然日
  18:00（不含）。本地时间小于 18:00 时归当前自然日，大于等于 18:00 时归下一日期。
- 卖家作业日：以 20:00 为边界，区间为前一自然日 20:00（含）至当前自然日
  20:00（不含）。
- `NORMAL_SALES`：20:00 至次日 16:00。
- `PEAK_SALES`：16:00 至 18:00。
- `DELIVERY_OVERLAP`：18:00 至 20:00。
- `SETTLEMENT`：20:00 后运行的后台作业，不是排他的全局运营阶段。

边界测试必须覆盖 17:59、18:00、19:59 和 20:00。跨边界扫描不能按批次起止时间整体
归属日期，必须按每条记录的 `observed_at` 计算平台交易日和卖家作业日。核心边界采用
版本化配置；修改必须保留操作者、策略版本、修改前后值、生效时间和回滚记录。

### 5.2 小扫描

`ONLINE_PULSE` 默认每 10 分钟执行一次，只扫描“上架中”商品，目标是快速发现：

- 实际价格变化。
- 平台库存变化。
- 已映射目标本轮未观察到、重复身份或页面读取不完整。
- 上次成功观察时间是否过旧。

未观察到商品只表示 `NOT_OBSERVED / UNKNOWN` 或扫描异常，不能推断为待上架、下架或不存在。

小扫描要求：

- 使用独立 `scan_profile=ONLINE_PULSE`，不能伪装成完整 `SYNC_STATUS`。
- 同一平台同一 profile 不允许并发重叠。
- 扫描结果必须带父快照、页面范围、起止时间、完整性和证据绑定。
- 小扫描只写入正向 `ONLINE` 观察；未出现商品不得改变完整 `listing_location`。
- 只有完整两页 `LISTING_STATUS_SCAN` 可以更新完整位置事实。
- 只在完整性门禁通过后更新本轮正向观察投影；失败快照保留，但不能覆盖最后一个可信快照。
- 连续无变化可以保存摘要和内容哈希，避免重复保存大体积相同证据。

### 5.3 完整扫描

`FULL_MARKET_SCAN` 是通用平台父作业，默认每小时执行一次：

```text
FULL_MARKET_SCAN
├─ LISTING_STATUS_SCAN
│  ├─ 上架中
│  └─ 待上架
└─ ORDER_SCAN
   └─ ORDER_HISTORY_IMPORT
```

Adapter 必须显式声明：

```text
supports_order_scan
supports_current_trade_day
supports_historical_trade_day
```

当前蚂蚁花团平台为 `true / false / true`。

完整扫描要求：

- 使用 `scan_profile=FULL_MARKET_SCAN`。
- `LISTING_STATUS_SCAN` 继续复用任务 13 正式 v5 `SYNC_STATUS` 的两页读取、身份匹配
  和异常语义。
- 订单页面采集必须使用独立合同和独立结果表，不能塞入商品状态结果。
- 商品和订单子结果由同一父级 `automation_run_id` 关联，但 request/result、完整性、
  结束标记、hash、Importer、错误码和数据质量均独立保存。
- 子结果使用 `UNSUPPORTED / UNAVAILABLE / FAILED`：分别表示平台明确不支持、平台
  支持但目标日期/范围当前不可用、能力应可用但本次运行失败；三者不得混写。
- 一个子结果失败不能使另一个已满足合同并被接受的事实失效。
- 页面结束标记、滚动轨迹、读取总数和去重结果必须可验证。
- 若订单页面存在分页或时间筛选，必须记录实际查询时间窗和平台返回范围。

### 5.4 边界扫描与日结

- 18:00 前执行 `PRE_CUTOFF_FULL_SCAN`，18:00 后执行 `POST_CUTOFF_PULSE`；
  用相邻观察证明交易日边界，跨界记录按 `observed_at` 单条归属。
- 20:00 执行结算作业，生成 `PROVISIONAL` 平台交易日汇总和下一销售计划输入。
- 历史订单观察到达后按
  `PROVISIONAL → OBSERVED → RECONCILED → FINAL` 单向推进；只有 `FINAL` 是正式
  终态。
- FINAL 后出现迟到数据或修正时，创建新的汇总版本并以
  `supersedes_summary_id` 指向旧版本，重新经过 OBSERVED、RECONCILED 和 FINAL；
  不删除或静默覆盖旧事实。
- 数据不完整时使用 `UNAVAILABLE` 或对应低质量等级并创建异常，不能输出“数据正常”。
- 结算批次只能选择最新、已接受且完整的订单观察批次；不同扫描批次不能累加为订单数。

## 6. 订单只读事实与销售预测数据

### 6.1 数据最小化

2026-07-31 首轮无副作用实测确认订单管理页可以读取当前交易日截至 `observed_at`
的开放快照，也可以读取相邻历史交易日。能力合同必须明确记录：

- `supports_order_scan=true`
- `supports_current_trade_day=true`
- `supports_historical_trade_day=true`

系统必须把 `supports_current_trade_day` 与 `OPEN / CLOSED` 或等价终态分开：
当前开放交易日结果只是截至观察时刻的快照，不能伪装成闭市完整订单或提前进入
`FINAL`；可信“暂无订单”空页不得写成 `UNAVAILABLE`。每次读取写入不可变的
`order_observation_batches` 和 `order_observation_items`。页面没有稳定订单 ID 或订单行
ID 时，使用规范化行指纹、`occurrence_no` 和 `occurrence_count` 表示重复行多重集：

- `source_row_fingerprint` 只用于候选分组和完整性校验，不是 canonical ID。
- `occurrence_no` 表示同一批次中每条相同指纹记录的实例序号。
- `occurrence_count` 表示同一批次按指纹分组的对账计数，可派生或固化。
- 指纹不能作为绝对唯一主键，不能建立会吞掉真实重复行的唯一索引。
- 跨批次只比较多重集合计数和内容差异，不把不同批次累加为销量。
- 结算选择最新、已接受的完整批次；历史批次只用于审计、差异分析和修订证据。

允许持久化的字段为：

- 平台、平台交易日、平台商品名称和等级。
- 内部 SKU 映射结果与映射状态；平台本身没有内部 SKU。
- 准确的 `order_created_at`。
- 下单数量、有效数量。
- 卖家实收金额；卖家实收单价只能由实收金额除以有效数量推导，不能称为买家成交价。
- 购买序号、`observed_at`、`source_batch_id`、完整性和质量。

取消量只有在无副作用 UI 探索证明稳定方法后才能推导，并保存方法、输入和置信度。
禁止持久化或声称采集平台订单 ID、订单行 ID、独立规格字段、商品累计销量、退款数量、
支付状态、支付/完成时间，以及客户姓名、手机号、地址、聊天记录、付款账号等个人信息。

### 6.2 日结核心指标

销售事实必须把来源、质量和日结生命周期作为三个正交维度：

```text
fact_source:
  ORDER_OBSERVED | SCAN_ESTIMATED

quality_level:
  ORDER_COMPLETE | ORDER_PARTIAL |
  SCAN_ESTIMATED_HIGH | SCAN_ESTIMATED_MEDIUM |
  SCAN_ESTIMATED_LOW | UNAVAILABLE

summary_status:
  PROVISIONAL -> OBSERVED -> RECONCILED -> FINAL
```

| `quality_level` | 进入条件 | 主要降级条件 | 日报 | 销售计划 | 规则输入 |
| --- | --- | --- | --- | --- | --- |
| `ORDER_COMPLETE` | 最新已接受批次覆盖目标交易日，字段、分页结束和映射满足合同 | 覆盖、字段、映射或分页不完整 | 正式订单事实 | 可以 | 只可分析或生成 proposal |
| `ORDER_PARTIAL` | 存在真实订单行，但覆盖、字段或映射有明确缺口 | 批次不可接受或关键字段不可读 | 单列部分事实 | 不进入正式计划 | 不允许 |
| `SCAN_ESTIMATED_HIGH` | 相邻完整观察、同一交易日、VERIFIED 映射、持续在线、库存可读且无已知调整 | 扫描间隔、价格区间或非关键支撑信息不确定 | 可用，必须标估算 | 可以 | 只可分析或生成 proposal |
| `SCAN_ESTIMATED_MEDIUM` | 数量仍可解释，但存在可界定的时间或价格不确定 | 未解释库存变化、关键扫描缺失或映射问题 | 单列 | 降权使用 | 不允许自动写 |
| `SCAN_ESTIMATED_LOW` | 只有方向性或宽区间估算 | 区间不再满足估算资格 | 低置信附注 | 不进入正式计划 | 不允许 |
| `UNAVAILABLE` | 无可接受订单事实且无合格扫描估算，或能力不可用 | — | 不伪造 0 | 不允许 | 不允许 |

任何质量等级都不能单独构成平台写授权。S4 依赖两次完整价格观察和独立版本化策略，
不能把销售质量枚举当作价格与成本门禁。

扫描估算使用：

```text
estimated_sold_qty =
  max(inventory_before - inventory_after - known_inventory_adjustment, 0)
```

每个估算区间至少保存：

```text
interval_started_at
interval_ended_at
inventory_before
inventory_after
known_inventory_adjustment
known_adjustment_source_refs
mapping_version
estimation_eligible
estimation_reason
confidence
supporting_observation_ids
```

人工修改库存、上架重设库存、已知 `target_inventory` 或其他库存写入、无法解释的库存
增加、期间离线、映射变化/不唯一、扫描不完整、价格或库存不可读，以及跨越 18:00
且无法按逐项 `observed_at` 归属时，必须 `estimation_eligible=false`，并按需要降级
质量或创建 Incident。

日结至少按“平台交易日 × 平台 × 品种 × 等级 × 数据来源”统计销量、卖家实收金额、
实收单价、销售时段、首末销售时间、峰值份额、库存轨迹、可售时长、异常影响和数据质量。
不能从页面可靠得到订单数时不得输出伪造的订单数；可输出观察行数，但名称和口径必须
明确。

### 6.3 为销售预测补充的特征

除用户提出的品种、等级、销售额和销售时段外，后续预测应准备：

- 10/30/60 分钟时间桶的销量与实收金额曲线。
- 星期、月份、节假日前后、交易日序号等日历特征。
- 平台价格、卖家实收单价、价格变化次数和价格持续时长，三者口径不得混用。
- 上架状态、可销售时长、缺货开始时间和补货时间。
- 期初库存、补货量、可售库存下限和库存约束标记。
- 同品种不同等级之间的销量替代关系。
- 包装产能、冷库容量和预计采收量等供给约束。
- 订单到达间隔、销售峰值、峰值出现时间和峰值持续时长。
- 16:00–18:00 高峰销售占比、18:00–20:00 下一交易日早期销售。
- 当前在线商品、价格、库存、商品轨迹和可售时长。
- 取消推导量、异常影响和后续订单观察修订量。
- 商品映射质量、日结完整性、扫描覆盖率和字段质量分数。
- 可选的节假日、促销活动和天气标签；没有可靠来源时不得编造。

“浏览量、访客数、曝光量、转化率”等指标只有在平台稳定提供对应页面和口径后才能
加入。仅凭订单数据和上架时长计算的结果应命名为“销售速度”，不能称为“转化率”。

## 7. 自动任务与人工任务分离

### 7.1 来源模型

普通任务和系统任务必须记录：

- `origin_type`
- `origin_ref_id`
- `approval_policy`
- `policy_version`

`MANUAL` 和 `AUTOMATION` 类新任务都必须提供非空、稳定的 `origin_ref_id`；人工
入口使用 `web:`、`cli:`、`workbook:`、`acceptance:` 等可追溯前缀，测试工具使用
`test-harness:`。历史记录缺少结构化来源时只标记 `LEGACY`，不得猜测。

核心 `origin_type` 只能取：

- `MANUAL`
- `AUTOMATION`
- `SYSTEM_EMERGENCY`
- `LEGACY`

Issue #20 中的细分来源名称是运营语义，不扩张为第二套数据库枚举，固定映射为：

| 运营语义 | 核心来源 |
| --- | --- |
| `MANUAL_WEB` | `MANUAL + web:<request-or-form-id>` |
| `MANUAL_CLI` | `MANUAL + cli:<command-run-id>` |
| `AUTOMATION_RULE` | `AUTOMATION + rule:<rule-or-run-id>` |
| `AUTOMATION_SCAN` | `AUTOMATION + scan:<automation-run-id>` |
| `SYSTEM_RECONCILE` | `AUTOMATION + reconcile:<execution-attempt-id>` |
| `SYSTEM_EMERGENCY` | `SYSTEM_EMERGENCY + emergency:<authorized-run-id>` |
| `LEGACY` | `LEGACY + NULL` |

`origin_type` 与 `origin_ref_id` 创建后均不可修改。通用 Repository 不得新建
`LEGACY` 或 `SYSTEM_EMERGENCY`；数据库不可通过 UPDATE 把既有任务改成这两种来源。
`SYSTEM_EMERGENCY` 仍只能由 13.5-6 的专用授权入口创建。

自动化运行使用 `automation_jobs`、`automation_runs`、`automation_run_events` 和
`automation_run_links` 独立建模。扫描不是普通任务；只有扫描或规则产生的业务处置才
创建任务，并通过 link 表关联来源运行。`created_by`、trigger 和 dedupe 信息可以作为
实现补充，但不得代替上述来源与授权合同。

### 7.2 模块边界

- 人工任务模块展示运营人员直接创建、调整或选择的任务。
- 自动任务模块展示规则评估产生的候选任务；扫描运行在自动化模块展示。
- 系统任务模块展示紧急下架和唯一 RECONCILE。
- 自动任务失败不能阻塞人工任务列表的查看和处置。
- 人工任务和自动任务可以复用同一任务状态机、Review 服务和执行账本，但必须可以按
  来源、通道和运行批次独立筛选、统计和暂停。
- 暂停自动化通道不得删除候选任务，也不得停止正在副作用区内执行的操作。
- 除满足全部 S4 门禁的 `SYSTEM_EMERGENCY` 外，自动化通道的最高权限是创建
  proposal、Review 或候选任务。
- Web、CLI 和 Scheduler 必须调用同一 application service；Web 不得通过 subprocess、
  自定义队列 JSON 或备用 gate 绕过服务层。

## 8. 异常分类与处理

### 8.1 统一异常分类

`category` 只能使用以下稳定值：

- `PLATFORM_LOGIN`
- `PLATFORM_NETWORK`
- `PAGE_STRUCTURE`
- `SCAN_INCOMPLETE`
- `WORKER_UNAVAILABLE`
- `QUEUE_BACKLOG`
- `PRODUCT_MAPPING`
- `PRICE_ANOMALY`
- `INVENTORY_ANOMALY`
- `ORDER_PAGE_UNAVAILABLE`
- `ORDER_DATA_INCONSISTENT`
- `SALES_ESTIMATE_LOW_CONFIDENCE`
- `NOTIFICATION_FAILURE`
- `WRITE_UNKNOWN`

### 8.2 严重度和处置策略

| 等级 | 名称 | 默认动作 |
| --- | --- | --- |
| `S0` | `INFO` | 记录信息，不要求处置 |
| `S1` | `LOW` | 日报汇总 |
| `S2` | `MEDIUM` | 首次提醒；未解决时按较长周期提醒 |
| `S3` | `HIGH` | 立即通知并默认每 10 分钟重复提醒，直到介入或解决 |
| `S4` | `CRITICAL` | 立即通知，默认每 5 分钟重复提醒；必要时进入受控自动保护 |

每个异常至少记录：

- 稳定异常代码和去重指纹。
- 严重度、当前状态、首次出现、最后出现和累计次数。
- 影响平台、商品、订单摘要、运行或批次。
- 责任人、确认时间、处置截止时间。
- 自动处理记录、人工处理记录和恢复证据。
- `OPEN / RETRYING / WAITING_HUMAN / ACKNOWLEDGED / AUTO_PROTECTING /
  RESOLVED / CLOSED` 状态；确认不等于已解决。

自动处理只允许安全重试、重新只读扫描、补发通知和恢复数据库投影。任何可能重复产生
平台副作用的异常仍必须遵守写锁、phase 和唯一 RECONCILE。

### 8.3 S4 紧急下架

普通低于成本异常可以是 S3。S4 仍是任务 13.5 的完成目标，但不在 13.5-1 固化最终
方案。实施顺序为：

1. 先完成异常发现、重复通知、人工确认、豁免、修价和人工下架闭环。
2. 在 13.5-6 使用 13.5-2～5 的真实扫描、销售和人工处置数据冻结最终策略。
3. 策略结构完成迁移、管理员预审批并启用后，才允许编码和开放自动保护。

在上述门禁全部完成前，`automatic_emergency_offline=false`，任何 Agent、Web、Scheduler
或脚本都不得创建或执行 `SYSTEM_EMERGENCY` 自动下架。

版本化 `EmergencyPolicy` 至少冻结：

```text
policy_version
enabled
effective_from
effective_until
cost_source
cost_freshness_limit
emergency_price_threshold
first_alert_wait_window
second_observation_requirement
max_auto_offline_per_product_trade_day
cooldown_window
forbidden_conditions
auto_relist = false
```

只有命中已启用策略并同时满足下列流程，才允许紧急下架：

1. 第一次完整观察创建 Incident 并通知。
2. 等待策略规定的一个完整 `ONLINE_PULSE` 周期。
3. 重新读取当前价格，并检查期间是否有人工作业、豁免或状态变化。
4. 若条件仍成立且无人工干预，创建 `SYSTEM_EMERGENCY` 授权和单一
   `SET_OFFLINE` 业务任务。
5. 复用 v5 写动作和 Importer；结果为 UNKNOWN 时只允许现有唯一 RECONCILE。

以下任一条件存在时禁止自动下架：映射不是 `VERIFIED`、成本缺失或过期、价格不可读、
扫描不完整、存在活动/UNKNOWN/Review 阻塞写锁、商品已下架、达到每商品每交易日
次数上限、处于冷却期、策略未启用/未生效/已过期、人工已介入/豁免或页面结构异常。

Incident ACK、Review 已处理、保护暂停、策略修改/停用、人工写任务、人工修价/下架/
豁免均视为人工已经介入并阻止本轮自动保护。紧急下架后不得自动重新上架。

## 9. Web 前端重写

### 9.1 设计目标

Web 主控端以运营人员的工作问题组织页面：

- 今天发生了什么？
- 哪些事情需要我处理？
- 自动化是否正常运行？
- 哪些商品、订单或数据有异常？
- 今天卖了多少，什么时间、什么品种和等级卖得最好？
- 哪些动作已经执行，结果是否确认？
- 系统是否具备继续运行的条件？

内部 schema、合同版本、operation/attempt、phase、hash 和本地路径默认不出现在主流程，
仅在“高级诊断”中按需展开。

### 9.2 推荐导航

1. **今日运营**
   - 同时显示平台交易日、卖家作业日、当前阶段、截单倒计时和交付重叠。
   - 展示扫描、在线商品、销售及质量、下一交易日早期销售、HIGH/CRITICAL 和人工介入。
   - 展示 Worker、队列和自动化健康状态。
2. **平台商品**
   - 区分 `ONLINE_PULSE` 和 `FULL_MARKET_SCAN` 的位置事实。
   - 展示映射、价格/库存轨迹、异常、写锁和关联任务。
3. **销售分析**
   - 严格区分 `ORDER_OBSERVED` 与 `SCAN_ESTIMATED`。
   - 展示日结版本、质量等级、峰值份额、18:00–20:00 早期销售和预测输入。
4. **自动化**
   - 调度计划、最近运行、下一次运行、暂停/恢复、手工补跑。
   - 运行状态使用 `SCHEDULED / RUNNING / SUCCESS / PARTIAL / FAILED / MISSED /
     MERGED / SKIPPED / CANCELLED`。
5. **待处理**
   - 集中 Review、登录、映射、页面、HIGH/CRITICAL、UNKNOWN、通知失败和低置信估算。
   - 确认不等于解决；支持指派、安全重试、处置时间线和恢复证据。
6. **任务中心**
   - 按人工、自动、系统紧急和全部任务分组。
   - 展示候选、授权、执行中、异常和完成状态；扫描运行不伪装为普通任务。
7. **业务资料**
   - 商品资料、库存补充、平台映射、价格规则、上下架规则、包装产能和冷库状态。
8. **系统维护**
   - 执行审计、UNKNOWN/RECONCILE、服务健康、队列、Outbox、磁盘、备份和高级诊断。

### 9.3 页面和交互规则

- 状态文案必须是运营语言，例如“等待人工复核”“平台结果待确认”，不能只显示
  `PENDING` 或 `UNKNOWN`。
- 危险操作与只读补跑在视觉和确认方式上严格区分。
- 页面顶部只显示与当前角色相关的主操作；诊断操作放在二级区域。
- 所有列表提供时间范围、平台、状态、来源和关键字筛选，并支持保存常用筛选。
- 详情页展示“发生了什么—影响什么—系统做了什么—需要我做什么”的时间线。
- 空状态、加载状态、数据过期和部分失败必须明确，不能用空白页面代替。
- 自动刷新不得丢失筛选条件、表单输入或当前查看位置。
- 所有时间显示业务时区，并可查看原始 ISO 时间。
- Web 不直接启动不可审查的 subprocess，不在请求线程中执行长任务。
- 保留服务端渲染技术路线，除非在 T13.5-0 审查后另行批准前后端分离。
- 代码渐进拆分到 `app/webapp/application.py`、`auth.py`、`csrf.py`、`routes/`、
  `presenters/`、`templates/` 和 `static/`；业务应用服务继续保留在
  `app/services/`，先保持兼容路由和行为测试。

## 10. 建议的数据结构

任务 13.5 的候选迁移版本为 schema v14。以下表名作为合同基线，不表示可跳过无损迁移、
索引、外键、回滚和旧库兼容评审：

### 10.1 自动化运行

- `automation_jobs`
- `automation_runs`
- `automation_run_events`
- `automation_run_links`

### 10.2 商品与订单观察

- `product_observation_batches`
- `product_observation_items`
- `order_observation_batches`
- `order_observation_items`

### 10.3 销售与日结

- `sales_estimate_segments`
- `platform_trade_day_summaries`

### 10.4 异常

- `operational_incidents`
- `incident_notification_state`

### 10.5 现有表扩展

- `tasks.origin_type`
- `tasks.origin_ref_id`
- `tasks.approval_policy`
- `tasks.policy_version`

实施时必须冻结：

- `script_runs` 与 `automation_runs` 的兼容或迁移关系，避免双运行账本。
- 现有 `listing_sync_snapshots` 到不可变商品观察批次的映射。
- 订单行指纹规范、`occurrence_no / occurrence_count`、多重集合比较和已接受完整
  批次选择规则。
- `PROVISIONAL → OBSERVED → RECONCILED → FINAL` 与 supersedes 关系。
- `listing_anomaly_cases` 与 `operational_incidents` 的兼容投影，避免两个异常系统。

核心 v14 只在任务来源字段中预留 `SYSTEM_EMERGENCY` 和策略扩展边界。
`emergency_action_policies` 的最终字段、约束和迁移在进入 13.5-6 前冻结；具体 Schema
版本号由该阶段决定，不在核心 v14 提前固化尚无运营证据的策略字段。

## 11. 调度、幂等和恢复要求

- 同一 schedule 在同一计划时间只允许生成一个逻辑运行。
- 使用数据库租约或等效单实例机制，不能仅依赖进程内锁。
- 每次运行记录 `scheduled_for / started_at / heartbeat_at / finished_at`。
- 自动化运行对外状态使用 `SCHEDULED / RUNNING / SUCCESS / PARTIAL / FAILED /
  MISSED / MERGED / SKIPPED / CANCELLED`；详细步骤结果保存在事件中。
- 重试必须复用逻辑运行身份并增加 attempt，不得产生无法关联的重复运行。
- 错过小扫描时只补最近一次；完整扫描和日结按交易日策略补跑，避免启动后形成任务风暴。
- 运行超时后先判断是否进入平台副作用区；只读扫描可以安全终止，写操作沿用现有恢复状态机。
- 手工补跑必须记录操作者、原因、原计划时间和是否覆盖正常结果。
- 暂停 schedule 不等于中止正在运行的任务。
- 服务器时间变化、休眠唤醒和跨日必须有测试。

## 12. 最小运维和可观测性

任务 13.5 只补齐进入综合验收和连续观察所需的最小能力：

- 服务健康总览和结构化健康检查。
- 最近成功时间、数据新鲜度、运行耗时和失败率指标。
- 队列积压、Outbox 积压、异常未处理数量和日结不完整告警。
- 冷态与暖态扫描耗时分别统计。
- 证据、日志、快照和归档的保留周期。
- 磁盘容量阈值和安全清理策略；不能删除未导入、UNKNOWN 或未完成审查的证据。
- SQLite 在线备份、恢复校验和迁移前备份。
- 服务账号、最小权限、密钥轮换和配置校验。
- 系统暂停、单模块暂停、恢复、补跑和灾难恢复操作手册。
- 功能开关：先 shadow/dry-run，再只读实机，最后开放运营入口。

任务 17 再完成正式运维看板、长期保留政策、完整备份轮换、生产 Runbook 和项目收口；
任务 13.5 不以这些后续工作未完成为由放宽当前安全门禁。

## 13. 分阶段实施

### T13.5-0：黄金基线与临时能力审计

- 冻结任务 12/13 最后成功代码 SHA、实机 Run ID、证据和禁止重写函数。
- 盘点所有需要手工运行的脚本、入口、环境变量、运行责任和 Web 可见性。
- 标记临时代码：保留、正式化、替换或归档。
- 输出当前数据流、进程图、故障点和 Web 页面问题清单。
- 归档带精确时间、main SHA、Runtime DB 脱敏快照、浏览器/视口/角色、路由和 DOM
  hash 的独立 Web 审计。
- 输出数据质量、日结状态、多重集合、库存估算和 S4 扩展边界草案，不创建 v14 表，
  不修改业务路径。

验收：审计清单完整，且明确每个脚本未来归属；不改真实平台动作。

### T13.5-1：双时间轴与 schema v14

- 冻结 18:00 平台交易日、20:00 卖家作业日、三个运营阶段和逐条观察归属函数。
- 设计 schema v14 无损迁移、回滚、旧数据兼容和索引。
- 冻结 `fact_source / quality_level / summary_status` 三个正交维度、六级数据质量矩阵和
  `PROVISIONAL → OBSERVED → RECONCILED → FINAL`。
- 冻结自动化账本、观察批次、日结、任务来源和 Incident 核心合同。
- 预留 `SYSTEM_EMERGENCY` 来源和扩展边界，但不冻结最终 S4 阈值、成本新鲜度、
  等待周期、次数上限或冷却时间，不实现自动紧急下架。

验收：质量矩阵和日结状态机先通过评审，随后 17:59、18:00、19:59、20:00 边界
测试、迁移测试和事务失败注入通过。

### T13.5-2：商品映射与扫描器提取

- 从任务 13 链路中提取可复用的两页只读扫描与身份匹配服务。
- 实现 `ONLINE_PULSE`、`FULL_MARKET_SCAN` 和不可变商品观察批次。
- 实现 `supports_order_scan / supports_current_trade_day /
  supports_historical_trade_day` 以及 `UNSUPPORTED / UNAVAILABLE / FAILED`。
- 不完整扫描保留证据但不覆盖最后可信投影。

验收：上架中、待上架、重复身份、双页冲突、结束标记和跨边界归属均有测试。

### T13.5-3：独立 Automation Service

- 实现 jobs/runs/events/links、单实例租约、10 分钟/每小时触发、合并、补跑和心跳。
- 统一 UI 通道优先级：UNKNOWN/RECONCILE、紧急下架、普通授权写、边界扫描、日结扫描、
  小时完整扫描、10 分钟脉冲。
- 复用长期 Worker 生命周期，不为每次扫描重复启动影刀。

验收：重叠、休眠、错过、合并、超时、重启恢复和任务风暴防护测试通过。

### T13.5-4：订单页探索与历史观察

- 无副作用探索页面，冻结能力标志、字段白名单、分页和可访问历史范围。
- 在编码前冻结不可变订单批次、行指纹、`occurrence_no / occurrence_count`、
  跨批次多重集合和完整批次接受规则。
- 当前开放交易日只形成截至 `observed_at` 的快照，不伪造闭市完整订单或稳定订单 ID。

验收：历史日、重复行、多批次、空页、分页、部分失败和取消推导均有证据。

### T13.5-5：销售估算、日结和计划输入

- 分离 `ORDER_OBSERVED` 与 `SCAN_ESTIMATED`，落实六级数据质量。
- 在编码前冻结估算区间、已知库存调整来源、映射版本、资格原因、置信度和支撑观察。
- 生成 20:00 `PROVISIONAL`，后续按
  `OBSERVED → RECONCILED → FINAL` 单向推进并保留 supersedes 版本链。
- 输出高峰份额、18:00–20:00 早期销售、价格库存轨迹、异常和质量等预测输入。

验收：不同来源不得混算，低质量不得包装为精确事实，日结可以复算和追溯。

### T13.5-6：异常、重复提醒与紧急保护

- 落实 S0–S4、固定类别、状态机、指纹、确认、指派和恢复时间线。
- 先完成 S3/S4 重复提醒，以及人工确认、豁免、修价和人工下架闭环。
- 使用 13.5-2～5 的真实数据和处置经验评审并冻结 S4 策略。
- 在自动保护编码前迁移策略结构，落实管理员预授权、二次观察、次数上限、冷却、
  禁止条件和 `auto_relist=false`。
- 实现 `SYSTEM_EMERGENCY`、v5 下架和 UNKNOWN 恢复。

验收：人工闭环和支撑数据先通过评审；确认不等于解决；同异常不刷屏；禁止条件逐项
阻断；紧急下架后不能自动上架。

### T13.5-7：控制服务、脚本和任务来源对齐

- Web、CLI、Scheduler 统一调用 application service。
- 将人工脚本入口收口为诊断/补跑工具，不建立 Web subprocess 或第二队列。
- 补齐 `origin_type/origin_ref_id/approval_policy/policy_version` 和人工/自动/系统任务分组。

验收：入口行为一致、来源可追溯、扫描不伪装为任务、自动失败不影响人工处置。

### T13.5-8：Web 架构拆分

- 建立 `app/webapp/` 目录、受控模板层、presenter/ViewModel 和兼容路由。
- 先迁移只读页面和公共组件，再迁移表单与高风险动作。
- 建立 HTML 行为快照、CSRF/auth、分页、性能和可访问性门禁。

验收：拆分前后路由、权限、状态语义和 POST 行为一致，首屏不再加载全部大日志。

### T13.5-9：Web 运营 UI 重写

- 按八个权威一级入口重写导航、今日运营、平台商品、销售分析、自动化和待处理。
- 任务中心按人工/自动/系统紧急分组，业务资料和系统维护收纳高级功能。
- 移除开发者文案，原始合同、JSON、phase、hash 和路径默认折叠。
- 具体浏览发现、页面地图、技术拆分和验收门禁见
  [任务 13.5 Web 主控端重写计划](task13_5_web_rewrite_plan.md)。

验收：用运营任务脚本进行桌面和移动端可用性验收，不以“路由可以打开”为完成标准。

### T13.5-10：集成回归与任务 14 交接

- 完成代码、迁移、Web、调度、只读实机和受控紧急策略回归。
- 连续观察至少覆盖一个完整平台交易日和卖家作业日。
- 输出脱敏证据、验收矩阵、运行手册、已知限制和任务 14 输入。

验收：任务 14 可以直接进行多品种、多动作、异常恢复、正式授权和版本冻结验收，不需要
补做任务 13.5 的控制面。

## 14. 关键验收场景

### 14.1 调度

- 正常 10 分钟 `ONLINE_PULSE` 和每小时 `FULL_MARKET_SCAN`。
- 17:59、18:00、19:59、20:00 的日期与阶段归属。
- 小扫描与完整扫描同一时刻触发时按优先级合并，不重叠操作同一平台页面。
- Windows 休眠后恢复，不产生补跑风暴。
- 调度服务重启后租约和运行状态可恢复。
- 暂停、恢复和人工补跑均有审计记录。

### 14.2 数据

- 相同订单观察行以指纹和 `occurrence_no` 保留真实实例，`occurrence_count` 可复算，
  跨批次按多重集合比较而不累加销售。
- 当前开放交易日快照或请求日期真实不可用时，能力标志、终态、质量和页面实际范围一致。
- `ORDER_OBSERVED` 与 `SCAN_ESTIMATED` 不混算。
- `fact_source / quality_level / summary_status` 独立保存。
- 后续历史订单按 `OBSERVED → RECONCILED → FINAL` 推进，而不是静默覆盖
  `PROVISIONAL`。
- 不合格库存区间设置 `estimation_eligible=false`，无法解释的变化创建 Incident。
- 不完整扫描不更新可信当前投影。
- 日结卖家实收金额、有效数量和分桶合计可自动复算；不可得订单数不伪造。
- 数据库中不存在客户个人敏感信息。

### 14.3 异常

- 登录失效、网络失败、白屏、页面结构漂移和结束标记缺失。
- 重复商品身份、商品双页出现、双页缺失和订单业务键冲突。
- Worker 心跳失效、Outbox 堵塞、磁盘不足和备份失败。
- UNKNOWN 只由现有唯一 RECONCILE 恢复。
- S3/S4 重复提醒符合策略，`ACKNOWLEDGED` 不关闭未解决异常。
- S4 二次观察、人工干预、成本新鲜度、映射、页面完整性和写锁禁止条件逐项通过。
- 紧急下架后只允许人工审查重新上架。

### 14.4 Web

- 运营人员可在 3 次点击内看到今日待处理事项。
- 可区分“自动化运行失败”“候选任务待复核”“平台动作结果未知”。
- 可查看下一次扫描时间、最近成功时间和数据新鲜度。
- 首屏同时显示平台交易日、卖家作业日、当前阶段和截单倒计时。
- 可按品种、等级、时段、事实来源和质量查看销售统计。
- 技术人员可从高级诊断进入原始 operation/attempt/phase/证据摘要。
- 页面不展示密钥、原始 token、客户个人信息或不必要的本地路径。

## 15. 任务 14 前置门禁

只有满足以下条件才开始任务 14：

- [ ] 任务 12/13 黄金基线和禁止重写清单已经冻结。
- [ ] 双时间轴、边界归属和 schema v14 迁移通过验收。
- [ ] `ONLINE_PULSE`、`FULL_MARKET_SCAN` 和日结具备稳定运行记录及新鲜度指标。
- [ ] 自动任务、人工任务和系统任务具备可靠来源与策略版本。
- [ ] 候选任务、Review 和异常之间可以追溯到来源运行。
- [ ] Web 能清楚展示候选、复核、授权、执行和异常，不把 `pending` 解释为授权。
- [ ] 订单页能力声明、不可变批次、销售事实质量和日结修订机制通过验收。
- [ ] S0–S4、重复提醒、S4 禁止条件和紧急下架恢复通过演练。
- [ ] 普通写动作仍被明确任务与授权门禁阻止；`SYSTEM_EMERGENCY` 只有一个受控入口。
- [ ] Web 八个一级入口、分页、移动端和高级诊断通过运营验收。
- [ ] 中文文档、JSON、CSV/TSV 和 SQLite 迁移均通过编码及结构验证。
- [ ] 实机证据、自动化测试和文档结论彼此一致。

## 16. 待冻结的实现策略

18:00/20:00 双时间轴、事实来源、异常等级、紧急保护、八个 Web 入口和任务 14 边界已经
由 Issue #20 冻结，不再作为开放产品问题。实施阶段仍需冻结：

1. 不营业日、临时维护窗口和节假日的维护方式。
2. 非交易时段脉冲是否降频，以及错过运行的最大补跑窗口。
3. 订单页面可稳定读取的历史天数、分页范围和取消量推导方法。
4. S4 阈值、成本新鲜度、人工豁免、策略审批人、次数上限、冷却和回滚规则；这些内容
   在 13.5-6 编码前冻结，不是 13.5-1 或核心 v14 门禁。
5. S3/S4 通知对象、响应时限和重复提醒升级路径。
6. 运营员与管理员的最小权限差异。
7. 自动化、日志、订单观察、商品观察和证据的保留周期。
8. 冷态/暖态扫描、页面首屏和列表查询的 SLO。
9. 模板层是否引入 Jinja；若引入，必须补齐 wheel 依赖和隔离安装测试。

未冻结的实现策略可以先采用保守默认值进行只读原型和测试，但不得改变 Issue #20 的业务
口径，也不得在没有版本化策略时开放 `SYSTEM_EMERGENCY`。
