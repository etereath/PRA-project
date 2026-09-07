# Task 13.7-1：人工改价实现与隔离验证

角色：Implementation / Validation Evidence；日期：2026-09-07。对应 [PR #47](https://github.com/etereath/PRA-project/pull/47)，保持 Draft，未合并、未部署、未操作真实平台。

## 版本与结论

后续状态说明（2026-09-07）：本报告保留 `8a1040d` 首版实现及开发自检快照。[正式首审](https://github.com/etereath/PRA-project/pull/47#pullrequestreview-5126336815)对交付 Head `ceb88a0` 判定 Implementation FAIL，发现 P1-47-01 人工终态入口缺失和两项非阻塞 P2，取代本报告“未发现未处理 P1/P2”的开发自检判断。后续修复、人工结论记录和超时策略见[P1 修复报告](task13_7_1_p1_human_resolution_20260907.md)；以下历史测试不代替修复版本证据。

- 实现源码与测试：`8a1040da308caf6f2ccc60fda7f18b9b2237be24`。本报告之后的文档提交不改变该源码；PR 说明另记录最终 Head 的 CI。
- 承接 Head：`3be9f15f11298e1cee06f706d117c95afdb3f7aa`；已合并 main 起点：`f227cd2517687e4a6dfadea90c2e126a5da69711`。承接时核验了正文、评论、提交、差异和 Windows/Linux CI，没有新增外部审核意见。
- 分支：`codex/task13-7-1-human-update-price`。独立 worktree 施工，保留原 main 及其本地改动。
- Task Type：Integration；Review Profile：R4，涉及授权后的跨模块责任与恢复，同时包含 R3 授权、迁移、UNKNOWN 边界。

| 结论维度 | 本次结果 | 证据范围与后续责任 |
|---|---|---|
| 代码实现 | COMPLETE / 待审核 | 正式 Web、持久授权、既有 Queue Service、v4、Worker、Importer、结果详情已连接 |
| Implementation Review | 开发者自检：未发现未处理 P1/P2；正式审核未完成 | 不冒充独立审查。ChatGPT 按本实现 SHA 首审并冻结完整 blocker |
| Merge Gate | NOT RELEASED | 最终 Head CI、正式审核及负责人决定分别核验；本报告不授权合并 |
| 隔离业务旅程 | PASS | 同一临时 Runtime 和 file Queue，正式链路，仅替换外部 UI/平台边界 |
| Stage Goal Validation | NOT YET VALIDATED | 缺少包含重启或 blocker 恢复的受控真实平台旅程，由负责人主持 |
| 部署 / 实机 / 长期运行 | NOT YET VALIDATED | 没有使用生产 Runtime，没有启动真实影刀窗口或 Worker |

实现范围为有限期、一次人工 UPDATE_PRICE。Exposure、Supply、Commitment、Closing、Observation Health、库存 authority 切换与自动销售 Agent 均未进入本次施工；纯改价没有新增库存扣减或计划生成。

## 复用选择与正式入口

**最新决定仍保存在 Task。** `ManualTaskApplicationService.create` 在原事务内保存 `origin=MANUAL`、`web-manual:` 来源、目标、TTL、幂等身份和 history。`decision_trace` 记录价格决定版本及 predecessor Task。开放旧任务不再阻止新有效价格决定入库：尚未发布的人工价格决定原子取消/替代；已发布或存在未决 operation 的旧决定保留，新的 Task 等待旧操作收口。原索引已有请求身份，无须另建 Intent 表或删除去重约束。

**最终授权是独立、不可变的持久接受记录。** v4 的 PREPARED 由预览就能创建，不能代表授权；原 AUTH history 不包含恢复所需的完整事实与执行配置，并且与 publish 分属两个提交边界。Schema v18 因此增加一张附属于已有 batch 的 `execution_continuations` 表：封存主体、capability、精确 Task/manifest/facts、确认摘要、幂等哈希、有效期及配置绑定。接受记录与 AUTH history 同一 SQLite 事务提交；主体/幂等唯一约束、授权字段不可变触发器及禁止删除触发器保护证据。没有第二套 Task、Attempt 或执行状态机；进度只是现有账本的交接投影。

**继续执行由既有 Queue Service 负责。** `scripts/run_shadowbot_queue_services.py` 在原单实例锁内创建 `TaskExecutionCoordinator`，每轮完成 Importer、Watchdog、Review、Outbox 后处理尚未关闭的 continuation。Web submit 只在事务接受后返回“执行授权已接受”，不再承担 v4 Queue 发布。Coordinator 不扫描所有 PENDING/PREPARED；发布前经原 authorization 重验与 v4 manifest 重建，再由原 publisher 的 `BEGIN IMMEDIATE` 和 batch 状态竞争发布权。

```text
POST /management/tasks/preview → /create
  → ManualTaskApplicationService → Task / history
POST /management/executions/prepare → /submit
  → ExecutionAuthorizationApplicationService → execution_continuations + AUTH
现有 Queue Service → TaskExecutionCoordinator
  → publish_task_commit_batch → file Queue → 现有 QueueWorker / 平台流程
  → ShadowBotResultImporter / Watchdog → batch / operation / attempt / receipt
GET /management/task/<task_id> → 授权进度、责任方、实际回读及来源
```

原 v5 授权/执行路线保持原合同。移除了 Web v4 的即时 publish 路径，未并行保留另一条自动执行入口。Operations Web 包导出改为延迟加载，避免 Queue Service 引入 authorization 时经包初始化产生循环依赖；公共导出名称保持兼容。

## 等待、失败和收尾

| 持久位置/情形 | 责任方与触发 | 明确出口 |
|---|---|---|
| Task / 未最终确认的 PREPARED | 人工通过 Web 预览确认 | 未授权不执行；Web 重启后重新预览；允许取消、替代或按 TTL 拒绝执行 |
| ACCEPTED / BLOCKED | Queue Service 周期检查真实 UI 占用、Review、写锁与 predecessor | 授权仍有效且事实匹配则继续；过期或事实改变交回人 |
| RECONFIRM | 人工从任务详情重新预览确认，或取消/形成新决定 | 旧授权关闭；必要时基于新鲜回读重建 expected_old_price 并记 history，新授权才可执行 |
| EXPIRED / SUPERSEDED | 持久终止本次 one-shot | 不再维护旧目标，不自动重授权 |
| PUBLISHING / QUEUED / RUNNING | 原 v4、Worker、Importer、Watchdog；Coordinator 跟踪同一身份 | 校验 ready/working 的校验和及 batch/attempt/instruction；已有请求不再投递 |
| 发布证据缺失超过 30 秒 | 既有 quarantine / fencing | 进入 UNKNOWN，沿唯一 RECONCILE 检查，不能推断“没有执行过”后再 COMMIT |
| UNKNOWN | `ShadowBotExecutor.ensure_reconcile_attempt` 的既有唯一只读对账 | VERIFIED 正常完成；NOT_APPLIED 明确终止旧 Task 为 skipped，后续决定仍需授权 |
| 唯一对账失败、人工处置或配置不再匹配已发布执行 | 管理员检查详情中的 operation、唯一 attempt 及原证据 | 保留原锁/记录，显示 HUMAN；不新建第二对账或猜测写入。单独的 MANUAL_HANDLED 备注不被视作已取得平台事实 |
| 目标已由外部人工完成 | 新鲜、带来源的价格观察 | ALREADY_APPLIED，Task skipped，history 保存观察证据，零平台写入 |
| 正常结果 | Importer / receipt，Web 读取 Runtime | 展示回读价格、时间、来源 attempt 与结果凭据；关闭 continuation |

授权有效期取确认预览后 10 分钟与 Task TTL 的较早值；请求有效期同样不越过授权。价格事实须有来源、观察时间且在 30 分钟以内。价格漂移不会触发旧 one-shot 自动写回。已接受的授权重放绑定同一主体、幂等身份、确认摘要和 Task 集合。

取消/替代按完整授权批次处理：若取消多项授权中的一项，不修改原 manifest 的精确范围；其他待执行决定返回 RECONFIRM，人工重新选择并授权。只有尚未发布、没有未决 operation 的人工价格决定可取消。

单个 continuation 的普通业务异常记录 RETRY_PENDING，其他对象继续；Coordinator 整体普通异常不阻断下一轮 Importer/Watchdog/Review/Outbox。根本 SQLite/schema 故障允许宿主明确 FAILED，不伪装正常。

价格与价格来源、观察时间现在一起导入。迟到的 COMMIT/RECONCILE 不能覆盖更新的价格观察；UNKNOWN 保留旧价格及其来源，无效或缺失的对账价格不能冒充新鲜平台事实。Task 曾完成的回执与后来外部修改后的当前价格是两个事实，详情保留原回执。

人工处置失败分支没有本次实机证据；本报告不把 HUMAN、保留锁或一条管理员备注算成业务完成。它由管理员继续处理原 operation/对账，不能靠清锁、删队列或重发旧授权收口。

## 验证证据与复现

正式旅程用 [test_human_price_journey.py](../../tests/test_human_price_journey.py)，授权与迁移用 [test_execution_authorization.py](../../tests/test_execution_authorization.py)、[test_runtime_schema_v18.py](../../tests/test_runtime_schema_v18.py)。Windows 旅程加载完整生产平台流程，只移除 xbot 导入并替换窗口、扫描/读取、点击、等待和截图的最底层 UI 函数；授权、publisher、file Queue、Worker、phase/result 序列化、receipt 和 Importer 均执行真实代码。故障注入测试在待证明的事务/发布边界抛出异常或 SystemExit，再重建服务；不是对真实操作再制造事故。

| 验收组 | 隔离证据 |
|---|---|
| 正常闭环 | 正式 WSGI 登录/CSRF、人工创建/确认，真实 Queue Service 装配、Worker、Importer，重建 Web 后显示来源回读 |
| 未授权 | 仅 Task/PREPARED 后重建服务，Queue 仍为空 |
| 最终确认边界 | 事务提交前故障时 AUTH/continuation 同回滚；提交后退出时重放接受结果并继续一次 |
| 发布边界 | 发布前后退出分别检查缺失证据与已有请求，最多一个 COMMIT；缺失证据经真实只读 RECONCILE 得出 NOT_APPLIED |
| blocker | 真实 automation UI lease 阻塞；解除并重建服务后同一授权继续执行 |
| 决定变化 | 未发布替代；旧已发布/UNKNOWN 时新决定先保存；旧收口、回读后重新确认 correction；取消批次单项不遗失其他决定 |
| 漂移/终止 | 外部改价交人重确认、目标已完成零写、授权过期、不能取消已发布或未决 operation |
| UNKNOWN | 模拟点击后未知，正式 Worker/Importer 后只出现 COMMIT + 唯一 RECONCILE；只读验证目标后完成 |
| 数据与隔离 | 无效对账价格不刷新事实；迟到结果保留更新价格与来源；单对象/Coordinator 异常不阻断其他组件 |
| 迁移 | v17→v18 幂等、无历史授权收养、不改变库存 authority；中途失败整体回滚；不可变触发器/健康门禁 |

固定实现版本的针对性命令：

```powershell
$env:PYTHONIOENCODING = "utf-8"
python -m pytest -q tests/test_human_price_journey.py tests/test_shadowbot_commit_pipeline.py tests/test_execution_authorization.py tests/test_runtime_schema_v18.py --tb=short -x
```

结果：**56 passed，50.18 秒**。同一源码 SHA 的完整本地回归 `python -m pytest -q`：**1255 passed、3 skipped、82 subtests passed，344.48 秒**。JUnit XML 已用显式 UTF-8 解码和 XML 解析核验。此前开发中间态的测试数不代替此版本。Windows 正式 Worker 锁依赖 `msvcrt`，Linux 明确跳过该 Windows 旅程；既有 Linux Core 范围与 Windows 全量 CI 继续保留，最终 Head 的 CI 结果在 PR 说明中绑定，不以历史 CI 代替。

开发期间曾复现回读价格与来源不一致：同秒但精度不同的观察暴露出旧 v4 导入先改价格、后保留来源的问题。已将价格与来源/时间一起更新，并加入迟到结果保留更新外部价格的回归；本节通过结果覆盖修复后的版本。

本地解释器：`C:\Users\etere\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`，Python 3.11.15；PowerShell 7.6.5 Core。版本来自实际检查，没有假定宿主名称代表版本。测试只使用临时 Runtime/Queue，未读取生产凭据或本地生产配置。

## Schema v18 与运行说明

以下为后续受控部署步骤，本次没有执行。沿用[现有发行物部署](../core_wheel_shadowbot_deployment.md)和[Queue 运维](../shadowbot_file_queue_operations.md)，不增加新 daemon。

1. 负责人安排维护窗口，确认待部署 SHA；停止 Web、Queue Service 与 Worker，按既有 SQLite 备份流程保存一致性快照及队列/执行证据。数据库、工作簿或配置被占用时先解决占用，不覆盖重试。
2. 用同一受控 Python 环境部署本版本，再执行 `python -m app.cli init-runtime-db --runtime-db "<runtime-db>"` 和 `python -m app.cli check-runtime-health --runtime-db "<runtime-db>"`，确认 v18 与完整性门禁通过。迁移仅建授权附表/约束，不把历史 Task、AUTH 日志或 PREPARED 收养成可执行授权。
3. Web 与 Queue Service 使用同一 Runtime、产品/映射文件、queue root、profile、applet URI。已发布对象配置不匹配时要求管理员检查原执行；未发布对象配置变化返回 RECONFIRM，不悄悄转投新队列。
4. 复用 `scripts/start_local.ps1` 与 `scripts/start_local_services.ps1 -RuntimeDb "<runtime-db>"`。后者的显式 RuntimeDb 参数优先于环境变量，务必匹配 Web；它会加载本地 `scripts/local_env.ps1`。Worker 仍按已有有界长驻流程启动。服务启动后会推进仍有效的已接受授权，部署启动必须纳入现场执行安排。
5. 检查原 Queue Service heartbeat 的 `components` 含 `task_execution_coordinator`，以及任务详情中的授权批次、进度/责任方、回读和结果凭据。不要以 heartbeat 正常代替单任务完成证据。

| 配置名称 | 使用要求 |
|---|---|
| `PRA_ENV` | 与 Web 相同，显式 development 或 production |
| `PRA_RUNTIME_DB` / `--runtime-db` | 同一 Runtime；正式启动脚本显式传参时参数优先 |
| `PRA_PRODUCTS_WORKBOOK` / `--products` | 同一产品和成本资料 |
| `PRA_PLATFORM_MAPPINGS_WORKBOOK` | 同一业务平台映射 |
| `PRA_SHADOWBOT_IDENTITY_MAPPING` | 同一执行端商品身份映射 |
| `SHADOWBOT_QUEUE_DIR` / `--queue-dir` | 同一队列绝对位置；执行脚本同时保持旧 alias 一致 |
| `SHADOWBOT_RUNNER_TYPE` | 现有 filequeue 路线 |
| `SHADOWBOT_APPLET_URI` | 与 Web 相同；真实值留在本地配置，授权只保存其摘要 |

不支持通过删附表降级。尚无真实执行时可在维护窗口按一致性备份恢复匹配的代码/数据库；已经发布或结果未知时，先由负责人处理执行与对账证据，不能恢复旧快照抹去授权/执行事实。

## 编码与证据边界

源码、Markdown、JSON 使用显式 UTF-8；写后严格解码回读、Python AST/JSON 解析、首行及中文样例检查。没有新建 CSV/TSV 或 bat。本次代码提交前 19 个源码/测试文件通过 UTF-8/AST 检查；交付文档严格回读，69 个相对链接检查通过。另回读两份测试工作簿的完整表头和各两行中文样例，并解析验证身份映射 JSON 的中文字段。Queue 请求/结果的 checksum 检查由正式序列化和读取链执行。

文件编码正确、终端能显示中文、隔离业务运行成功分别判断：文件验收依据 UTF-8 回读和字段断言，控制台不作为数据真值；测试成功仅证明上述隔离范围。没有残留真实影刀运行窗口或本次未归档的生产执行；实机、现场重启与长期运行仍待负责人安排。
