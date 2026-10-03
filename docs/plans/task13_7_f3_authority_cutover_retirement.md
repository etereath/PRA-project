# Task 13.7-F3 — Authority Cutover 与强制 S3 Retirement

Issue：#64。依赖 F1 #62 / PR #65 和 F2 #63 / PR #66。
规划基线：`main@aaa93497ea2862fc80d303be2aeafc65227d943b`；实施整合以 F1/F2 实际已审查提交为准。状态：PLANNED，非切换/退役完成证据。

## 1. 最新 Owner 裁决：没有 S3，13.7 不算完成

S3 Migration / Engineering Retirement 是本任务核心，不是可省略的代码整洁工作。旧设计已被替代却继续被正常开发继承，属于交付未完成。
本裁决替代旧 Owner Boundary §8 中把“后置，不阻塞经营功能”解释成可永久延期的做法：可以按依赖先实现新功能，但必须完成适用退役才能结束 13.7。

本功能交付的不是一份清理建议，而是：
`F1/F2 已联调功能 → 唯一正常经营/记账责任 → 安全切换 → 旧路径实际退出 → 后续开发只有一套有效接口`

## 2. 集成负责人从现在介入，不最后才组装

同一主 Codex 对 F1/F2/F3 的共享 Runtime、Schema migration 顺序、Automation composition、Order capture 和 Web 查询承担集成责任；可以委派，但不把组装责任留给用户。
F1 自身从真实来源做到 Web；F2 送审前已消费真实 F1 接口并跑完整周期。F3 接收实际代码及固定 SHA，不接收仅 stub 可用的模块。
F3 可立即进行相关旧责任的窄清单、IG-05 方案和集成接缝检查，不必等全部功能完成才发现问题；破坏性退役与实际生产切换必须等前置证据和权限成立。

## 3. IG-05：先确定唯一 physical/accounting event

- 根据当前代码及真实运行接线，核对旧 Summary/Settlement sales application、新 Commitment、Closing、Supply、Carryover 各自职责。
- 提出并记录唯一允许记账的事实/事件、作用范围、日期、单位、去重依据及修订处理。若业务裁决仍缺失，集中提出最小选择供 Owner 决定，不由实现者猜测。
- 不把 Closing 成功、Provider 接管、累计快照刷新、Packaged 输入或 Exposure 调整当成当然的扣库存事件。
- 不 blanket 删除所有 sales-driven accounting；退役的是被新契约替代的路径，必须仍有明确的唯一现役记账责任。
- 重复导入、迟到观察、来源接管、跨日和历史修订均不得让同一责任扣两次。

## 4. 同 gate 迁移与 authority 切换

复用既有迁移、备份和维护能力，不建新 Cutover/Retirement 平台。

1. 绑定实际 Runtime、代码/Schema、数据来源与已授权范围，检查存量 Task、continuation、operation/attempt、锁、租约和历史 UNKNOWN；只按其真实副作用责任处理，不为“整齐”清空。
2. 在隔离副本上完成 F1/F2/旧链对照、baseline 对齐及 no-double-count；不是重跑所有历史阶段。
3. 明确切换边界：停止被替代的旧 Job/写入/计划责任，再启用唯一新经营链；Today/Quality/当前查询在同一 gate 切换，旧 Summary 仅保留历史查询。
4. 切换前可恢复到明确单一的旧 authority；跨越新权威变更/副作用边界后，禁止把陈旧旧源偷偷恢复为当前，采用显式恢复或 forward correction。任何恢复均不能双 authority。
5. 切换中断/重启后能识别当前唯一 owner 和下一步，不依赖页面或内存猜测。单项历史故障不机械冻结无共享风险的经营对象。

F1/F2 代码合并不自动切真实生产。实际切换需要用户的独立运行授权；本 PR 可以完成工具、接线、隔离验证和运行说明，但没有现场证据就不得宣告最终 13.7 验收完成。

## 5. 必须实际完成的 S3 退役范围

先以当前源码/实际调用链核实，再对被替代部分做减法；不能整段 cherry-pick 历史设计，也不做无关全仓清扫。

