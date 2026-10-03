# Task 13.7-F2 — 完整交易日经营周期

Issue：#63；上游 F1 #62 / PR #65；强制收尾 F3 #64。
规划基线：`main@aaa93497ea2862fc80d303be2aeafc65227d943b`。状态：PLANNED，非实现/验收证据。

## 1. 交付原则

本任务按可使用的完整经营功能交付，不把 Closing、订单字段、Supply、Carryover、页面各自做完即停。代替未实施的 3D/3E 技术阶段；普通内部步骤由 Codex 连续完成，整包功能旅程通过后一次送审、集中整改。
最新 Owner 裁决：**F3 Authority Cutover & S3 Retirement 必须完成，13.7 才算完成。** 不能把旧复杂度长期保留给后续开发。

## 2. 管理者能完成的完整功能

一个交易日结束后，上一日形成独立历史日结；新日结余和新供给可录入、修订并与实时销售承诺组合，用于人工经营决定：

`18:00 平台换日 → 新日销售继续观察 → 19:00 Closing(上一日) → 20:00 planning checkpoint → Supply 收敛 → 当前经营参考 → Human 决定`

时刻采用平台 TimePolicy（当前蚂蚁 Asia/Shanghai），不是运行机器本地时区。20:00 不第二次换日。

## 3. Closing 与订单证据

- 复用既有 Order READ_ONLY，先确认真实页面日期、完整范围、尾部/可信空页和订单字段。purchase_sequence 是页面可见“第 N 次购买”，不是 occurrence_no；不得从订单号、买家身份或历史猜测。
- 只补确有需要的字段/解析/最小持久化，并给出缺失值和类型语义。没有真实字段证据不得声称该采集能力完成；不添加财务净额、退款、买家 PII 等新范围。
- 19:00 采集被冻结的上一交易日；成功按平台/日期锁定正常自动重扫。既有事件/结果足够表达时不新建另一 Closing 状态机。
- 首次失败报告+一次自动重试；第二次失败至既有 Closing S2 Review，停止自动重试。普通 Closing 故障不自动升级 S3/S4；如实时 Provider 同时失效，F1 Health 独立评估。
- 需要历史维护时使用有权限的显式入口，记录 actor/reason/新来源，不让普通 late-data 重开成功日结，不把旧多版本 Settlement 当新 Closing。
- Closing 结果只影响历史日结查询；不直接修改新日 Commitment、Supply 或实物库存，也不自动生成销售任务。

## 4. Supply、Carryover 与真实管理入口

- 同一 production_date/商品范围/单位：PRODUCTION_FORECAST → HARVEST_ESTIMATE → PACKAGED_ACTUAL 是覆盖，不相加。高阶段优先，同阶段使用最新有效修订；晚到的低阶段不能覆盖高阶段。
- CARRYOVER_CONFIRMED 是进入新平台交易日后确认的、未被上一周期销售承诺占用的剩余。它与新生产供给分轴，昨日 Packaged 不自动成为今日 Carryover。
- 复用现有 HarvestForecast/输入资产，完成有权限的录入、修订、回读、历史与现有 Web 页面，不能只交一张表或离线 selector。
- 日期/范围/单位/粒度兼容时：经营参考量 = 已确认 Carryover + 当前有效 Daily Supply - F1 Current Sales Commitment。缺失保持 unknown/incomplete，不补零；不同粒度不得擅自分配到 SKU。
- 这些是经营参考而非 physical inventory balance，既不覆盖实物台账，也不据此重新引入 Exposure<=实物 的硬限制。Human 保留定价/上下架/Exposure 决定权。

## 5. F1/F2 组合从开发时开始

F1 是 current sales query/selection/health 单一 owner。F2 直接调用其真实服务，并保留 source/as-of/quality/scope；不得复制一个自己的 Commitment 算法或把 mock 当正式依赖。
F2 可先并行完成 Closing 和供给输入，但送审前必须在同一隔离 Runtime 集成真实 F1 固定提交，跑过完整跨日旅程。必要时在本分支整合 F1 进展；最终合并前同步已审查 main，清楚区分依赖提交与本功能增量。

