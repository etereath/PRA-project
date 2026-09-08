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
桌面/手机复核不再读取 XLSX。候选 v18 库必须通过真实订单观察和既有库存 bootstrap，
不能由脚本伪造经营事实。2026-08-16 已完成 canonical 激活，最终证据见第 12 节。

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

## 11. 2026-08-16 真实 v18 候选库订单映射整改

取得用户对真实 v18 切换的明确授权后，先完成旧 canonical v14 归档和候选 v18 准备；
canonical 库没有被替换。候选库首次真实当前日期订单 `READ_ONLY` 完整通过页面范围、尾部、
Watchdog、Worker、Importer 和 Archive，但非空批次被导入为 `PARTIAL`。只读追溯确认页面
源批次实际为 `ACCEPTED`，降级发生在 Importer 商品映射阶段。

根因是旧专项验收脚本固定传入空的合成映射集合。该做法可以验证页面和队列能力，却会把
任何非空真实订单全部导入为 `UNMAPPED`，不符合 v18 正式运行时以数据库主数据为唯一映射
来源的合同。脚本现与正式订单 Composition Root 对齐，改由同一个调用方指定 Runtime DB
中的 `RuntimeMasterDataRepository.compiled_mappings` 提供版本化映射；没有恢复工作簿运行时
旁路。

候选库原有 12 条 PRODUCT 映射均处于 `DISABLED`，但逐条保存了存在于商品目录中的唯一
候选 SKU。只读预览核对平台、商品名称、平台等级、候选 SKU、商品目录等级和范围唯一性后，
先生成 SQLite Backup，再通过正式 `MasterDataManagementService` 将这些候选关系确认成
`VERIFIED`。该操作只修改尚未激活的候选库；旧 canonical 库和真实平台均未修改。确认后
候选库 `integrity_check=ok`、外键违规为 0。

随后再次执行相同真实 `READ_ONLY`：Automation Run 为 `SUCCESS`、订单批次为 `ACCEPTED`，
范围完整、尾部确认、Watchdog 验证、导入和归档全部通过，活动 Queue 为 0，平台写操作为 0。
当前订单快照仍然非空，因此既有“可信空 OPEN 快照”库存 bootstrap 门禁继续生效；本轮没有
执行库存 bootstrap、候选库 verify 或 canonical 激活。

修复后的验收脚本、订单 Adapter/Importer/Automation、Queue 集成和主数据专项为
`47 passed`。`git diff --check` 和修改文件严格 UTF-8 回读通过；当前 Python 环境未安装
Ruff，因此没有宣称 Ruff 通过。候选库映射确认前的备份和两次真实只读审计保留在仓库外
切换目录，未向仓库写入真实订单值、平台订单号或买家信息。

## 12. 2026-08-16 canonical v18 真实激活

用户冻结库存为 12 个 SKU、合计 29 扎，并确认截至 18:00 的订单已经结算且包含在该库存
中。由于维护窗口位于截单后，小程序仍展示刚结束的自然日，库存 bootstrap 增加两项窄门禁：

- 允许最新、十分钟内、完整且可信的刚截单 `CLOSED` 批次作为切换水位；
- 非空批次必须同时提供显式开关和固定确认文本，记录批次引用，并把下一 PRA 交易日作为
  水位日期，避免已结算订单再次扣减。

最终真实 `READ_ONLY` 通过同一候选 v18 Runtime 的
Watchdog → Worker → Importer → Archive。页面捕获 30 个商品行，其中 29 个正数量行进入订单
事实；另 1 个退款展示行为数量 0。原始 Worker 归档完整保留该行，Adapter 将其作为
`excluded_zero_quantity_row_count=1` 显式记录，不计入销量；范围、日期、滚动和尾部仍全部
通过。Automation Run 为 `SUCCESS`，订单批次为 `ACCEPTED / CLOSED`，活动 Queue 为 0，
平台写操作为 0。仓库未保存真实订单值、平台订单号、截图或买家信息。

