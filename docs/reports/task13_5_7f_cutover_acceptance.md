# 任务 13.5-7F：系统维护、切换删除与运营验收报告

- 实施日期：2026-08-13
- Review Profile：`R3`；真实平台发布继续沿用既有 `R4` 门禁
- 分支：`codex/task13-5-7f-cutover-acceptance`
- 基线：`e27046b`（7E PR #35 合并提交）

## 1. 结论

7F 已把新运营 Web 收敛为仓库唯一 Web 实现，删除旧 `app.web`、旧样式和重复测试，
同时补齐系统运行状态、通知、数据与备份、高级诊断四个分区。系统维护 POST 只接受固定
类型化意图，不接受脚本、命令、SQL、路径或 Queue 内容，也不在 Web 请求内启动 Worker、
发送通知、执行备份或等待长任务。

初始 7F 分支没有迁移或修复真实 Runtime DB，没有启动或重启真实 Worker，没有投递真实
Queue，没有发送真实飞书，也没有执行真实平台读写动作。2026-08-14 的合并后外部验收已
补做独立后台生命周期和真实平台 READ_ONLY，详见第 8 节；真实飞书仍需要独立通知后台服务
具有新鲜心跳，真实平台写验收仍需用户另行明确商品、动作和批次授权。

旧库数据已确认为测试时期数据，不再要求完整迁移。7F 收尾新增受控干净重建入口：完整
归档旧库以便追溯。2026-08-15 的后续整改把候选结构提升到 v18：商品和映射工作簿仅在
准备阶段作为一次性导入，候选库保存商品目录和平台映射；正式 Web、人工任务、执行授权和
桌面/手机复核不再读取 XLSX。候选 v18 库仍必须通过真实空 `OPEN` 订单快照和既有库存
bootstrap，不能由脚本伪造经营事实，也尚未替换 canonical 真实库。

## 2. 复用与新增矩阵

| 能力 | 分类 | 7F 处理 |
| --- | --- | --- |
| Worker 健康与恢复 | 原样复用 | 读取既有 Worker health；异常进入既有 `WORKER_UNAVAILABLE` Incident 和恢复 Automation |
| 通知测试 | 原样复用 | 只写既有 Notification Outbox；由独立 Notification Worker 实际发送 |
| 运行备份 | 参数化复用 | 薄 Automation Handler 调既有 `release_backup.py` 创建与回读验证，不复制备份逻辑 |
| Automation Run、租约、完成接口 | 原样复用 | 新增 `MANUAL_ONLY` 固定 Job，只能由类型化维护意图创建 Run |
| 系统状态 | 公共抽取 | 聚合 Web、Runtime、工作簿、Automation、Queue、Worker、Importer、Outbox 和备份状态 |
| 旧 Web | 删除 | 删除旧 Route、HTML 拼接、样式与重复测试；打包门禁禁止其回流 |
| CLI | 保留 | 继续承担测试、Mock、诊断、备份和恢复，不恢复日常运营旁路 |
| 旧测试 Runtime 归档 | 参数化复用 | SQLite 在线备份和逻辑快照回读；允许归档不健康旧库但不称为发布备份 |
| 干净 v18 候选库 | 参数化复用 | 复用 v17 库存合同并新增数据库商品目录/映射；真实订单空快照、库存 bootstrap 与逐 SKU 回读不变 |
| 激活/紧急回滚 | 确需新增 | 只编排固定路径、双哈希、确认文本、同盘替换和失败恢复，不复制库存逻辑 |

## 3. 类型化维护与权限

`SYSTEM_ADMIN` 与只读 `VIEW_SYSTEM` 分离。普通系统查看者只能查看运行状态；通知测试、
受控备份和高级诊断均要求管理员能力。所有维护 POST 继续要求 Session、CSRF、确认和幂等键。

- Worker 恢复先读取既有健康报告；健康时零写返回，无健康证据时才创建 Incident 和既有
  Automation Run。Automation Service 必须有 30 秒内 `RUNNING` 心跳并明确注册恢复 Handler。
- 通知测试只创建 `system_test` Outbox；Queue Service 必须有 30 秒内 `RUNNING` 心跳，且
  通知 Worker 已启用、通道与 Web 启动配置一致。
- 备份只创建 `RELEASE_BACKUP_MAINTENANCE` Run；Automation Service 必须在启动时固定
  wheel 和备份目录并注册 Handler。Handler 要求回执绑定同一 Run ID 且回读验证成功。

