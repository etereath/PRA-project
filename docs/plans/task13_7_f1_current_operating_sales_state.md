# Task 13.7-F1 — 当前销售经营态完整功能

Issue：#62。规划基线：`main@aaa93497ea2862fc80d303be2aeafc65227d943b`（#61 已合并）。
交付状态：PLANNED；本文件不是实现、实机或验收通过证据。

## 1. 最新 Owner 裁决与后续任务

后续 13.7 按实际可使用的完整功能分配，不按技术构件设停工点：

| 功能 | 对应任务 | 必须交付 |
|---|---|---|
| F1 当前销售经营态 | #62 / 本 PR | 真实 Provider → 当前 Commitment → Health/恢复 → 当前 Web/API |
| F2 交易日经营周期 | #63 | Closing + purchase_sequence + Supply/Carryover + 消费真实 F1 服务的跨日旅程 |
| F3 Authority Cutover & S3 Retirement | #64 | 唯一记账/经营 authority、统一切换、整合验收和实际退役 |

旧 3B-R0、3B、3C、3D、3E、3F 仅用于追溯范围，不再逐个开工、暂停、审核。已合并的 S1/S2/2D/3A 不重开。
**没有 F3/S3，13.7 不算完成。** 这项最新用户裁决替代任何“retirement 可以省略”或“始终后置”的解释；不是删除仍有真实安全职责的代码。

一个功能负责人连续完成探索、实现、接线、定向验证和功能旅程后集中送审。普通内部 commit、函数、文件、最小 Schema 设计不逐次等待 Chat 再授权。

## 2. 功能完成后，管理者能做什么

从同一个当前经营入口看见：当前平台交易日已形成的销售承诺、实际粒度、来源、截至时间、可信度和故障下一步。在订单页仍显示旧日和已经 rollover 两种情况下均有明确结果，不能用旧 Summary 或零值掩盖缺失。

F1 必须交付整条 read-only 功能链，不接受只有 Provider、表、selector 或占位 UI：

`真实平台采集 → immutable observation → current selector → health/recovery → 正式查询服务 → 现有 Web/API`

## 3. 事实与功能范围

### 3.1 两个直接来源与接管

- 冻结期：`CurrentTradeDaySalesObservation` 读取当前交易日的品种、等级、累计数量；先在真实页面确认入口、数量含义、单位、reset、零值、完整性和适用窗口。不存在的订单 ID、金额、下单时间不得伪造。
- rollover 后：复用 current-trade-day Order Observation。接管须兼容 platform/trade-date/scope/unit/as-of，不把两个累计值相加。
- 不同时间的 22→25 可以是正常增长，不因此机械报冲突；较旧订单证据不能把较新的累计值压回去。未证明覆盖时，不把差值自动标成精确区间销量。
- Light Scan 保留价格/Exposure/状态及符合现行条件的 QUICK-derived 辅助；辅助不是 CONFIRMED 直接事实。
- 页面能力、日期、行时间、尾部/可信空页和重放采用已有约定；取得 source acquisition 完成事实不机械要求整个父 Run/ACK/archive 终态。

### 3.2 粒度与健康

- 自然粒度为品种+等级。只有整个累计覆盖时间及业务范围内唯一映射时才投影 SKU；1:N 保留合格 aggregate 与缺失 SKU 归属，不按当前在线状态、比例或人工决定伪造销量。
- 可信 0 与缺失/partial/unavailable 分开；新的失败读取不得覆盖历史 evidence 或伪造 current=0。
- 实现现行 Observation Health S0–S4 与 Recovery Calibration 语义，不另造降配的五个文字标签替代业务合同。合法排队/资源等待不算恢复失败；单 SKU 问题不扩大成平台 S4。
- 使用既有 Automation、Incident/Review/Outbox 和最小动作范围；Observation S4 不继承 Emergency 自动下架权限。禁止通用 blocker framework 或第二恢复状态机。

### 3.3 实际接线与隔离

- 当前 Web/API 必须消费真实 selector 的同一份查询结果，并表达来源/时间/粒度/未知和恢复下一步；不是额外建一套长期 shadow dashboard。
- 在隔离 Runtime 中实际启用新读模式跑完功能旅程，并交付受控切换接线。真实生产部署和 authority 切换保留给 F3 授权窗口；合并 F1 不自动切生产 /today。
- 新采集/导入不得调用旧 `Order → Settlement → InventorySalesApplication` 的 post-import 链，不创建 Sales Task、扣实物库存或修改平台。
- 当前 `app/services/order_automation_runtime.py` 的 `refresh_settlement_and_inventory` 是必须隔离的旧副作用接线，不是新 Provider 模板。复用 Reader/Importer 等真实适用能力，不复制不适用的会计责任。