库存 bootstrap 在写入前完成只读预览、商品工作簿哈希和候选库逻辑哈希冻结；写入后回读
12 个余额、合计 29，权威状态为 `DB_AUTHORITY`，并生成迁移前商品工作簿和 SQLite 双备份。
12 条 PRODUCT 候选映射经正式服务确认后，新增 `confirm-mappings` 门禁把最终映射快照绑定
回切换清单，避免通过人工改写清单哈希绕过验证。

候选库最终验证结果为 Runtime Schema v18、`integrity_check=ok`、外键违规 0、商品目录和
平台映射快照一致、逐 SKU 库存一致。停止候选库 Queue Service 后，以固定确认文本和旧库/
候选库双逻辑哈希执行 canonical 激活；激活后逻辑快照与候选库一致。旧 v14 保留在激活记录
目录，可在没有新增非 `BOOTSTRAP` 库存流水前按既有回滚门禁恢复。

正式 v18 回读确认艾莎与紫霞仙子库存为 0；卡布奇诺 B/C/D 分别为 18/10/1，其余卡布奇诺
等级为 0，总库存 29。Queue Service 已重新绑定 canonical v18；长期 Worker 恢复 3 秒轮询并
保持新鲜 `RUNNING`。开发 Web 以 `PRA_ENV=development`、HTTP 和非 Secure Cookie 启动，
`/health` 返回 200；内置浏览器自动重载被本地 URL 安全策略拒绝，未绕过该策略，页面留给
操作人员手动刷新验收。本轮没有提交库存调整或任何真实平台写动作。

迁移相关专项为 `95 passed`；第一次完整回归发现 1 项测试仍断言已被多商品订单结构取代的
旧固定步长帮助函数，生产代码未改，仅把断言更新为实机验证后的同卡片步长 5、跨卡片步长
9 及索引容器关联。相关复验为 `19 passed`，最终完整回归为
`1243 passed, 3 skipped, 82 subtests passed`，耗时 440.28 秒；隔离系统冒烟为
`16 passed, 0 failed`，使用临时数据库和 mock 通知。

## 13. 2026-08-28 库存与上架语义纠正

人工录入真实库存后重新核对业务目标，撤销“平台目标库存不得超过真实库存”和“把各平台
额度求和后判断超售”的旧假设。数据库库存是农场真实剩余库存，没有新流水时自动跨 PRA
交易日延续；平台库存只是买家可购额度，可以显著高于真实库存，不形成预留或销量。`20` 扎
按品种作为真实剩余库存接近销售边界时的运营安全余量，不从平台额度扣除，也不做等级比例
分配。自动调整平台额度的具体策略尚未冻结，本轮不借此创建新的平台写动作。

实现收口后，库存预警已从单 SKU 判断改为按 `product_name`（运营品种）汇总全部等级的
数据库余额；Incident 以品种去重，并继续复用原有通知、重复窗口和恢复链。运营 Web 的
今日页和人工库存调整页显示品种总库存、等级明细、`20` 扎安全余量及差额；平台扫描得到的
可购上限在独立表中展示，不参与真实库存或预警计算。默认策略通过版本化配置服务启用，
不直接修改平台、不创建 Task，也不新增 Schema。

专项与受影响回归：`115 passed in 53.06s`，覆盖品种跨等级触发/恢复、并发去重、配置范围、
今日页、业务管理、Web 基础、订单扫描、日结自动化和商品主数据。

`SET_ONLINE` 最初在人工预览和最终执行授权使用同一最近定时扫描质量硬门禁。后续运营验收又
发现改价、加/降价和下架没有完整沿用同一人工确认语义。现统一为：MANUAL 人工任务在最终
确认弹窗逐项显示扫描不完整、质量不足或价格/状态事实过期提醒，操作者可确认继续；上下架
预发布不再以旧扫描阻断或直接判定已完成，交给执行端实时页面预检。AUTOMATION 来源仍保留
硬门禁。两类路径都只查询既有 Automation/观察事实，不创建临时 Run。