后台载体缺失、过期、身份或通道不一致时，请求在业务写入前拒绝。Web 重启不启停 Queue、
Worker 或 Automation；`start_local.ps1` 与 `start_local_services.ps1` 继续独立。

## 4. 唯一 Web 与界面验收

`serve-web` 是唯一运营 Web 入口。旧 `app/web.py`、`app/web_styles.py`、旧 Web 测试已删除；
wheel/sdist 审计会显式拒绝重新包含旧模块。系统页不显示 secret、完整 token、webhook 或本地
绝对路径。

内置浏览器使用真实 Runtime DB 的只读页面完成桌面和 `390×844` 手机验收：四个一级入口、
系统四分区和业务管理弹窗均无整页横向溢出。验收中发现并修复：

- HTML `hidden` 被表单 CSS 覆盖，导致平台目标库存误显示；现在全局 `[hidden]` 强制隐藏；
- 静态资源一小时强缓存可能让发布后浏览器继续使用旧交互；现在资源 URL 带 7F 版本并要求
  `no-cache` 重验证；
- 全部平台映射停用时任务弹窗没有选项却可预览；现在显示中文原因并禁用预览。

复验确认：调整价格只显示目标价格；下架不显示价格或库存；上架显示上架价格和平台目标
库存；无可用平台时不能提交预览。

## 5. 真实 Runtime DB 只读验收

受控脚本固定读取真实 Runtime DB、三份工作簿和真实 Queue 根目录，对 `/today`、
`/database`、`/database/project`、`/database/quality`、`/management`、`/system` 发起
已认证 GET。结果：六页均为 `200`，主数据库大小、修改时间与 SHA-256 不变，WAL 内容不变，
预热后的 SQLite 侧车内容不变，`platform_write_performed=false`。

SQLite 只读连接第一次建立 WAL 共享内存时可能更新 `-shm` 锁元数据，因此验收先预热再比较
侧车内容；这不等于业务数据库写入。真实库既有健康问题仍使 `/health` 返回 `503`，页面只
报告不可用。当次 GET 验收没有推断违规来源，也没有初始化、迁移或修复真实数据。

后续只读追溯已查明该外键违规来源：2026-07-31 的历史验收清理直接删除了
`T1354-ACCEPT-FULL` Run/Job，却遗漏其 `automation_run_events`，且直接 SQLite 连接没有启用
外键约束，因而遗留事件 `AUTO-EVENT-1205ba4fd0464ad08d629529081a42f6`。这是测试清理缺陷，
不是正常 Automation 事务或平台错误；旧库保持原样归档，新库不迁入该事件。

2026-08-13 又使用新增维护入口对 canonical 真实库执行了一次不带 `--apply` 的只读预览：
识别到 12 个正式 SKU、库存合计 148 扎、12 条平台映射均为 `DISABLED`；SQLite 完整性为
`ok`、Schema 为 v14、外键违规为 1 条。预览没有创建切换工作目录，主库和 WAL 的 SHA-256、
大小及修改时间在调用前后完全一致。没有准备候选库、bootstrap、替换或回滚真实库。

## 6. 测试与制品

- 最终直接专项：`77 passed, 3 subtests passed`；
- 干净 v17 准备、命令行 UTF-8、激活失败恢复与紧急回滚专项：`5 passed`；
- Windows 陈旧路径修复后的对应测试组：`51 passed`；
- 完整 pytest：`1220 passed, 3 skipped, 82 subtests passed`，耗时 286.15 秒；
- 系统冒烟：`16 passed, 0 failed`，使用临时数据库和 mock 通知；
- 本次新增 Python 文件 Ruff/格式检查、`git diff --check`、`compileall`：通过；全仓 Ruff
  仍报告 67 项既有告警，本轮没有借机改动无关核心代码；
- wheel/sdist 构建、allowlist、secret scan、仓库外 wheel 安装：通过；
- Windows ShadowBot fixture 与失败退出码：通过；
- wheel/sdist 均不包含旧 Web 模块。

Linux/Windows CI 由 Draft PR 执行；本地 Windows 结果不能替代 GitHub Actions。

## 7. 未关闭的外部验收门禁

以下不是代码缺口，但在外部条件满足前不能宣称 13.5-7 全部运营验收完成：

1. 真实飞书：当前没有经本分支验证的新鲜 Queue Service/Notification Worker 心跳，因此 Web
   会拒绝制造“已发送”假象；需在独立服务运行后由管理员发起一次通知测试并确认手机收到。