同一主 Codex 负责接口、Schema migration 顺序、共享 Order capture、Automation composition 和 Web 查询的整合。首次可用 F1 接口就与 F2 连接；不等 F3 第一次联调。涉及共享文件由主负责人串行接入，不让两个分支各造一份不兼容模型。
F3 负责跨功能 authority/记账退役，不替 F2 补做缺失的功能接线。

## 6. Authority 与会计边界

在隔离 Runtime 中实际运行 F1/F2 新模式和 Web 查询，生产仍保持既有 authority，直到 F3 获得授权后执行统一切换。
既有 `build_order_read_only_handlers()` 中 order-import→Settlement/InventorySalesApplication 副作用不可照搬到新 Closing。复用适用的 Reader/Importer/Queue，隔离旧 post-import accounting。
F2 不裁决唯一 physical/accounting event，不把 Closing、累计销量、Supply 修订当作自动扣库存事件。发现 IG-05 决策缺口交 F3 主负责人集中裁决，其余授权内功能继续。

## 7. 功能级验收

| ID | 端到端场景和关键结果 |
|---|---|
| F2-AC01 | D 结束、新日 D+1 实时销售继续；19:00 Closing 只写 D，20:00 不换日，不污染 D+1 Commitment |
| F2-AC02 | 成功 Closing 重启/重复触发不重扫；失败报告后只重试一次，第二次进入 S2 人工；当前销售观察/安全恢复不被历史故障连坐 |
| F2-AC03 | 真实 purchase_sequence 采集、类型/缺失/允许字段边界、精确重放及历史证据保留；occurrence_no 不替代复购序号 |
| F2-AC04 | 从真实管理入口录入 Carryover40、Forecast120、Harvest115、Packaged113，F1 Commitment20：参考140→135→133；Commitment35时为118；迟到Forecast不能覆盖Packaged |
| F2-AC05 | 重启、重复导入/提交和同阶段修订不重复累计；无确认 Carryover、缺失供给或1:N SKU归属时不造数，页面与服务一致 |
| F2-AC06 | 在真实 F1 服务+正式 F2 接线上跑完周期，Closing/Supply/Exposure 不改实物余额与交易台账，不调用旧计划/扣减链；历史证据保留 |

上述数值是隔离验收样例，不是实际农场生产数据或默认阈值。F1 接口证据、真实新采集 READ_ONLY 和测试结果分别列出，不能相互替代。

## 8. 复用、减法与禁止扩张

复用 Automation/TimePolicy、Order evidence、Review/Token/Outbox、现有库存台账与输入资产。必要新字段/表说明现有模型不能表达的具体事实，不固定新表集合，也不为每个 stage 建子系统。
不建设第二 scheduler、daemon、Queue、通用 Provider/审批框架、Exposure allocator、多账号/session/page identity、第二平台或 Agent Controller。
本功能替代的旧正常接线记入 F3 退役范围；功能内已经无消费者的代码随替代删除，不能用“等待 S3”永久保留双实现。

## 9. 连续施工、验证和权限

开发按完整功能推进：探索→实现→真实接口接线→有授权的只读验证→端到端旅程→集中送审。普通修补不逐次停工；只在真实事实冲突、未裁决记账、超出授权或误写/数据破坏风险时升级。
遵守 AGENTS 验证预算。需要扩大测试或真实 READ_ONLY 时一次汇总本功能需求，不把每张表每个 helper 变成审批。没有现场权限可继续其他实现；不能以 fixture 代替 IG-07 实机证明。

送审提供固定 SHA、F1 真实依赖 SHA、功能场景/源码/断言、实际入口、重复/重启/故障结果、新旧路径差异及未核验项。实现通过不等于生产切换通过。
保持 Draft；当前不授权 merge、部署、生产迁移、真实平台写或 cutover。完成 F2 后仍必须执行 F3/S3，不能跳过强制退役。