订单/上架/人工任务/授权专项为 `32 passed`；受影响 Web、预测、持久化、Task 13 和
Automation 集成为 `191 passed`；Ruff、`git diff --check`、18 个变更 Python/Markdown 文件
的严格 UTF-8 回读通过。Web 重启后 `/health=200`、监听器 1 个，健康检查前后 canonical
Runtime 文件大小和修改时间不变。本轮未投递真实平台任务，未操作 Worker 或 Queue。

## 14. 2026-08-29 正式定时商品扫描接线与实机验收

复查发现正式 Automation Service 只接入了订单子链；每小时父 Run 可以生成
`ORDER_SCAN`，却没有执行 `LISTING_STATUS_SCAN`，不能为上架库存门禁提供新鲜平台事实。
整改先按复用门禁盘点任务 13 资产，没有新建平台扫描协议或第二套 Worker：

- `FULL_MARKET_SCAN` 组合既有订单父 Handler，并增加商品状态子 Run；
- 商品 Handler 原样调用任务 13 v5 `prepare → publish → import`，再用既有标准转换和
  Product Observation Importer 写入不可变观察；
- 商品和订单共用同一文件队列等待、Worker 心跳、Automation 租约续期和归档接口；
- 常驻 Queue Service 在有效商品子 Run 持有租约时让出结果，Run 结束或租约失效后不永久
  延迟导入；
- 新增显式只读扫描运行模式，只物化已注册扫描类型，避免验收时顺带执行日结或规则生成。

第一次真实调度已经读完页面并导入 17 个商品观察，订单子链也成功，但商品 Run 被错误标为
`FAILED`。根因不是页面或 Importer，而是既有归档函数完成移动后没有返回归档目录，新
Handler 将返回的 `None` 误判为未归档。修复公共归档返回值并验证请求、结果两组 SHA-256
后，在真实 `2026-08-29 16:10 +08:00` 每小时窗口自然重跑，结果为：

- 父 `FULL_MARKET_SCAN`、商品 `LISTING_STATUS_SCAN`、订单 `ORDER_SCAN` 均为
  `SUCCESS`；
- 商品观察批次和订单观察批次均为 `ACCEPTED`，`scope_complete=1`、
  `end_marker_verified=1`；当前交易日订单保持 `OPEN`；
- 商品结果 ACK 为 `WRITTEN`，商品与订单请求/结果均进入仓库外 Archive，校验和通过；
- 活动 `inbox/working/results` 全部为 0，新增平台操作 0、业务任务 0；
- 验收用 Automation 进程已停止，长期 Worker 与 Queue Service 保持 `RUNNING`。

运行前对 canonical v18 执行 SQLite 在线备份并通过 `integrity_check=ok`；旧的
`T1354-ACCEPT-FULL` 验收任务只停用不删除，避免与正式小时任务重复。受影响专项最终为
`184 passed`，另补充的租约退出回归为 `1 passed`；`compileall`、`git diff --check` 与
修改文本严格 UTF-8 回读通过。当前 Python 环境没有 Ruff，故本轮不声称 Ruff 通过。本轮
只读取真实页面，没有执行或注册任何平台写 Handler。当时 10 分钟 `ONLINE_PULSE` 的
“仅上架中” profile 尚未实现，因此本节没有用完整双页扫描冒充。

## 15. 2026-08-29 `ONLINE_PULSE` 仅上架中实机验收

在同一任务 13 v5 扫描链上增加 `scan_scope=online` 参数，而不是新建平行 Adapter：

- 继续复用同一 `SYNC_STATUS` 合同、Worker、索引读取、`END → 尾部确认 → HOME`、文件队列、
  Automation 租约、结果 ACK 与 Archive；
