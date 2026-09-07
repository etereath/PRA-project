# Task 13.7-1 RM0：受控真实平台验收准备

角色：Operations / Real-Machine Acceptance Preparation；检查时间：2026-09-07T21:55:00+08:00。对应已合并 PR [#47](https://github.com/etereath/PRA-project/pull/47)。本记录只覆盖 RM0，不证明部署或真实平台纵向旅程已通过。

## 结论与边界

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