| 旧职责/成本 | 本任务完成要求 |
|---|---|
| 20:00 seller-day 第二销售日界 | 退出正常交易日分类/调度；20:00 仅 planning checkpoint；必要历史字段可只读保留 |
| 旧 Settlement/Summary current-sales authority | 从正常 current 查询、计划和记账路径撤出，历史查询不再拥有 current 权力 |
| Settlement→Plan→DailyTask、order import→旧 accounting 强耦合 | 删除/改接被新契约替代的生产回调与 owner，证明不重复生成计划或扣减 |
| 双 reader、旧 workbook fallback、已完成迁移的 hooks | 正常经营只走单一 authority；无消费者代码/配置真正删除，不以关闭开关冒充退役 |
| OD-58-RESID-01：history 参与 is_resolved | 核对当前责任与历史幂等回执边界，消除无必要审计格式 blocker；不损坏原子收口或历史 UNKNOWN |
| OD-59-RESID-01：continuation environment context | 去掉无现实必要的环境身份耦合/重确认，保留绑定正确执行目标与存量安全所需校验 |
| OD-59-COMPAT-01：legacy resolution_only | 证明无相关活动存量，或已安全完成迁移/收口后删除旧路径与多余 guard；不能删 guard 后让旧 envelope 获得写权限 |
| account_id 与旧企业化残留 | 消除公共经营/account/session/page 语义及无用途抽象；物理列是否删除看真实依赖/迁移风险，不为删列重造框架 |
| 重复工程成本 | 在受影响流程中删除同环境完整 suite 后重复的同一子集；必要专项/不同环境证明保留；减少无必要实时时钟等待与重复规范读取 |
| 开发入口/Canonical 文档/测试 | 更新当前职责和完成状态，替换旧企业化、组件停工和错误业务测试要求；历史报告不篡改，不为恢复旧行为保留测试 |

**完成必须含实际代码/接线减法。** 仅写 retirement 清单、加一个 disabled flag、把旧实现换名藏到 compatibility 包，不构成完成。
不以删除行数作为目标；删除的是无职责与重复责任，不是安全内核。

## 6. 允许保留的例外有退出条件，不是无限延期

每个未删除项只记一行可核查信息：实际消费者/存量对象、现在删除会发生的具体事故、现任负责人、可验证退役条件和当前处理方式。
例外可包括：尚未被微信小程序替代的必要 Web/认证；尚未处理完的存量授权 envelope；仍处于真实 cutover 窗口的受控恢复工具；物理删列风险较大但已无经营权力的 inert 历史数据。
“以后可能有用”“兼容保险”“代码已存在”不是保留依据。一个有前置条件的例外不能推迟整个 S3；前置已满足的部分本次必须删除。
历史 observation、Task、UNKNOWN、receipt、合法库存交易不是过度设计，不删除或改判。durable handoff、认证/权限、精确授权、有效期、幂等、事务、write lock、read-before/compare/write/read-after、唯一 RECONCILE 继续保留。

## 7. 功能级最终验收

| ID | 实际证明 |
|---|---|
| F3-AC01 | 同一 Runtime/真实 F1+F2：D销售→18:00 D+1→冻结期累计→19:00 Closing(D)→订单接管→20:00供给/结余→Harvest/Packaged覆盖→Human决定→既有执行/回读→下一轮 |
| F3-AC02 | 重复快照/Closing/重放、来源接管、晚到修订与跨日不会双扣库存、重复计划或再次换日；Exposure不写成实物库存 |
| F3-AC03 | 中断/重启、旧 pending/expired/UNKNOWN、执行锁与恢复：不会重放旧目标，不靠删除历史解锁；恢复后经营继续 |
| F3-AC04 | 新 authority 与 Today/Quality/当前查询同 gate 切读；旧配置/Job/回调不能悄悄复活第二条正常经营链 |
| F3-AC05 | 已无职责旧代码/配置/错误测试实际删除；移除旧依赖的新入口仍运行；每个保留例外都有实证和退出条件 |
| F3-AC06 | 授权范围内真实切换证据与隔离演练分别列明；有现场未验项时不虚称 F3/13.7 全部完成 |

执行链结果可在隔离环境使用受控适配器，不代表真实平台写验收；真实操作仅在用户明确授权范围。真实 READ_ONLY 和实际切换记录绑定 SHA、Runtime、范围及时点。

## 8. 交付、验证与停止条件

整包持续施工并集中送审。真实事实否定合同、IG-05 未裁决、越权或可能误写/数据破坏时升级，普通函数/字段/内部修补不逐次停工。验证超过 AGENTS 预算时一次汇总申请；不得为了省步骤取消事务、重启和迁移证据，也不无授权重跑全量。

送审材料以本计划追加的简短完成矩阵为主：固定 Head/集成 SHA、AC→入口/函数/真实断言、实际删除路径、存量处理、保留例外、CI与本地/现场证据、未核验项。无需另建冗长审计框架。
F3 开始时将适用 Canonical 中过时的 S3/旧阶段描述与现役目标统一，完成时更新唯一 Current Status；不重写历史证据。

当前 PR 保持 Draft；不授权 merge、结束 Draft、部署、生产 DB 迁移、真实平台写或实际 authority cutover。
**Implementation Review、真实切换验收和13.7总体验收分别报告；S3适用退役未完成，不能以F1/F2已可用结束项目阶段。**