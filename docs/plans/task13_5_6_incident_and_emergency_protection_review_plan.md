# 任务 13.5-6：异常、重复提醒与受控紧急保护审查计划

- 计划日期：2026-08-01
- 状态：设计评审待确认；本 PR 不实现业务代码
- Review Profile：`R4`
- 真实平台写操作：分阶段；13.5-6A/6B 为否，13.5-6C 为是
- 唯一允许的无人值守平台动作：复用既有 v5 `SET_OFFLINE`
- 新增平台动作类型：0
- 当前涉及平台：蚂蚁花团供应商微信小程序；公共核心保持平台隔离
- 当前账号假设：每个平台仅一个已授权账号；多账号支持不在本阶段范围
- 基线：`main@36cf2babf148cfabc038416e359c2e56a603cd9e`
- 宏观权威：GitHub Issue #20、`docs/plans/task13_5_operational_closed_loop_and_web_rewrite.md`
- 风险治理：`docs/pra_review_risk_and_complexity_governance.md`

## 1. 阶段定位

13.5-6 承接 13.5-2～5 已形成的商品观察、订单观察、销售估算、平台交易日日结、Automation Run、Review、Outbox、任务来源、写锁、operation/attempt 和唯一 `UNKNOWN → RECONCILE` 边界，完成：

1. S0～S4 统一异常分级；
2. Incident 指纹、状态机、确认、指派、恢复证据和时间线；
3. S3/S4 重复提醒；
4. 人工确认、豁免、修价和人工下架闭环；
5. 使用真实扫描、销售和人工处置数据冻结版本化 `EmergencyPolicy`；
6. 在全部门禁满足时，由专用授权入口创建 `SYSTEM_EMERGENCY` 来源的单一 `SET_OFFLINE` 任务；
7. 复用既有 ShadowBot v5 写动作、Importer、写锁和唯一 RECONCILE，不增加快速点击旁路。

本阶段第一次改变系统的无人值守授权边界。真实下架本身属于 R3 高风险写操作；由于本任务同时连接 Incident、通知、Review、Automation、任务来源、策略版本、写锁和执行恢复，整体采用当前治理体系最高等级 `R4`，并叠加 R3 的副作用幂等、人工状态漂移和受控实机验收要求。

## 2. 明确非目标

本阶段不实现：

- AI 自动决策、AI 自动定价或普通业务任务自动审批；
- 自动改价、自动上架、自动库存写入；
- 自动重新上架；
- 第二平台或同平台多账号；
- 第二套 Incident、任务、授权、执行或恢复账本；
- 绕过 application service 的直接写库、队列 JSON 或页面点击；
- 修改订单、确认发货、退款、支付或资金对账；
- 完整企业权限系统；只实现管理员预审批与运营员处置所需的最小角色边界；
- 24/7 正式观察与最终生产授权；由任务 14/15 的综合验收和连续观察承接；
- 自动紧急下架后的自动恢复或自动重新上架。

在 13.5-6C 完成受控验收且任务 14 正式授权前：

```text
automatic_emergency_offline = false
```

任何 Agent、Web、Scheduler、CLI 或脚本都不得创建或执行 `SYSTEM_EMERGENCY` 自动下架。

## 3. 分阶段实施与独立 PR

### 3.1 13.5-6A：Incident 与人工处置闭环

- Review Profile：`R4`
- 真实平台写操作：否
- 数据库迁移：原则上不新增 Incident 表，复用 v14 `operational_incidents` 与 `incident_notification_state`

实现范围：

- 固定 Incident 类别、S0～S4 严重度和状态机；
- 稳定指纹、重复出现计数、首次/最后出现时间；
- 指派、ACK、人工豁免、处理截止时间、恢复证据；
- S3/S4 重复提醒与通知去重；
- 将人工修价、人工下架、Review 和写任务作为人工介入证据关联到 Incident；
- 建立“发生了什么—影响什么—系统做了什么—需要人做什么”的时间线；
- 保持 `automatic_emergency_offline=false`。