- 执行端只扫描“上架中”，不选择“待上架”；只保存出现商品的正向观察，缺席不推断下架；
- 单页事实写入 v14 已有 `product_observation_batches/items`。只允许双页完整的
  `listing_sync_snapshots` 不写入单页结果，因此没有伪造待上架完成状态，也没有扩 Schema；
- 受控 `ONLINE_PULSE_ONLY` 验收模式只注册该 Handler，不物化小时完整扫描、订单、日结、
  规则生成、复核维护或任何平台写 Handler。

同步 `test2` 前发现旧生命周期记录为 `RUNNING`，但心跳已失效且影刀进程不存在；队列为空，
因此按规则更正为 `STOPPED`，同步后核对部署哈希，再从应用列表启动长期 Worker。真实
`2026-08-29 17:40 +08:00` 计划窗口结果为：

- `ONLINE_PULSE` Run 为 `SUCCESS`，约 22 秒完成；
- v5 请求为 `READ_ONLY / sync_status / scan_scope=online`，没有写任务项；
- 结果为 `VERIFIED`，`online_scan_complete=true`、`online_end_marker_verified=true`，
  `waiting_scan_complete=false`、`waiting_end_marker_verified=false`；
- timing 只有窗口准备、登录检查、商品刷新、上架中扫描和总计，不含待上架扫描；
- Product Observation 批次为 `ACCEPTED`，`pages=["online"]`、范围完整、尾部已确认，
  3 项均为上架中正向观察，离线观察为 0；
- `listing_sync_snapshots` 数量保持 2，平台操作保持 0，业务任务保持 0；
- 请求与结果 SHA-256 均与 Archive 校验文件一致，ACK 为 `WRITTEN`，活动
  `inbox/working/results` 均为 0，`stop.signal` 不存在，长期 Worker 心跳保持新鲜
  `RUNNING`。

部署后运行进程回读确认 Worker 实际解释器为影刀内置
`C:\Program Files (x86)\ShadowBot\shadowbot-6.3.12\python310\python.exe`，版本
`3.10.11`；`vertical_slice_read_price.py` 与 `shadowbot_queue_worker.py` 的源/部署 SHA-256
分别一致。文件内容、部署哈希与真实运行成功分别核对，没有用控制台显示代替任一门禁。

运行前 canonical v18 在线备份位于仓库外
`D:\PRA_Runtime\backups\online-pulse-acceptance-20260829-1740`，`integrity_check=ok`。
受影响的合同、Worker、Importer、Automation 和列表控制流测试为 `225 passed`，耗时
70.74 秒；`compileall` 与 `git diff --check` 通过。第一次后台启动验收调度器时，扫描完成后
因 `Start-Process` 参数中的 heartbeat 路径引号导致该一次性进程在写 heartbeat 时退出；随后
使用相同参数直接执行 `--once`，确认 `ONLINE_PULSE_ONLY` 只注册 `ONLINE_PULSE`、不再创建
重复 Run，并正常写出 `STOPPED / ONCE_COMPLETED` heartbeat。该收尾问题没有影响已经完成的
Worker → Importer → Archive，也不是平台页面或扫描失败。

最终收尾发现旧 Queue Service heartbeat 同样停在 17:21 且对应进程不存在；本轮结果已由
Automation Handler 在有效租约内导入和归档，不受该旧服务状态影响。随后使用 canonical v18
和同一队列重新启动常驻 Queue Service，回读 heartbeat 为新实例 `RUNNING`、周期持续递增且
无待导入事件。交付时 Worker 与 Queue Service 均为新鲜 `RUNNING`，一次性验收 Automation
进程为 `STOPPED`。

## 16. 2026-08-30 常驻 Automation 无人值守完整周期验收