2. 真实平台写：本轮没有用户指定商品和批次授权，未执行 COMMIT。后续授权后必须完整通过
   Queue → Worker → Importer → Archive 和既有 UNKNOWN/RECONCILE 门禁。
3. 真实 Runtime 健康：既有外键违规另走显式维护、备份与回读流程；7F 不修复真实数据。

上述第 3 项现已有 `scripts/clean_runtime_cutover.py` 和
`docs/clean_runtime_v17_rebuild.md` 的受控实施路径，但本分支仍未对 canonical 真实库执行
准备、bootstrap 或激活。真实执行必须另取维护窗口和用户明确授权。

## 8. 2026-08-14 外部生命周期与真实 READ_ONLY

本次使用仓库外一次性健康 v17 Runtime DB，启动独立 Queue Service、Automation Service
和验收 Web（`127.0.0.1:8766`）。Web `/health` 首次返回 200；停止 Web 后 Queue 周期从
61 增至 65，Automation 心跳继续前进且两服务保持 `RUNNING`；重新启动 Web 后 `/health`
再次返回 200。由此确认 Web 重启不启停 Queue、Worker 或 Automation。验收完成后，隔离
Web、Queue Service 和 Automation Service 进程全部停止，端口 8766 不再监听，避免它们以
验收数据库继续观察真实 Queue；常驻影刀 Worker 独立保持运行。

首次真实订单 READ_ONLY 暴露两个执行端问题并在本分支修复：

- 日期 `2026-08-14` 在订单页可见，但日期读取仍沿用商品页文本选择器路径，返回
  `ORDER_DATE_NOT_READABLE`。现在从已捕获的订单行选择器截取到订单 Page-Frame 文档根，
  只枚举该订单页的 `StaticText`；不使用 OCR、屏幕坐标或订单号。
- 同步后的 `emergency_offline_fence.py` 在影刀宿主导入 `app.services`，导致 Worker 启动时报
  `No module named emergency_offline_fence`。listing review gate 的无副作用查询与阻断判断
  已下沉到同一份标准库依赖 fence 模块，业务侧和影刀侧复用同一实现；独立 `-I` 导入测试
  防止再次把项目包依赖带入宿主。

最终验收固定目标日期 `2026-08-14`，常驻 Watchdog、Worker、Order Importer 与 Archive
绑定同一个一次性 v17 Runtime DB。为消除验收审计与 Worker 领取的轮询竞态，仅在本机未
跟踪配置中临时把 Worker poll 从 3 秒调整为 10 秒，使 Watchdog 先输出与 Automation Run、
目标日期精确绑定的 `READY_REQUEST_VALIDATED`；验收后原配置已完整恢复为 3 秒并重启。
最终结果为：

- `trade_day_status=OPEN`；
- 读取 7 条原始订单观察，`scope_complete=true`、`end_marker_verified=true`；
- 批次为 `PARTIAL`，原因是该一次性验收不导入真实商品映射；这不影响页面能力、范围完整性
  或零副作用门禁；
- `watchdog_validated=true`、`result_imported=true`、`result_archived=true`；
- `inbox/working/results` 均为 0，`platform_write_operations=0`；
- 本机配置恢复后 Worker 严格健康检查通过，生命周期记录更新为 `RUNNING`。

修复后的订单、Worker、授权栅栏和直接依赖专项为 `121 passed`。最终完整 pytest 为
`1229 passed, 3 skipped, 82 subtests passed`，耗时 352.52 秒；隔离系统冒烟为
`16 passed, 0 failed`，使用 mock 通知且未发送真实飞书。源码编译、`git diff --check`、
仓库与影刀部署文件 SHA-256 一致性以及含中文文档的严格 UTF-8 回读均通过。

本次没有提交任何真实平台写请求。COMMIT 仍停在“用户明确商品、动作和批次授权”门禁前；
真实飞书与真实 Runtime v18 canonical 切换也仍分别按第 7 节处理。

## 9. 2026-08-15 Runtime 主数据后续整改

人工验收发现 Web 创建真实任务仍需读取 `products.xlsx` 和
`platform_mappings.xlsx`。这会让正式任务受 Excel 路径、占用和文件漂移影响，因此新增
Runtime Schema v18 的 `product_catalog` 与 `platform_product_mappings`，并完成以下收口：