13.5-6A 可以在本计划通过后编码，但不得创建 `SYSTEM_EMERGENCY` 任务。

### 3.2 13.5-6B：EmergencyPolicy 迁移与影子判定

- Review Profile：`R4`
- 真实平台写操作：否
- 数据库迁移：是；候选 Runtime Schema v15，准确版本号在合同评审时冻结

实现范围：

- 冻结并迁移版本化 `EmergencyPolicy`；
- 管理员预审批、启用/停用和生效区间；
- 成本来源、新鲜度、价格阈值、等待窗口、二次观察、次数上限、冷却和禁止条件；
- shadow/dry-run 规则评估；
- 只保存“本可触发”或“被何种门禁阻止”的审计结果；
- 不创建任务、不发布 COMMIT、不调用平台写动作。

未冻结策略字段和真实阈值前，不进入 13.5-6B 业务编码。

### 3.3 13.5-6C：SYSTEM_EMERGENCY 授权与受控下架

- Review Profile：`R4`，叠加完整 R3 写操作门禁
- 真实平台写操作：是
- 新增平台动作类型：0；只复用 `SET_OFFLINE`

唯一允许流程：

```text
第一次完整观察
→ 创建/更新 S4 Incident 并立即通知
→ 等待策略规定的完整 ONLINE_PULSE 周期
→ 第二次完整观察重新读取当前价格
→ 重验策略、成本、映射、写锁和人工介入
→ 专用服务创建 SYSTEM_EMERGENCY 授权
→ 创建单一 SET_OFFLINE 任务
→ 复用 v5 application service / gate / operation / attempt / write lock
→ ShadowBot Worker
→ Importer 回读
→ VERIFIED 或唯一 UNKNOWN → RECONCILE
```

不得自动重新上架；受控验收后的恢复由人工完成并留下恢复证据。

## 4. 当前部署假设与故障模型

### 4.1 部署假设

- 单机 SQLite；
- 单 Automation Service；
- 每个平台一个已授权账号；
- 每个平台账号仅一个平台写 Worker；
- 既有文件队列、Importer、Watchdog、写锁和唯一 RECONCILE 保持权威；
- 平台当前事实只由重新读取确认，本地数据库只保存意图、历史和证据；
- 员工可能绕过 PRA 直接在小程序中修价、下架、上架或调整库存，这是正常运营场景。

若后续引入同平台多账号、第二写 Worker 或跨机器接管，必须单独重新评审，不能复用当前单账号指纹和锁假设。

### 4.2 最坏事故

- 正常商品被错误自动下架；
- 人工已修价、豁免或下架，系统仍使用旧证据执行；
- 同一 Incident 重复创建多个下架任务；
- 不同平台或 SKU 的 Incident、策略或任务串用；
- 成本缺失、过期或错误时触发 S4；
- 页面读取不完整、价格不可读或映射失败时仍下架；
- 写入结果不明后自动重试，造成重复副作用；
- 策略停用、修改或过期后旧授权继续执行；
- 紧急下架后系统自动重新上架。

失败默认必须停止提升权限、保留证据并转人工处置；副作用不明只进入既有唯一 RECONCILE。

## 5. 复用矩阵与禁止重写项

| 能力 | 处理方式 | 权威 |
| --- | --- | --- |
| Incident 核心记录 | 原样复用/最小扩展 | `operational_incidents` |
| 通知重复状态 | 原样复用/最小扩展 | `incident_notification_state`、Outbox |
| 人工 Review | 原样复用 | `review_tasks`、Mobile Review |
| 任务来源 | 原样复用 | `tasks.origin_type / origin_ref_id / approval_policy / policy_version` |
| Automation 调度与租约 | 原样复用 | `automation_jobs/runs/events/links` |
| 商品事实 | 原样复用 | 不可变商品观察与 `listing_status` 可信投影 |
| 销售事实 | 只读参考 | 13.5-5 日结和销售输入；不得单独授权写操作 |
| 平台写动作 | 原样复用 | v5 `SET_OFFLINE` application service、operation/attempt、共享写锁 |
| 结果导入 | 原样复用 | ShadowBot Result Importer、receipt、ACK |
| 不明副作用恢复 | 原样复用 | 唯一 `UNKNOWN → RECONCILE` |
| EmergencyPolicy | 确需新增 | 一套版本化策略结构，不建立第二执行账本 |
| SYSTEM_EMERGENCY 授权 | 确需新增 | 一个专用 application service 入口 |

