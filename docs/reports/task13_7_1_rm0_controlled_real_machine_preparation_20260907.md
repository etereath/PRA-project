# Task 13.7-1 RM0：受控真实平台验收准备

角色：Operations / Real-Machine Acceptance Preparation；检查时间：2026-09-07T21:55:00+08:00。对应已合并 PR [#47](https://github.com/etereath/PRA-project/pull/47)，由 [PR #49](https://github.com/etereath/PRA-project/pull/49) 交付。本记录只覆盖 RM0，不证明部署或真实平台纵向旅程已通过。

> 2026-09-08 已追加 blocker remediation 结果；本节以下至“RM1 PASS 证据模板”保留 2026-09-07 初始 RM0 快照，最新结论见文末“2026-09-08 Blocker Remediation”。

## 结论与边界（2026-09-07 初始检查）

**RM0 结果：BLOCKED；当前不得进入真实 UPDATE_PRICE 写操作。** 仓库起点和非敏感本地配置已收口，正式 Queue 活动目录为空，已有执行账本未发现活动 attempt、UNKNOWN 或未释放写锁，Credential provider 在预期 Windows 用户上下文中可读取凭据。但以下门禁尚未通过：

1. 当前 Runtime 的 migration 记录已到 v18，但缺少 `execution_continuations`，正式 health gate 返回失败；
2. 实际 ShadowBot 应用目录有 3 个受控源码文件与 main 不一致；
3. Queue Service 和 Worker 均未运行，历史 heartbeat / lifecycle 互相矛盾，且历史 Queue Service heartbeat 没有 `task_execution_coordinator`；
4. 当前平台映射工作簿 12 条记录均为 `DISABLED`，Operations Web 无可用 VERIFIED 平台映射；
5. 测试商品身份、实际价格、目标价格和业务时段尚待负责人确认。

本次没有迁移真实 Runtime、同步 ShadowBot、启动 Web / Queue Service / Worker、打开真实平台、投递 READ_ONLY 请求、创建或授权 UPDATE_PRICE、点击改价、清理 operation / lock / reconcile 证据，或修改凭据内容。

`REAL PLATFORM WRITE NOT AUTHORIZED / NOT EXECUTED`

Stage Goal 保持 **NOT YET VALIDATED**。本记录不开始 13.7-2。

## 版本与合并事实

| 项目 | RM0 结果 | 证据 |
|---|---|---|
| 远端与本地 main | PASS | `origin/main` 与本地 `main` 均为 `08f4e70fc2ffcd54de6a247ae48f4804da136056` |
| PR #47 | MERGED | merge commit 为 `08f4e70fc2ffcd54de6a247ae48f4804da136056`；PR Head 为 `1011091d21bf8ae2be0bb9da7c124e2281bddf04`；最终 Windows / Linux Core 均 SUCCESS |
| Implementation Review | PASS | 用户最终裁决；P1-47-01、P2-47-01、P2-47-02 均 CLOSED |
| 当前运行服务版本 | NOT DEPLOYED / NOT RUNNING | Web、Queue Service 均无运行进程；历史 Queue heartbeat 不含 13.7-1 Coordinator，不能当作 #47 已部署证据 |

## 环境检查结果

### 本地配置与宿主关系

`scripts/local_env.ps1` 未受 Git 跟踪。RM0 只补齐了非凭据配置：明确的 Web 环境组合、Runtime、产品工作簿、平台映射、ShadowBot identity mapping 和实际 app-dir。现有 secret、applet URI、Credential target、用户名和密码均未改动或输出。

- 基础环境检查：PASS；仅有飞书签名 secret 未设置的可选告警。
- Operations Web 配置对象构建：PASS。
- `SHADOWBOT_RUNNER_TYPE=filequeue`；applet URI 已配置且前缀合同通过，真实值未记录。
- Queue 和 evidence 目录都存在于仓库外；Worker 配置中的 Queue 与 `SHADOWBOT_QUEUE_DIR` 一致，fault injection 关闭。
- 预期宿主：Operations Web 与 Queue Service 使用本仓库 main 和同一 Runtime / 工作簿 / identity mapping；Importer 与 Coordinator 同宿于 Queue Service；ShadowBot Worker 由影刀宿主运行，通过同一正式 Queue 交换请求和结果。
- 当前实际进程：Web 0、Queue Service 0；影刀 Shell 在当前 Windows 用户下存在，但 Worker heartbeat 为 `STOPPED`。因此当前没有可声明为已部署运行的 #47 服务链。

不把完整本机路径写入 Git。本轮脱敏 path fingerprint 为：Runtime `02bf7039…`、正式 Queue `f60b55c0…`、evidence `a73f4f79…`。

### Runtime / Schema / 执行遗留

正式 CLI 的只读 health 检查结果：

```text
schema_versions = 1..18
schema actual = 18
schema health = FAIL: missing execution_continuations
SQLite operational health = PASS
integrity_check = ok
foreign_key violations = 0
```

已存在表的只读检查结果：

| 对象 | 数量 |
|---|---:|
| ACTIVE / UNKNOWN write lock | 0 |
| UNKNOWN write lock | 0 |
| STARTING / RUNNING attempt | 0 |
| PUBLISHING / QUEUED / RUNNING / UNKNOWN commit batch | 0 |
| PREPARED commit batch | 0 |
| UNKNOWN commit batch | 0 |
| NEEDS_RECONCILIATION operation | 0 |

由于 `execution_continuations` 表缺失，未关闭 continuation 不能完成有效查询。本次零遗留结论只覆盖上述已有表，不能替代 migration 后的 continuation 重查。禁止通过创建空表、删 migration 记录或清理旧证据绕过 health gate。负责人授权维护后，应先做一致性备份，再从本 main 执行既有 v18 初始化/迁移入口并复跑 health；RM0 未执行该真实 Runtime 写入。

### Runtime 输入与身份映射

| 输入 | 结果 | 非敏感版本证据 |
|---|---|---|
| 产品工作簿 | 可解析，12 条记录，13 列表头完整 | SHA-256 `611a7cceb090ca3985cddf7c9623ec6a5bf3fd85ee53e507993407a18d301de7` |
| 平台映射工作簿 | 可编译，12 条记录 | SHA-256 `a47b7c298ad93a6e999694a02ec14d9a5636f982bd6f3198bdcfefd0cf3bc41e` |
| ShadowBot identity mapping | 可解析，12 条记录，商品名与等级字段完整 | SHA-256 `24f0dd9f88f0fe5e42587cc2b7866f035fef88bcbface3d0353afd99c4fa5ce6` |

产品和平台映射与历史 runtime-v18 启动脚本使用的输入一致。业务映射当前全部为 `DISABLED`，所以“文件存在且可解析”不等于 Operations Web 已有可执行商品身份。负责人必须在 RM1 前确认测试对象，并通过正式映射维护流程形成唯一、有效的 VERIFIED 映射；不得直接改 Runtime 或为通过验收伪造映射。

### Queue 与生命周期

正式 Queue 的 `inbox/working/results` 文件数均为 0。没有删除 archive、quarantine、control 或历史证据。

历史状态记录不能作为当前运行证据：

- Queue Service heartbeat 自称 `RUNNING`，但已陈旧约 4.2 天，且组件只有 Importer、Watchdog、登录验证码监视、Review reminder、Outbox，没有 `task_execution_coordinator`；
- Worker heartbeat 为 `STOPPED`，已陈旧约 6.9 天；
- lifecycle 仍记录 `RUNNING`，已陈旧约 7.3 天；
- 进程检查没有发现 Queue Service 或 Operations Web。

这是生命周期记录与进程事实不一致，不得删除 control 文件来制造“干净”。RM1 前由负责人按现有运维手册核对影刀窗口、Worker 和 Queue Service，保留旧记录并形成新的可验证 heartbeat。

### ShadowBot 与凭据

实际 `xbot_robot` 候选目录具备 `package.py`、`selectorsV2.xml`、Worker 配置、凭据 provider 和 Queue Worker。部署结构/语法校验 PASS，但 `scripts/sync_shadowbot_test2.py --check` 返回失败：

| 文件 | 结果 |
|---|---|
| `module1.py` | DIFFERENT |
| `vertical_slice_read_price.py` | DIFFERENT |
| `shadowbot_queue_worker.py` | DIFFERENT |
| `shadowbot_credentials.py` | CURRENT |
| `shadowbot_contract_primitives.py` | CURRENT |
| `emergency_offline_fence.py` | CURRENT |
| `product_identity_mapping.json` | CURRENT |
| `shadowbot_worker_config.json` | EXISTS |

不得把 `verify_shadowbot_deployment.py` 的结构 PASS 误写成源码一致。负责人授权本地部署写入后，必须先确认 Worker 已安全停止并退出影刀编辑器，再同步这 3 个文件，随后要求 `--check` 全部 CURRENT；RM0 未同步。

Worker 配置已启用自动登录、必需选择器齐全、Credential target 已配置。部署目录中的 provider 在当前 Windows 用户上下文读取 Generic Credential 成功，账号与密码均为非空；本记录没有读取后保存、打印或记录 target、用户名、密码或 `CredentialBlob`。所有影刀宿主进程也属于该当前用户，因此用户上下文匹配。此项只证明 Credential Manager 可读，不证明平台接受凭据或当前登录会话有效；后者只能在负责人授权 RM1 后由真实 read-before 验证。

## RM1 前测试对象确认清单

“艾莎 B级”与 `expected_grade=B级` 只来自历史运维记录，不是当前平台事实，也不是本次默认测试对象。负责人须在 RM1 前逐项签字确认：

- [ ] platform；
- [ ] account；
- [ ] internal SKU；
- [ ] platform product identity；
- [ ] 商品名称与等级；
- [ ] 上述身份在产品工作簿、VERIFIED 平台映射和 ShadowBot identity mapping 中唯一一致；
- [ ] 新鲜平台 read-before 得到的当前实际价格；
- [ ] 本次目标价格；
- [ ] 测试时段及价格暴露的业务影响可接受；
- [ ] 若需恢复价格，已理解必须作为新的人工决定重新走 Task + Authorization，不允许自动回滚。

RM0 不决定目标价格，也不从旧日志推断当前价格。

## RM1 开工门槛

只有以下项目全部有同一现场窗口证据后，才可开始真实 read-before 和后续写旅程：

- [ ] 负责人明确授权进入 RM1；
- [ ] 待部署 SHA 固定为现场确认的最新 main，Web 与 Queue Service 均从该版本启动；
- [ ] Runtime 完成授权维护、migration 和备份，`check-runtime-health` 返回 PASS；
- [ ] migration 后重新检查 open continuation、operation、attempt、write lock 和 Queue，无测试 SKU 冲突；
- [ ] ShadowBot 同步后 `--check` 全部 CURRENT；
- [ ] Worker / lifecycle / Queue Service heartbeat 与实际进程一致，Queue Service heartbeat 含 `task_execution_coordinator`；
- [ ] Web、Queue Service、Worker、Importer 使用同一 Runtime 输入和正式 Queue；
- [ ] Credential provider 仍可在 Worker 用户上下文读取，且未泄露任何值；
- [ ] 测试对象确认清单完成；
- [ ] 异常停止负责人、证据记录人和人工恢复负责人在场。

## RM1 预定操作步骤

以下步骤只供负责人批准后的 RM1 执行。本次没有执行。

1. 记录部署 main SHA、Runtime schema、配置 fingerprint 和测试对象确认；对正式 Queue 与 execution ledger 做开始前快照。
2. 通过正式 READ_ONLY 实机链获得一次新鲜平台价格，记录 `observed_at` 和 source；这是 expected old price 的唯一现场起点。完成导入并确认该只读 attempt 离开活动目录。
3. 暂停 Queue Service，记录停止时间和进程事实；保持 Worker 空闲，不创建 `stop.signal` 清理历史状态。
4. 管理者从正式 Operations Web 创建 1 个 Human `UPDATE_PRICE` Task，核对精确 scope、TTL、目标和 expected old price，再做最终确认；记录 authorization accepted timestamp。
5. 在 Queue Service 仍暂停时，从 Runtime 只读确认恰有一个未关闭 durable continuation，且 Web 没有直接发布第二条即时路径；`inbox/working/results` 仍无该写 attempt。
6. 必要时退出并重建 Web，重新登录后确认同一 Task / continuation 仍可见，授权责任不依赖页面内存。
7. 启动或重启 Queue Service，记录启动时间；确认 heartbeat 使用本次 main、包含 `task_execution_coordinator`，且 Runtime / Queue fingerprint 与 Web、Worker 一致。
8. 等待 Coordinator 从持久 continuation 发现授权；确认只产生一个正式 COMMIT 和一个 execution attempt。出现第二个即停止。
9. Worker 完成 read-before，记录实际价格、时间、来源、identity 和 checksum 绑定。
10. 比较 read-before 与 expected old。完全一致才继续；漂移时停止并进入重新确认/终止，不覆盖平台新事实。
11. Worker 只执行一次平台写；记录 submit checkpoint 和 write attempt 数量，不做隐藏重试。
12. Worker 完成 read-after；记录实际价格、`observed_at`、source 和 evidence reference。无法取得合格 readback 时停止，不宣称成功。
13. Queue Service 中的 Importer 导入 receipt、价格、观察时间和来源；核对 result/checksum/operation/attempt 身份一致。
14. Coordinator 正常关闭 continuation，Task 到明确终态；UNKNOWN 只能沿既有唯一 RECONCILE 收口。
15. Web 展示 Task、continuation、operation/attempt、Importer 终态以及带来源的平台回读。
16. 确认本 attempt 已离开 `inbox/working/results` 活动区，记录 Queue 收尾状态；保留 archive、receipt、checksum 和证据引用。
17. 若负责人决定恢复测试前价格，停止本次验收收尾，另建新的人工决定并重新走完整 Task + Authorization。不得由验收脚本自动恢复。

## 异常停止规则

出现任一情况立即停止推进，不为了 PASS 继续：

- 平台 read-before 与 expected old 不一致；
- 存在旧 active operation、UNKNOWN、未关闭 continuation 或写锁冲突；
- 出现两个 COMMIT 或两个 attempt；
- request、checksum、Task、continuation、batch、operation 或 attempt identity 不一致；
- Runtime、Queue、Worker、Web 指向不同环境；
- Schema / migration / SQLite operational health 不通过；
- Credential provider、登录状态或测试账号范围不能可靠确认；
- 写后没有合格 readback；
- 自然发生 UNKNOWN 且唯一 RECONCILE 尚未收口；
- 出现验证码而现场人工接管人或时限条件不满足；
- 负责人撤销测试窗口或发现业务影响不可接受。

停止后只记录事实，保留 operation / lock / Queue / reconcile 证据。Reviewer 再判断是环境问题、实现缺陷还是需要人工恢复。

## RM1 PASS 证据模板

以下字段只记录非敏感值。不得填入密码、Credential target、完整 Review token URL、Webhook、账号凭据或本地秘密配置。

| 字段 | 现场记录 |
|---|---|
| deployed main SHA | `<待填>` |
| Runtime schema version / health | `<待填>` |
| internal SKU / platform identity | `<待负责人确认>` |
| Task ID | `<待填>` |
| continuation identity | `<待填>` |
| batch / operation / execution attempt identity | `<待填>` |
| human authorization accepted timestamp | `<待填>` |
| Queue Service stop / start timestamp | `<待填>` |
| read-before price / observed_at / source | `<待填>` |
| expected-old compare | `<MATCH 或 STOP>` |
| write attempt count | `<必须为 1>` |
| read-after price / observed_at / source | `<待填>` |
| Importer terminal result | `<待填>` |
| Task / continuation final status | `<待填>` |
| Queue cleanup | `<待填>` |
| result / checksum / evidence reference | `<仅填非敏感引用>` |
| 验证码 / blocker / UNKNOWN / 人工漂移 | `<NONE 或事实记录>` |

只有完整证据经过 Reviewer 判断后，才可更新 Stage Goal；RM0 不作 PASS 宣告。

## 2026-09-08 Blocker Remediation

### 当前结论

`RM0 TECHNICAL READINESS = PASS WITH HISTORICAL DEBT`

B1 的 Runtime v18 物理 schema、B2 的 ShadowBot 源码差异和 B3 的服务生命周期问题已修复。迁移后的完整执行账本重查发现一个此前未覆盖的 `set_online` UNKNOWN 批次，涉及 `AISHA-B/C/D`；2026-09-08 的定向核验确认其中 `AISHA-C-55-Z` 已由旧版正式人工处置流程收口，历史 UNKNOWN 保留，但不再承载自动恢复责任。该对象及其 operation、attempt、lock、receipt 和 history 均未清理或改写。B4 的 12 条商品 mapping 仍全部为 `DISABLED`，测试对象和目标价格未由负责人确认。

因此旧 `WEB7E-6646…` 作为 `LEGACY HISTORICAL UNKNOWN / OPERATIONALLY CLOSED` 永久保留，不再构成全平台 RM1 blocker。RM0 技术准备以历史审计债务通过；RM1 业务授权仍为 **WAIT OWNER**，Stage Goal 继续为 **NOT YET VALIDATED**。本记录不授权进入 RM1。

`REAL PLATFORM WRITE NOT AUTHORIZED / NOT EXECUTED`

### 版本与维护窗口

- 远端 main 仍为 `08f4e70fc2ffcd54de6a247ae48f4804da136056`；整改开始时 PR #49 Head 为 `11ed44da9034324b2d614befa01c9ddadf98bd4e`，相对 main 只有文档差异，`app/`、`scripts/` 和 `shadowbot/` 生产源码与 main 一致。
- 整改前 Operations Web 进程为 0，Queue Service 进程为 0；Worker heartbeat 为历史 `STOPPED`，Worker lock 可获取，活动 execution attempt 为 0。影刀只打开“应用”列表，没有打开编辑器或设计器。
- 本轮未启动 Operations Web；已从上述代码版本启动 Queue Service，并从影刀应用列表启动同步后的 `test2` Worker。

### B1 — Runtime v18 physical schema

处理结果：**schema 子项 CLOSED；遗留执行事实保留为历史审计债务。**

1. 在 Web / Queue Service 均停止且活动 attempt 为 0 时，使用 SQLite online backup 创建一致性备份；备份文件名为 `runtime.before-v18-remediation.sqlite3`，大小 1,343,488 bytes，SHA-256 为 `318f4e19358acdbbe31dca21153de809ad069f603e8348de006b56d9d041939c`。备份 `integrity_check=ok`、FK violation=0，migration 记录完整为 1..18。备份保留在仓库外，不记录完整本地路径。
2. 使用当前 main 的正式入口 `python -m app.cli init-runtime-db --runtime-db <configured-runtime>` 执行修复。没有手工 `CREATE TABLE`，没有删除 v18 migration row，也没有修改或清除历史 Task / operation / attempt / receipt。
3. 正式 `check-runtime-health` 返回 `ok=True`、schema 1..18、`runtime schema v18 healthy`；SQLite operational health 为 WAL、`synchronous=NORMAL`、`foreign_keys=1`、`busy_timeout_ms=5000`。
4. 独立回读确认 `execution_continuations` 存在，9 个 v18 必需列完整；`ix_execution_continuations_open`、授权字段不可变 trigger 和 no-delete trigger 均存在。`integrity_check=ok`，FK violation=0。
5. migration 后账本重查：open continuation=0、STARTING/RUNNING attempt=0、活动 update-price batch=0、NEEDS_RECONCILIATION operation status=0、ACTIVE/UNKNOWN write lock=0；但有 1 个 listing-action batch 仍为 UNKNOWN：

| 对象 | 状态 | 范围 / 事实 |
|---|---|---|
| `WEB7E-6646163b9fc5df85359b2e756b6c02b4` | `UNKNOWN` | 2026-08-30 的 `set_online`；涉及 `AISHA-B-60-Z`、`AISHA-C-55-Z`、`AISHA-D-50-Z` |
| `OP-1c65202b1e2cb1ac5adc8234` | `FAILED / NOT_APPLIED` | `AISHA-B-60-Z` |
| `OP-f2fc27e5d0c546d2746f258e` | `MANUAL_HANDLED`，batch item 仍为 `NEEDS_RECONCILIATION` | `AISHA-C-55-Z`；相关历史 UNKNOWN attempts 保留 |
| `OP-44a2c1659f0dfcf7efa2537f` | `VERIFIED`，batch 仍未收口 | `AISHA-D-50-Z`；历史 UNKNOWN attempt 与后续 VERIFIED attempt 均保留 |

相关 write lock 当前均为 `RELEASED`。batch-level UNKNOWN 不改写，三个 item 按各自历史事实保留；其运营责任是否已关闭见下文定向核验。本轮不自行清理，也不以该历史 batch 全局阻塞其他 SKU。

### B2 — ShadowBot deployed source

处理结果：**CLOSED。**

- 同步前再次确认 Worker 停止、Worker lock 可获取、活动 attempt=0、影刀编辑器未打开。
- 使用既有 `scripts/sync_shadowbot_test2.py --app-dir <configured-app-dir>` 完成同步；没有从影刀目录反向覆盖仓库，没有修改 selector、Credential 或业务 mapping。
- 同步后使用同一脚本的 `--check` 验证：`module1.py`、`vertical_slice_read_price.py`、`shadowbot_queue_worker.py` 及其余 4 个受控文件全部 `CURRENT`，配置文件 `EXISTS`。
- `shadowbot_worker_config.json` 与 `selectorsV2.xml` 的 SHA-256 在同步前后不变。app-dir 只以脱敏 fingerprint `7c13091a…` 记录，完整路径未写入 Git。

### B3 — Queue Service / Worker lifecycle

处理结果：**CLOSED。**

- Queue Service 于 `2026-09-08T02:49:39+08:00` 从当前 main 等价源码启动；heartbeat 为 fresh `RUNNING`，包含 Result Importer、Watchdog、登录验证码监视、Review reminder、Outbox 和 `task_execution_coordinator` 六个组件。
- `test2` Worker 从影刀“应用”列表启动。严格 Worker health 检查通过：heartbeat fresh `RUNNING`，Worker lock 被运行实例持有，heartbeat write failures=0、consecutive failures=0、thread restarts=0。
- 使用既有 `ShadowBotLifecycleStore.write_verified_state()` 在 fresh heartbeat、空 Queue 和实际影刀窗口事实均通过后记录 lifecycle：`recorded_state=RUNNING`、`shadowbot_window_state=RUNNING_VERIFIED`、reason=`RM0_REMEDIATION_VERIFIED_START`。没有删除旧 heartbeat 或 lifecycle 文件。
- Web composition、Queue Service 和 Worker 配置指向同一正式 Queue；Web 与 Queue Service 指向同一正式 Runtime。部署 identity mapping 与正式配置哈希一致，Importer 和 Coordinator 均由该 Queue Service 托管。
- 启动后 `inbox/working/results` 始终为 0，没有投递平台读写请求。8 个历史 pending Review 和 notification outbox 均未发生状态变化，Queue Service 日志没有产生催办、通知或错误事件。
- 上述为 B3 整改窗口的生命周期证据；Worker 后续已按负责人要求停止。该正常停机不撤销 B3 的技术核验证据，但 RM1 若获授权仍须按既有运维入口启动并重新确认 fresh heartbeat。

### B4 — 测试对象候选与 mapping

处理结果：**WAIT OWNER。**

正式产品工作簿、平台 mapping 工作簿和 ShadowBot identity mapping 分别有 12 个对象，哈希仍为 `611a7cce…`、`a47b7c29…`、`24f0dd9f…`。每个 internal SKU 在产品数据和 ShadowBot active identity 中均唯一；12 条 PRODUCT mapping 仍全部 `DISABLED`，本轮没有修改工作簿、immutable mapping 或 SQLite。

当前 Operations Web 的商品映射 read model 明确返回“目录暂不可用”，没有产品级 VERIFIED mapping 的 Web 维护页面。运营权威源仍是 `platform_mappings.xlsx`，由现有编译器执行四状态和唯一性校验。负责人确认具体 platform / account / product identity 后，需在独立受控维护中更新该权威工作簿并重新编译验证；本轮不以直接改 SQLite、伪造 VERIFIED 或历史日志猜测替代。

| internal SKU | 商品名称 | 等级 | 唯一 ShadowBot identity | 当前 mapping | 激活前仍缺 |
|---|---|---|---|---|---|
| `AISHA-A-70-Z` | 艾莎 | A级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `AISHA-B-60-Z` | 艾莎 | B级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `AISHA-C-55-Z` | 艾莎 | C级 | 是 | DISABLED | 负责人确认 platform / account / product identity；历史 UNKNOWN 只作审计保留 |
| `AISHA-D-50-Z` | 艾莎 | D级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `AISHA-E-45-Z` | 艾莎 | E级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `CAPPUCCINO-A-70-Z` | 卡布奇诺 | A级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `CAPPUCCINO-B-60-Z` | 卡布奇诺 | B级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `CAPPUCCINO-C-55-Z` | 卡布奇诺 | C级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `CAPPUCCINO-D-50-Z` | 卡布奇诺 | D级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `CAPPUCCINO-E-45-Z` | 卡布奇诺 | E级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `ZIXIA-0-FG-Z` | 紫霞仙子 | 0级 | 是 | DISABLED | 负责人确认 platform / account / product identity |
| `ZIXIA-B-FG-Z` | 紫霞仙子 | B级 | 是 | DISABLED | 负责人确认 platform / account / product identity |

负责人还需为最终测试对象明确当前实际价格、目标价格和测试时段。本轮不默认选择“艾莎 B级”，也不决定任何真实目标价格。

### 旧 `AISHA-C` UNKNOWN 定向核验

本节只判断旧版人工处置是否已经结束当时的自动恢复责任，不重新执行或以今天的平台状态反推 2026-08-30 的平台副作用。核验以 Runtime 的只读查询、[PR #38](https://github.com/etereath/PRA-project/pull/38)及本机保留的 13.5-7F 历史工作区源码与故障分析、未合并的 [PR #39](https://github.com/etereath/PRA-project/pull/39)和 13.5-7G 计划为边界；不把后来 13.7-1 的 `price_execution_human_resolved`、durable continuation 或 Coordinator 合同反套旧记录。

| 对象 | 只读事实 | 结论 |
|---|---|---|
| source Task | `TASK-MANUAL-994adbff5cb5faee936d7235`；旧任务组 Review 处理后为 `cancelled` | Task 已有终态；旧合同允许人工处置 operation 时保留已取消 Task |
| Review | `fa2c8f09d571`；`manual_review`；`cancelled`；`resolved_by=operations`；`resolved_at=2026-08-30T15:59:53.735236Z` | 不是 `pending`，无 open Review |
| batch item | `ITEM-7e197…`；`operation_result=NEEDS_RECONCILIATION`；listing effect `UNKNOWN` | 历史执行事实保持 UNKNOWN，不改成 VERIFIED / NOT_APPLIED |
| operation | `OP-f2fc27e5d0c546d2746f258e`；`status=MANUAL_HANDLED`；`resolution_status=MANUAL_HANDLED`；`resolved_by=web:admin`；`resolved_at=2026-08-31T10:14:39.586979Z` | 旧版人工处置为合法运营终态 |
| attempts | 1 次 COMMIT 与 1 次 RECONCILE，均以 UNKNOWN 结束；第一次写后无合格 readback，第二次列表刷新失败 | 无 active attempt；不能据此断言平台成功或失败 |
| result / receipt | 初始执行与两次 reconcile 共 3 份 accepted receipt，均已写入账本且无 projection error | 历史输入已落账，不需重放 |
| write lock | 对应 `蚂蚁花团供应商|sku:AISHA-C-55-Z` 的锁为 `RELEASED`，释放时间与 operation 人工处置时间完全一致 | 按旧合同释放，无 active lock |
| 后续责任 | 该旧 v5 batch 无 `execution_continuation`；当前也无关联 active attempt、active lock 或 open Review | 没有当前系统必须继续自动恢复的持久责任 |
| 负责人补充确认 | 负责人于 2026-09-08 确认，其在 2026-08-30 当时已观察到 `AISHA-C-55-Z` 目标商品成功上架 | 与旧正式入口记录的 `TARGET_APPLIED` 一致，进一步证明人工处置实际发生 |

Task status history 保留旧任务组 `cancel_task` 事件，没有后来 13.7-1 才定义的 `price_execution_human_resolved`。前者证明旧 Task 已按当时流程终止，后者的缺失是符合年代边界的预期事实，本轮不补造。

`MANUAL_HANDLED` 来自旧版正式入口而非可见的直接改库：13.5-7F 的管理 Web 以 `HANDLE_REVIEW` 权限调用 `resolve_shadowbot_operation_manually()`；该事务会写入 `web:admin`、将 operation 置为 `MANUAL_HANDLED`、写入 `operations_web_manual_confirmation` execution log，并在同一事务释放 write lock。Runtime 中字段组合、专用日志载荷和完全相同的 resolution / release 时间与这条唯一正式路径一致。旧 7F 测试也明确覆盖“已取消 Task 保持 cancelled，但 operation 与 lock 被人工收口”的情况。

旧界面中操作者选择过 `TARGET_APPLIED`，因此 operation 的旧 `operation_result` 列记录为 `VERIFIED`；负责人现已明确确认该选择源于其当时观察到目标商品成功上架。该确认是有效的旧版运营处置证据，足以支持 `MANUAL_HANDLED` 和恢复责任关闭；但它不是系统留存的、时间绑定且可回读验证的平台 observation。batch item 的 `NEEDS_RECONCILIATION`、两次 UNKNOWN attempt 和缺失合格 readback 均原样保留，所以机器可验证的历史副作用真值仍只定性为 `INDETERMINATE`，不把 item 或 batch 改成 `VERIFIED`。

本机历史材料 `docs/reports/task13_5_7f_automation_queue_failure_analysis_20260831.md` 已记录：旧人工确认能原子解决 operation、释放 write lock 并收口 Review / token / outbox，但系统当时没有持久 owner 推进被阻塞的后续 Task。[13.5-7G Coordinator 计划](https://github.com/etereath/PRA-project/blob/4c9c0acd4b0d73cdc6c4efa4aaf51b7ea1d4e457/docs/plans/task13_5_7g_task_execution_coordinator_plan.md)正是为这个后续责任缺口提出，且 PR #39 未合并。该历史缺口不把已经 `MANUAL_HANDLED` 的原 operation 重新变成待自动恢复对象。

定向核验过程中 Runtime 以 SQLite `mode=ro` 和 `query_only=ON` 打开；核验前后文件大小、修改时间及 SHA-256 `7d217fe8abd892fce0e9697899ca576d15c55ca1fa7a7ba57561feb478fe7c33` 均一致。未调用真实平台 READ_ONLY / WRITE，未修改 Runtime。

### 历史分类与 SKU 隔离

```text
Historical execution fact:
NEEDS_RECONCILIATION / historical UNKNOWN

Legacy operational disposition:
MANUAL_HANDLED

Historical recovery responsibility:
CLOSED under legacy manual-resolution workflow

Historical side-effect truth:
INDETERMINATE
```

负责人对当时平台状态的运营确认是 `TARGET_APPLIED / 成功上架`；上述 `INDETERMINATE` 专指缺少合格系统 readback 时不可升级的机器可验证历史事实层，两者不互相覆盖。

`WEB7E-6646…` 可永久保留 batch-level UNKNOWN：`AISHA-B-60-Z` 保持 `FAILED / NOT_APPLIED`，`AISHA-C-55-Z` 保持 historical UNKNOWN + `MANUAL_HANDLED`，`AISHA-D-50-Z` 保持 `VERIFIED`；不要求重写 batch 聚合结果。历史 UNKNOWN 不成为全平台或其他 SKU 的 RM1 blocker。当前 SKU 是否允许新任务，仅按该 SKU 当前的 active lock、open Review、新鲜平台事实、mapping、Task 和新授权判断；不得对原 operation 再做自动 RECONCILE 或重放原 `SET_ONLINE`。

### Remediation 后 RM0 Gate

| Gate | 结果 | 说明 |
|---|---|---|
| Runtime schema / SQLite health | PASS | v18 物理结构、health、integrity、FK 均通过 |
| Runtime execution ledger | PASS WITH HISTORICAL DEBT | 旧 `WEB7E-6646…` 保留 batch-level UNKNOWN；AISHA-C 的旧人工处置已关闭恢复责任，无活动责任或当前阻塞对象 |
| ShadowBot deployed source | PASS | 所有受控文件 CURRENT；配置和 selector 未改 |
| Lifecycle / environment alignment | PASS | Queue Service、Worker、lifecycle fresh/一致；Web 配置、Importer、Coordinator 对齐正式 Runtime/Queue |
| Mapping / test object | WAIT OWNER | 12 条 mapping 均 DISABLED；负责人尚未确认测试对象 |
| Queue active directories | PASS | `inbox/working/results` 均为 0 |
| Credential / security | PASS | 当前 Worker 用户上下文可读取 Generic Credential；只记录非空布尔结果，无 secret、target、username、password、token、Webhook 或完整私密路径进入 Git/报告 |

`RM0 TECHNICAL READINESS = PASS WITH HISTORICAL DEBT`。`RM1 BUSINESS AUTHORIZATION = WAIT OWNER`。下一步由负责人确认一个满足当前 SKU 隔离门禁的测试对象、platform、account、product identity、当前价、目标价和时段，并决定 B4 mapping 的受控维护；在负责人明确授权前不得创建或授权真实 UPDATE_PRICE，不得进入 RM1，也不得开始 13.7-2。Stage Goal 保持 **NOT YET VALIDATED**。