本次直接使用 canonical v18 Runtime 和正式文件队列，启动
`LISTING_ORDER_READ_ONLY_SCANS_ONLY` 常驻 Automation Service；注册范围固定为
`ONLINE_PULSE / FULL_MARKET_SCAN / LISTING_STATUS_SCAN / ORDER_SCAN /
PRE_CUTOFF_FULL_SCAN`，`platform_write_handlers_registered=false`。影刀 `test2` Worker 和
Automation Service 在所有目标窗口持续常驻；Queue Service 的既有失败与恢复事实见下文。
整个扫描过程没有人工登录、人工投递或平台写操作。

常驻服务在 `2026-08-30 09:50`、`10:00` 和 `10:20 +08:00` 三个自然窗口自动完成
`ONLINE_PULSE`。三次 Run 均为 `SUCCESS`，Product Observation 批次均为 `ACCEPTED`、
`scope_complete=1`、`end_marker_verified=1`，活动队列在每轮完成后都回到 0。10:10 的小时窗口
按冻结合同执行：

- 父 `FULL_MARKET_SCAN` 为 `SUCCESS`；
- 商品子 `LISTING_STATUS_SCAN` 为 `SUCCESS`，批次 `ACCEPTED`，完整读取 14 项商品事实；
- 订单子 `ORDER_SCAN` 为 `SUCCESS`，批次 `ACCEPTED / OPEN`，当前页面为可信空页，保存 0 项
  订单事实，不把开放交易日包装成闭市事实；
- 同一分钟的 `ONLINE_PULSE` 被成功的完整商品扫描覆盖为 `MERGED`，没有重复操作小程序；
- 商品与订单请求/结果均归档，请求和结果共四个 SHA-256 均与 sidecar 一致；两个结果均为
  `READ_ONLY` 且 `side_effect_state=NOT_STARTED`；
- 本次 Worker 启动后共归档 8 个真实只读请求，所有登录检查均走
  `BUSINESS_ENTRY_FAST_PATH`，没有人工介入；
- 验收期间新增 `shadowbot_operations=0`、新增业务 `tasks=0`、过期 `RUNNING` 租约为 0。

验收准备时发现前一日 Queue Service 在 `2026-08-30 09:45:24 +08:00` 因一次 Windows
`os.replace` 的 `PermissionError` 已退出；该旧实例不在本轮 10:10 Automation Handler 的
Importer/Archive 执行路径上，因此没有造成事实丢失或活动文件残留。收尾时使用同一 v18
Runtime 和同一队列恢复 Queue Service，新实例跨过 10:20 实际扫描并持续运行至少 450 个周期，
心跳为 `RUNNING`、`reason` 为空、错误日志为 0 字节。

交付时 Automation Service、Queue Service 和 Worker 均保持新鲜 `RUNNING`；Automation 仍为
同一实例且错误数组为空，两个后台 stderr 均为 0 字节，`stop.signal` 不存在，
`inbox/working/results` 全部为空。生命周期记录已更新为
`RESIDENT_UNATTENDED_FULL_CYCLE_ACCEPTED`。本节只证明常驻只读完整周期，不授权或证明任何
真实平台 COMMIT。

## 17. 2026-08-30 Web 创建与独立授权真实下架验收

管理员在新运营 Web 中按“艾莎 + B/C/D 等级 + 蚂蚁花团供应商”范围创建一次人工下架操作。
Web 在 `2026-08-30 10:37:24 +08:00` 生成 3 个独立 Runtime Task，三者共享同一
`web-manual` 来源引用，并分别绑定 `MANUAL_EXECUTION_AUTHORIZATION_REQUIRED`；创建任务阶段
没有投递 Queue。管理员随后在独立执行授权阶段明确选择这 3 个 Task，系统于 10:38:03 生成
单一 v5 `set_offline` 批次 `WEB7E-77673180ffaabd03916f7e3a7239c16a`。

真实执行结果为：

- 批次目标 3、`verified_count=3`，`unknown / partial / not_attempted / failed` 均为 0；
- B/C/D 三项均从预期 `online` 到目标 `offline`，只执行上下架动作；
  `detail_effect_state=NOT_APPLIED`、`listing_effect_state=VERIFIED`；