禁止：

- 新建“紧急下架快速 Worker”；
- 直接拼队列 JSON；
- Web/Scheduler 直接点击平台；
- 普通 Repository 创建或修改 `SYSTEM_EMERGENCY` 来源；
- 新建第二套写锁、operation/attempt、Importer 或 UNKNOWN 恢复；
- 复制 v5 `SET_OFFLINE` 点击链路；
- 为紧急动作增加新的任务状态机或动作类型。

## 6. Incident 合同

### 6.1 固定类别

`category` 只允许以下稳定值：

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

新增类别必须修改公共合同并单独评审，不能用自由文本替代稳定异常代码。

### 6.2 严重度

| 等级 | 名称 | 默认动作 |
| --- | --- | --- |
| `S0` | `INFO` | 记录信息，不要求处置 |
| `S1` | `LOW` | 日报汇总 |
| `S2` | `MEDIUM` | 首次提醒；未解决时按较长周期提醒 |
| `S3` | `HIGH` | 立即通知；默认每 10 分钟重复提醒 |
| `S4` | `CRITICAL` | 立即通知；默认每 5 分钟重复提醒；满足策略后才可能受控保护 |

任何严重度均不能单独构成平台写授权。普通低于成本异常可以保持 S3；S4 必须由冻结策略和完整证据共同判定。

### 6.3 状态机

固定状态：

```text
OPEN
RETRYING
WAITING_HUMAN
ACKNOWLEDGED
AUTO_PROTECTING
RESOLVED
CLOSED
```

必须冻结的语义：

- `ACKNOWLEDGED` 只表示有人已知晓，不等于 `RESOLVED`；
- `RESOLVED` 必须有恢复证据或明确人工裁决；
- `CLOSED` 是完成处置后的终结投影；
- 同一异常再次出现时必须按指纹和关闭策略决定重新打开或创建新 Incident；
- 严重度升级必须保留时间线，不能静默改写历史；
- `AUTO_PROTECTING` 只能由专用紧急授权服务进入；
- 平台写结果 `UNKNOWN` 不得通过 Incident 状态机自行重试。

### 6.4 指纹与隔离

指纹至少绑定：

```text
platform_name
+ subject_type
+ subject_key（如 internal_sku / run_id / queue）
+ category
+ stable_reason_code
+ policy_version（仅策略相关 Incident）
```

当前单账号部署下以平台隔离账号范围。未来增加多账号前，必须把账号身份纳入公共合同和指纹，不能让多个账号共享 Incident。

指纹不得把不同平台、SKU、策略版本或不同行为主体合并。相同指纹重复出现只增加计数和时间线，不得重复创建通知风暴或紧急任务。

## 7. 重复提醒与通知幂等

- S3：立即提醒，默认每 10 分钟；
- S4：立即提醒，默认每 5 分钟；
- ACK 后是否继续提醒由状态和处置截止时间决定，但 ACK 不能自动标记解决；
- `RESOLVED/CLOSED` 停止重复提醒；
- 同一 Incident、通知通道和 cadence 时间槽最多产生一个 Outbox 意图；
- 重放 Automation Run、Worker 重启或 Outbox 重试不得创建新的业务提醒身份；
- 通知失败只能重试投递，不能重新创建 Incident 或触发平台动作；
- 收件人、响应时限和升级路径必须在 13.5-6A 合同中冻结。

## 8. 人工介入阻断语义

以下任一事件发生后，必须阻止本轮自动保护，并在第二次观察前后重新检查：