## 4. 集成责任和共同边界

同一主 Codex/功能集成负责人负责组合正确性；可并行委派，不强制多 Agent，也不把组合责任交还用户。
F1 是 current sales query/selection/health 接口的单一实现 owner；第一次真实接线时在本计划记录实际符号、输入输出和固定提交，F2 直接消费，不复制另一 selector。

共同语义只约定必要内容，不先造通用协议框架：
- 查询范围：platform、platform_trade_date、单位、品种/等级及可证明的 SKU 归属；
- 当前结果：value 或明确缺失、as-of/覆盖范围、provider、quality/health、source references；
- supply 使用独立 production_date，不把 Closing 历史日期混入 current trade date；
- evidence、派生 current、业务决定和执行责任保持区分。

共享 `runtime_schema.py`、Automation composition、Order capture 和 Web queries 修改由主 Codex 串行整合；不让并行分支各造一套版本/字段。首条真实服务出现即联调，不等 F3。
F1 第一批实现顺带一次同步 Current Status、开发入口与本功能涉及的旧规划冲突；不是另开 docs-only 审批阶段，不改写历史验收记录。

## 5. 功能级验收（同一个 Runtime，不按组件分别宣告完成）

| ID | 完整验收场景 |
|---|---|
| F1-AC01 | 18:00 后 D+1、订单页 D：真实累计读取、入库、查询及 Web 均明确归属 D+1；跨 cutoff 批次不得混日 |
| F1-AC02 | 累计22→25、随后订单来源接管：兼容来源接替而非相加；更旧证据不使较新 current 回退 |
| F1-AC03 | 1:1 与1:N：合格聚合保留，SKU 投影有完整范围依据；下架 B 不把其历史销量归入 A |
| F1-AC04 | 可信零、加载失败、partial、stale 与 provider recovery：UI/查询不补零，安全 READ_ONLY 可恢复，影响限于真实 scope |
| F1-AC05 | 重启、相同内容精确 replay、同 ID 异内容：无重复累计、历史不覆盖、来源可追踪 |
| F1-AC06 | 正式 Adapter/Importer/selector/Web 连通；新链不进入旧 Settlement/Inventory/销售写；新模式在隔离环境运行，生产 authority 未隐式切换 |

真实新采集能力按 IG-07 提供 READ_ONLY 证据；fixture/CI 不代替页面定位、字段语义及自然粒度验收。现场窗口尚未授权/无法取得时，继续其他可验证实现，明确未核验，不虚构 PASS。

## 6. 复杂度与连续施工

复用 Automation Run/claim/event/lease、TimePolicy、file Queue、Worker 生命周期、checksum、master-data mapping 和资格校验原则。累计事实不能塞入订单行或库存字段；必要最小 append-only 持久结构应说明旧模型为何不能承载，不预先指定新表数量或状态机。

不做多账号/session/page认证、provider DSL、新 daemon/Queue、Exposure allocator、Agent Controller、小程序、F2 输入重写。F3 的退役工作在本功能实现时就登记受影响旧路径；本功能内明显死代码随替代清理，不把所有减法无限推后。

只有真实业务事实否定合同、未裁决 authority/accounting、跨授权边界或会造成误写/重复扣减/数据破坏时升级问题；普通实现适配不停止整包。
测试继续遵守当前 AGENTS：功能内定向验证；超过预算或需全量/实机时，汇总本功能需要一次申请，不以压缩流程为由免除必要证明，也不反复读取/跑相同证据。

## 7. 送审和权限

送审提供固定 Head、完整功能旅程、实际接口/消费者、源码与具体断言、真实 READ_ONLY/CI/本地证据区别、未核验项、受影响旧路径。整包一次审查、冻结问题集中整改；不为每个 helper 单开验收。
保持 Draft。当前规划/开发不授权 merge、部署、生产迁移、真实平台写或实际 authority cutover；READ_ONLY 现场操作亦需已有对应授权。
F1 功能实现通过不等于 13.7 完成；F3/S3 是强制完成条件。