- 每项均保存独立 operation、attempt、点击时间和后置回读时间，三项 operation、COMMIT attempt
  和 Runtime Task 最终分别为 `VERIFIED / VERIFIED / success`；
- 批次结果为 `VERIFIED`、`side_effect_state=VERIFIED`，请求与结果 SHA-256 均与 sidecar
  一致，Importer ACK 绑定同一 execution attempt 与 batch；
- 请求、结果、phase、ACK 和人工可读报告已进入同一 Archive 目录，活动
  `inbox/working/results` 全部为 0；
- 10:40 的自然 `ONLINE_PULSE` 随后为 `SUCCESS / ACCEPTED`，完整确认上架中页尾部且读取 0
  项；该单页事实只作为与三项下架结果一致的后续观察，不用“上架中缺席”单独推断下架。

验收后 Web 继续监听 `127.0.0.1:8765`，Automation、Queue Service 与 Worker 心跳均保持
`RUNNING`，Automation 错误数组为空。本节只证明本次明确范围和单批次授权，不扩大为后续
任意商品或动作的持续写授权。

## 18. 2026-08-30 任务创建一次确认与逐项目标整改

根据首次真实操作反馈，运营 Web 不再要求操作者依次完成“创建确认、再次选择 Task、执行
影响预览、二次执行确认”。当前范围弹窗只选择品种、等级、平台和动作；服务端展开具体
平台商品后自动弹出逐项预览。每项分别显示最近记录的平台状态、价格和平台库存，并允许
单独修改本次价格/库存；绝对价格和上架库存默认带入最近记录，加/降价因语义为差额而默认
为 0，必须改为非 0。

操作者完成逐项预览后进入单独的小型最终确认弹窗，并只需在该窗口确认一次。Web 随后只对
本轮明确勾选的 item key 依次调用既有
`ManualTaskApplicationService.create()`、`prepare_execution()` 和 `submit_execution()`。
后台的 Task、授权 digest、v4/v5 publisher、Queue、Worker、Importer 和回读门禁均未合并或
绕过；其他 `PENDING` 不会被顺带执行。若 Task 已创建但后续授权或发布失败，Task 保留在
“当前任务”，由原有独立授权入口恢复。

修改前按用户要求停止常驻定时扫描：确认运行中 Automation Run 为 0、活动
`inbox/working/results` 均为 0、Automation 进程为 0，并把外部 heartbeat 明确记录为
`STOPPED / USER_REQUESTED_SCHEDULED_SCAN_STOP`。Queue Service 和影刀长期 Worker 未因本次
停止动作而停止。

验收使用 canonical Runtime 的 SQLite 在线备份建立仓库外隔离副本，并使用合成登录凭据；
只执行范围选择和 READ_ONLY 预览，没有点击“确认并执行”，没有创建真实 Task、Queue 请求或
平台写动作。桌面确认弹窗能分别带入两项不同价格；390×844 手机视口下改为逐项卡片，价格/
库存输入不再隐藏在横向滚动区域。隔离 Web、临时数据库和临时队列随后已关闭并删除。

专项回归覆盖人工任务编排、统一执行授权、Web Foundation/读模型以及既有 v4/v5 发布链，
结果为 `129 passed in 48.67s`；变更 Python 文件通过 Ruff，`git diff --check` 通过。中文源码
和 Markdown 另以显式 UTF-8 回读，不以终端显示代替文件编码检查。

## 19. 2026-08-30 人工任务扫描质量提醒整改