- Web Composition Root 删除商品和映射工作簿依赖；
- 今日/数据库/业务管理商品回读改为数据库商品目录联接数据库库存；
- 人工任务预览/创建和真实执行授权绑定同一数据库商品、映射、库存和平台事实；
- `DAILY_TASK_GENERATION`、`ORDER_SCAN`、Queue/Executor/Importer 均改读数据库商品或映射
  快照，不再把工作簿作为正式运行前提；
- 桌面与手机紧急复核的基础成本改为数据库版本快照；
- 干净重建只在 `prepare` 一次性导入 XLSX，`verify/activate` 只回读候选库和切换清单。

库存未复制到商品目录，仍只有 `inventory_balances` 是真实库存权威；ShadowBot 身份 JSON
仍是既有平台定位配置，不属于业务 XLSX。旧工作簿读取代码仅保留给一次性导入、测试、
诊断和明确隔离的旧 CLI，不属于正式 Runtime 控制面。订单/Automation/Queue/紧急保护专项
组合通过；最终完整 pytest 为 `1231 passed, 3 skipped, 82 subtests passed`，耗时 325.96 秒；
隔离系统冒烟为 `16 passed, 0 failed`。
本次未迁移 canonical 真实库、未启动 Worker、未投递 Queue、未执行平台读写；真实 v18
切换和后续 Web 写验收仍需新的维护窗口与单独商品/动作/批次授权。

随后按人工验收发现的同类遗留问题继续完成正式运行收口：

- 所有 Web、Automation、Queue、Executor、旧规则应用和过期请求修复入口，在执行前只做
  `require_current_schema()` 精确健康检查；数据库创建和迁移只允许显式维护入口执行；
- 业务管理增加商品资料与平台商品对应关系维护，使用版本号、幂等请求号和同事务库存零余额
  初始化；未指定对应关系编号时从请求来源生成稳定编号，重复提交不会新增第二条记录；
- 正式执行身份只接受 JSON；生产执行 CLI 和旧规则 `--apply` 默认关闭，仅能通过显式恢复
  开关、原因和固定确认文本进入；CLI 继续保留开发测试入口；
- 发布备份不再把退役的商品/映射 XLSX 当作正式运行输入；价格和上下架规则工作簿仍保留，
  但启动时解析校验，生产环境拒绝样板路径；
- 每日任务生成输入清单新增商品、库存、目标平台状态和两份规则的快照摘要；持久化前再次核对
  可变库存与平台状态，变化时整批失败，不生成基于混合时点的任务；
- 商品/映射 XLSX 读取只保留在一次性重建导入、样板、E2E/Mock 和显式隔离的旧实验入口。

最终受影响专项为 `174 passed, 21 subtests passed`；完整 pytest 为
`1240 passed, 3 skipped, 82 subtests passed`，耗时 394.82 秒；隔离系统冒烟为
`16 passed, 0 failed`，使用临时数据库和 mock 通知。`compileall`、`git diff --check` 与
全部变更文本的严格 UTF-8 回读通过；当前 Python 环境没有安装 Ruff，因此本次没有宣称
Ruff 通过。整改期间未读取或修改 canonical 真实库，未启动或停止 Worker，未投递 Queue，
未发送真实飞书，也未访问或写入真实平台。

## 10. 2026-08-16 测试责任去重

本轮先按生产责任层复核 13.5-7 相关测试。旧 Web 的两个大型测试文件已随 7F 删除；当前
服务、HTTP、数据库和执行端之间名称相似的测试多数分别承担安全边界，不能仅为了降低数量
继续删除。本轮只归并以下同一责任内的重复测试项：

- 配置对象密码脱敏归入会话登录合同；
- 已登录访问登录页归入同一会话生命周期合同；
- 一级只读路由拒绝 POST 归入公共路由合同；
- 本地资源约束归入静态资源发布合同；
- 系统页只显示当前状态归入运营页面文案合同；
- 未知内部标识的中文回退归入同一运营文案合同。

上述原断言全部保留，只删除 6 个独立测试函数及其重复 fixture。没有删除 CSRF、权限、
Schema 精确健康、幂等冲突、事务回滚、Queue/Worker/Importer、执行授权或真实写门禁。
pytest 收集数从 1243 降至 1237；Web 专项为 `59 passed`；完整回归为
`1234 passed, 3 skipped, 82 subtests passed`，耗时 439.57 秒；隔离系统冒烟为
`16 passed, 0 failed`。全量耗时没有随 6 项去重显著下降，因此后续性能治理应依赖测试分层
和耗时分析，而不是继续删除安全测试。本轮没有修改生产代码，也没有访问真实 Runtime、
Worker、Queue、飞书或平台。