- Incident ACK；
- Review 已处理；
- 人工豁免；
- 人工修价、下架、上架或库存调整；
- 人工创建相关写任务；
- 保护功能暂停；
- 策略修改、停用、过期或被后继版本替代；
- 商品价格、库存、在线状态或映射发生变化；
- 活动写锁、`UNKNOWN / PARTIALLY_APPLIED / NEEDS_RECONCILIATION`；
- 页面结构、登录、网络或扫描完整性异常。

人工介入必须按事件发生时间和商品/平台作用范围匹配，不能只检查当前是否存在一条开放任务。第二次观察必须重新读取平台事实，不能沿用第一次观察的价格或在线状态。

## 9. EmergencyPolicy 合同

版本化策略至少冻结：

```text
policy_version
enabled
effective_from
effective_until
approved_by
approved_at
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

约束：

- 策略默认禁用；
- 策略不可原地修改；修改必须创建新版本并显式替代旧版本；
- 生效区间不得重叠；
- 未审批、未启用、未生效、过期或被替代的策略不得授权；
- 阈值、成本新鲜度、等待窗口、次数上限和冷却值必须来自 13.5-2～5 的真实数据与人工处置复盘，不在本计划中臆造具体数值；
- 成本来源缺失、过期或无法证明时不得触发；
- `auto_relist` 固定为 `false`，数据库和服务层都不得允许开启；
- 影子判定和真实授权必须使用相同策略解释器，不能维护两套规则。

编码前仍需业务确认：

1. S4 价格阈值或计算公式；
2. 成本权威来源与新鲜度；
3. 首次告警后的等待周期；
4. 第二次观察必须满足的时间和完整性；
5. 每商品每平台交易日自动下架次数上限；
6. 冷却时长；
7. 策略审批角色与停用权限；
8. S3/S4 通知对象、响应时限和升级路径。

## 10. 二次观察合同

第一次观察只能创建/更新 Incident 和通知，不能直接创建平台写任务。

允许授权的第二次观察必须同时满足：

- 与第一次观察属于同一平台和 `internal_sku`；
- 使用同一有效策略版本；
- 两次均为完整、已接受、尾部可信的商品观察；
- 映射均为 `VERIFIED`；
- 当前价格可读；
- 成本来源在第二次观察时仍有效；
- 至少经过策略规定的一个完整 `ONLINE_PULSE` 周期；
- 第二次观察重新读取当前价格、库存和在线状态；
- 两次观察之间没有人工介入、豁免、相关写任务、活动写锁或状态漂移；
- 商品仍在线且没有达到次数上限或处于冷却期；
- 策略仍启用、生效、未过期且未被替代。

任一条件不成立时保持通知或转人工，不能通过降低质量等级来授权写操作。

## 11. 禁止自动下架条件

以下任一条件存在时必须 fail closed：

- 映射不是 `VERIFIED`；
- 成本缺失、过期或来源不可信；
- 价格不可读；
- 扫描不完整或尾部未确认；
- 页面结构、登录或网络异常；
- 商品已下架；
- 商品、价格、库存或在线状态在两次观察间变化且不能解释；
- 存在人工 ACK、Review、豁免、修价、下架、上架或相关人工任务；
- 存在活动写锁；
- 存在 `UNKNOWN / PARTIALLY_APPLIED / NEEDS_RECONCILIATION`；
- 达到每商品每平台交易日次数上限；
- 处于冷却期；
- 策略未审批、未启用、未生效、已过期、已停用或已被替代；
- 功能开关关闭；
- 系统无法证明当前运行使用的是已批准代码、数据库结构和策略版本。

## 12. 授权、幂等与事务边界

同一授权身份至少绑定：

```text
platform_name
+ internal_sku
+ platform_trade_date
+ policy_version
+ incident_id
```

当前单账号部署下，每个身份最多存在一个有效 `SYSTEM_EMERGENCY` 授权和一个单一 `SET_OFFLINE` 任务。

规则：

- 通用任务 Repository 不得创建 `SYSTEM_EMERGENCY`；
- 不得通过 UPDATE 把普通任务改成 `SYSTEM_EMERGENCY`；
- 专用授权服务必须在一个 SQLite 事务中重验 Incident、策略、二次观察、人工介入、次数上限、冷却和写锁前置条件；
- 授权任务创建、`origin_type=SYSTEM_EMERGENCY`、`origin_ref_id=emergency:<authorized-run-id>`、策略版本、Incident 关联和 `AUTO_PROTECTING` 状态应原子提交，任一失败整体回滚；
- 任务发布继续走既有明确 application service 和 action gate；
- 重放相同 Automation Run 或相同输入 manifest 不得创建第二个任务；
- 平台已达到目标下架状态时记录外部已完成，不重复点击；
- 平台状态与预期不符时停止覆盖并转人工；
- 副作用状态不明时不得自动重试，只进入唯一 RECONCILE；
- 紧急下架结果确认后不得自动重新上架。

## 13. 复杂度预算

```text
新增 EmergencyPolicy 表：1 个以内
新增 Incident 表：0
新增通知账本：0
新增平台动作类型：0
新增 Executor/点击链路：0
新增全局写锁：0
新增任务状态：0
新增 UNKNOWN 恢复路径：0
新增自动重新上架：0
新增无人值守授权入口：1 个专用 application service
```

若实现需要超过预算，必须暂停编码并重新评审，说明现有机制为何无法表达风险。不得为了形式对称、未来假设或增加审计字段而扩张控制面。

## 14. 编码前与生产启用门禁

### 14.1 13.5-6A 开工门禁

- 本计划通过评审；
- Incident 类别、状态机、指纹和重复提醒语义冻结；
- 明确 ACK、RESOLVED、CLOSED 的差异；
- 明确人工介入事件与作用范围；
- `automatic_emergency_offline=false` 有测试保护。

### 14.2 13.5-6B 开工门禁

- 使用真实扫描、销售和人工处置记录评审 S4；
- EmergencyPolicy 字段、版本和生效规则冻结；
- 具体阈值、成本新鲜度、等待窗口、次数上限和冷却已由管理员确认；
- Schema 迁移、回滚和健康检查设计通过；
- 影子模式不创建任务或平台写意图。

### 14.3 13.5-6C 开工与实机门禁

- 解决真实 Runtime DB 现有 `NEEDS_RECONCILIATION`；
- 使用 SQLite backup API 备份并验证真实 Runtime DB；
- 完成并验证 v14 迁移；
- 完成并验证 EmergencyPolicy Schema 迁移；
- shadow/dry-run 观察达到约定样本；
- 策略由管理员预审批且仍禁用真实自动写；
- 专用授权服务、幂等、人工漂移和 UNKNOWN 测试通过；
- 受控实机仅使用明确测试商品和单次 `SET_OFFLINE`；
- 实机结束后人工恢复，不启用自动重新上架；
- 任务 14 综合验收前，不把代码合并等同于生产授权。

## 15. 测试与验收矩阵

### 15.1 13.5-6A

- 固定类别和非法类别拒绝；
- 指纹在平台、SKU、类别、原因、策略版本上的隔离；
- 相同指纹重复出现只累计，不重复建案；
- S0～S4 默认通知策略；
- S3 10 分钟、S4 5 分钟 cadence 幂等；
- Outbox 重试不重复创建业务提醒；
- ACK 不等于解决；
- RESOLVED/CLOSED 停止提醒；
- 指派、豁免、处理期限和恢复证据；
- Incident 再现、重新打开和严重度升级时间线；
- 通知失败不触发平台动作。

### 15.2 13.5-6B

- 策略默认禁用；
- 版本不可原地修改；
- 生效区间不重叠；
- 未审批、未启用、未生效、过期和被替代策略拒绝；
- 成本缺失、过期、来源变化拒绝；
- 单次观察拒绝；
- 两次观察间隔不足拒绝；
- 映射、扫描、价格或在线状态不可信拒绝；
- 人工 ACK、Review、豁免、修价、下架、上架或相关任务阻断；
- 写锁、UNKNOWN、页面异常阻断；
- 次数上限和冷却；
- shadow/dry-run 与真实授权共用同一解释器；
- 影子判定零任务、零队列、零平台副作用；
- 迁移、新库、带数据升级、重复迁移、失败回滚和健康检查。

### 15.3 13.5-6C

- 相同授权身份只创建一个任务；
- 事务失败不留下半授权或错误 `AUTO_PROTECTING`；
- 策略或证据在事务内变化时授权失败；
- 人工状态漂移在写前读取时阻断；
- 平台已下架时不重复写；
- 写锁和 operation/attempt 隔离；
- 复用既有 v5 `SET_OFFLINE` 请求、Worker、Importer、receipt 与 ACK；
- 成功读回投影；
- `UNKNOWN` 后不自动重试，只进入唯一 RECONCILE；
- 不自动重新上架；
- 不同平台/SKU 不串扰；
- 全量回归、系统冒烟、Linux/Windows CI；
- 受控真实页面验收、零额外平台写、证据脱敏和人工恢复记录。

## 16. 第一轮代码审查冻结清单

第一轮审查负责尽量形成完整 P1 阻塞清单，后续复审原则上只验证以下问题是否关闭：

1. Incident 指纹导致不同平台、SKU、类别或策略版本串案；
2. ACK 被错误当成 RESOLVED；
3. 已解决 Incident 仍重复通知；
4. cadence 或重放产生通知风暴；
5. 已停用、未审批、未生效、过期或被替代策略仍可授权；
6. 成本缺失、过期或来源不可信仍触发；
7. 单次观察、部分扫描、尾部不可信或价格不可读仍触发；
8. 两次观察间隔不足或没有重新读取平台事实；
9. 两次观察之间人工修价、下架、上架、豁免、ACK、Review 或人工任务被忽略；
10. 页面状态、映射、价格、库存或在线状态漂移仍使用旧证据；
11. 活动写锁、UNKNOWN、Review 阻塞或页面异常被绕过；
12. 同一 Incident 重复创建多个紧急授权或任务；
13. 普通 Repository 可以伪造或事后修改 `SYSTEM_EMERGENCY` 来源；
14. Web、Scheduler 或脚本绕过 application service 直接写库、拼队列或点击平台；
15. 平台已下架仍重复执行；
16. 达到次数上限或处于冷却期仍执行；
17. `UNKNOWN` 后自动重试而不是唯一 RECONCILE；
18. 紧急下架后自动重新上架；
19. 策略判定成功但授权/任务创建失败，留下半状态；
20. 任务创建成功但 Incident、策略版本或来源关联不完整；
21. 功能开关默认启用；
22. 没有受控真实平台验收便宣称可投入运营；
23. 代码合并后绕过任务 14 正式授权直接生产启用。

不新增与当前 Incident、通知、策略和唯一紧急下架链无关的理论性门禁；发现确有新的高风险事故时，必须说明其不属于上述哪一类以及为何无法在现有清单内处理。

## 17. 完成定义

13.5-6 只有在以下条件全部满足时才完成：

- 13.5-6A Incident 与人工处置闭环通过 R4 审查；
- 13.5-6B 策略结构、迁移、影子判定和管理员预审批通过 R4 审查；
- 13.5-6C 专用授权、v5 `SET_OFFLINE` 复用、幂等、人工漂移和 UNKNOWN 恢复通过 R4/R3 审查；
- 全量回归、系统冒烟和 Linux/Windows CI 通过；
- 受控真实 `SET_OFFLINE` 验收通过，且没有额外平台写；
- 自动重新上架保持不存在；
- 真实 Runtime DB 迁移、备份和健康检查有独立证据；
- 任务 14 完成综合安全验收和正式授权前，生产功能开关仍保持关闭。

Refs #20