人工任务预览项新增独立 `warnings`，并进入预览摘要；改价、加/降价、上架和下架的扫描质量、
价格事实过期及状态事实过期均不再混入 `blockers`。最终确认弹窗顶部显示“扫描信息需确认”，
逐项列出原因并明确人工任务可以继续，执行前仍复用平台读取与旧值校验。扫描质量硬门禁在
执行授权阶段仅适用于非 MANUAL 来源；MANUAL 上下架预发布也不再把旧扫描解释为阻断或
`ALREADY_APPLIED`。真正缺少平台事实、映射错误、基础成本/目标值错误、未解决复核、冲突和
写锁等安全门禁不变。错误文本的多条原因直接按中文句号衔接，避免出现 `。、`。

## 20. 2026-08-30 最终确认与首次导航登录恢复整改

真实上架操作暴露出两个交互/执行边界问题。第一，逐项预览曾被实现成可直接提交的确认窗口，
导致运营者点击“确认并执行”时没有独立的最终确认提示；扫描质量提醒也停留在预览顶部。现已
改为“逐项预览 → 小型最终确认 → 执行”：预览只编辑逐项目标，最终弹窗才逐项展示过期或低
质量扫描提醒；提醒不阻断 MANUAL 任务，真正安全门禁保持不变。

第二，Task 12 的登录快速路径曾以“点击前可见商品管理入口”作为已登录的正向信号。真实小程序
会在点击入口后才判定会话过期并跳到登录页，因此该信号不足以证明后续页面仍可访问。上下架、
商品扫描/对账及订单读取现统一为：第一次业务入口点击后检查登录状态，出现登录页时复用既有
自动登录/验证码人工介入链，成功后重新点击原入口；若上次失败已使页面停在登录页，仅在入口
不存在时走同一恢复兜底。没有新增登录实现，也没有自动重试本次失败的真实上架任务。

## 21. 2026-08-30 任务队列运营视图补齐

业务管理新增 `/management/queue` 二级页面，不增加一级入口、数据库表、队列、状态机或
平台动作。页面把现有 Runtime Task 与 `inbox/working/results` 的活动制品按同一任务去重后
组合为“待发送、排队中、执行中、等待回收、需要处理”五个运营阶段，并原样复用 Worker、
Queue Service 和 Importer 健康查询。人工、自动和系统紧急保护可按来源筛选；紧急任务继续
采用既有 dispatch lane 排序，完成历史仍进入数据库。

后续运营复查补充了人工批量取消，但没有改变上述只读队列投影：仅未出现在活动文件队列中的
`PENDING/FAILED` Task 显示选择框；POST 使用 `SUBMIT_EXECUTION + MANAGE_BUSINESS` 权限、
CSRF 和二次确认，数据库在一个 `BEGIN IMMEDIATE` 事务中条件更新全部任务并追加
`task_status_history`。任一任务已执行、进入复核、进入文件队列、状态竞争或队列读取不完整时
整批拒绝，不删除任务、队列制品或历史。失败任务的页面说明改为“任务已停止，当前不会自动
重试”，不再要求运营人员寻找不存在的处理入口。

专项测试覆盖成功批量取消、混合状态整批零写、活动 Queue 拒绝、CSRF 拒绝和数据库竞争回滚；
真实 Runtime 只完成界面读取和弹窗交互验收，不提交取消。

队列制品只按白名单读取动作、商品、平台、来源任务、Automation Run、阶段和时间，单文件
限制为 2 MiB；页面不显示任务内部 ID、execution attempt ID、Hash、文件路径或原始 JSON。
并发移动导致文件消失时按正常领取处理，格式无效或数量超过展示上限时降级为“队列记录需要
检查”，不会让页面返回 500，也不会尝试修复制品。专项测试证明 GET 前后 Runtime DB 内容
摘要以及活动队列文件的大小、修改时间均不变。

真实 canonical Runtime 的只读页面检查显示 2 项待发送、0 项排队、0 项执行、0 项等待回收、
1 项需要处理；Worker、任务传递和结果回收均可读。浏览器已验证人工任务+需要处理组合筛选，
桌面布局未出现技术字段。本次检查没有投递、领取、导入或重试任何真实任务。
