# 任务 13.5-7E：运营控制面实施报告

- 实施日期：2026-08-13
- Review Profile：`R3`；真实平台发布继续沿用既有 `R4` 门禁
- 分支：`codex/task13-5-7e-control-plane`
- 基线：`f9721fa`（7D PR #34 合并提交）
- 范围：人工任务、精确执行授权、原子复核、Automation 固定配置与 CLI 归位

## 1. 结论

7E 补齐了新运营 Web 的写控制面，但没有新建平台动作、任务/Review/Incident 状态机、
Queue 协议、全局锁或 Runtime 表。创建任务与真实平台执行授权是两个独立阶段；Web 创建
成功只写 Runtime Task，绝不自动投递 Queue 或启动 Worker。

本分支没有读取、迁移或修复真实 Runtime DB，没有启动真实 Worker、投递真实 Queue、发送
真实通知或执行平台写动作。真实执行仍必须另获用户明确批次授权，并完整通过既有
Queue → Worker → Importer → Archive 和 UNKNOWN/RECONCILE 门禁。

## 2. 复用矩阵

| 能力 | 分类 | 实现 |
| --- | --- | --- |
| Runtime Task、幂等与事务 | 原样复用 | 使用既有 Task 模型和 SQLite Runtime Repository；不建候选任务表 |
| 商品、平台映射、listing 状态与真实库存 | 参数化复用 | 预览和创建都读取既有权威来源并在提交事务内重检 |
| 改价执行 | 原样复用 | 授权服务编排 v4 `prepare_task_commit_batch` / `publish_task_commit_batch` |
| 上下架执行 | 原样复用 | 授权服务编排 v5 `propose_listing_action_batch` / `publish_listing_action_batch` |
| Review/Incident | 原样复用 | 桌面与手机共享同一处置服务和既有原子 Repository 事务 |
| Automation Job/Run/Scheduler/租约 | 原样复用 | 配置只创建/切换 Job 版本；补跑只写 SCHEDULED Run |
| 库存预警 | 原样复用 | 继续使用 v17 版本化预警策略，不创建平台 Task |
| 人工范围展开和统一执行授权 | 确需新增/公共抽取 | 仅新增薄 Application Service，不复制 publisher 或平台状态机 |
| Review 超时、每日任务生成 Handler | 参数化复用 | 薄 Handler 调既有 Review 与任务生成服务，Automation Service 负责运行 |

## 3. 人工任务与执行授权

人工任务弹窗按品种、等级、平台三个维度多选，支持：

- 调整价格到：填写绝对目标价格；
- 加/降价：填写有符号金额，负数代表降价；
- 下架：不显示价格或库存字段；
- 上架：同时填写上架价格和平台目标库存。

服务端逐项展示当前平台事实、真实库存、基础成本、映射和阻断原因。创建时在
`BEGIN IMMEDIATE` 内重建预览并比较 digest；事实漂移、低于成本、库存不足、映射异常、
价格不可用、开放任务冲突或同键异内容都会整批拒绝。精确重放返回原任务；排除项只影响
本次明确选择。

执行授权只接受同平台、同动作族、最多 50 个明确且不重复的 Task ID。准备阶段绑定认证
principal、Task 版本、映射、最新平台事实、成本、真实库存和既有批次；提交阶段再次校验
capability、principal、digest、有效期和全部事实，再进入 v4/v5 publisher。伪造表单 actor、
换批、过期 digest、Review/锁/UI 租约变化或 Queue 失败均保持既有失败语义。

正式 `production` 投递继续遵守既有 v4/v5 合同，不携带仅供开发批次验收使用的
`confirmation_text` 或 `confirmed_by`；认证 principal 在投递前以同状态 Task 历史审计事件
持久化，批次内任一审计写入失败都会整体阻止投递。`development` 仍按原合同传入固定确认
文本和确认人。本次使用真实 v4/v5 请求构造器覆盖了两种 profile，没有放宽 Queue 合同。

## 4. Review 与 Automation

桌面复核要求 `HANDLE_REVIEW` 和 CSRF；手机复核要求有效、未过期且未使用的 Review Token。
两者调用同一个 Repository 业务事务；手机路径只在该事务前半段增加 Token 校验与消费，
桌面路径不制造 Token。Review、源 Task、任务组、调整投影、Task 历史和 Outbox 撤销由同一
事务决定，数据库失败整体回滚；紧急保护仍复用既有专用原子分支。手机 GET 不消费 Token，
原始 Token 不进入 HTML，而是保存到 Review 专用 HttpOnly、SameSite=Strict Cookie；Secure
属性继续服从启动环境。新旧 Web 的 Mobile Review 错误统一复用既有 403/404/409/410/422
映射，不再由新 Web 把已处理复核误报为 410。

Automation 配置不提供任意 Cron、脚本、路径、SQL 或 Queue 编辑器。固定开放：快速扫描、
完整扫描、截单前/后、日结、销售计划输入、人工复核超时维护和每日任务生成。子任务不能
独立配置；时间关键值从运营时间策略派生。排程变化生成确定性新 Job 版本，并在同一事务
停用同类型/平台旧版本。受控补跑只允许日结和销售计划输入，必须明确 PRA 交易日与幂等键；
Web 只创建 SCHEDULED Run。

缺失默认 Job 按初始化时实际生效的时间策略生成并记录 `time_policy_version`；历史交易日补跑
按目标时刻选择当时生效的策略，而不是沿用当前策略。销售计划输入偏移变化会和下游每日任务
生成 Job 在同一事务中确定性重版本，保证下游始终晚于上游；事务任一步失败时两者都不切换。
停用 Job 在原时间表上重新启用时使用原子 upsert，新的白名单等配置不会被旧版本静默保留。

2026-08-14 合并后维护补丁补齐了未来时间策略换版入口：
`scripts/replace_operational_time_policy.py` 默认只读检查，只有显式 `--apply` 才会先生成并校验
SQLite 逻辑备份，再调用 `OperationalTimeMaintenanceService`。Policy 与截单前/后、交易日结算、
销售计划输入、每日任务生成五个定时 Job 在同一事务创建 successor 并停用 predecessor；旧启停
状态、两个后置偏移和来源 allowlist 保留，任一步失败全部回滚。新 Policy 只允许在维护事务时刻
立即生效，Web 仍无 Policy 编辑入口。

每日任务生成要求同一 PRA 交易日的销售计划输入成功完成，只允许商品、价格规则和上下架
规则输入，并明确走既有冻结规则路径，不再因传入交易日而进入预测任务路径。停用的规则来源
不会被读取或参与摘要，生成 Task 的 `origin_ref_id` 精确绑定 Automation Run，并写入同一 Run
冻结的 `platform_trade_date`、`seller_operation_date` 和 `time_policy_version`。包装产能、冷库
和 Mock 平台 evaluator 保留诊断或延期，不接入生产 Automation。复核超时 Handler 直接调用
既有 ReviewTaskService。

## 5. CLI 与界面验收

`serve-web` 已指向新 `app.operations_web`。旧 Excel 任务生成必须显式 `--test-only`；
Runtime 任务生成和 Review 处置必须显式 `--admin-recovery`；超时复核只有只读预览可直接
运行，`--apply` 同样要求管理员恢复开关。CLI 继续保留测试、诊断和恢复职责，但不再承担
日常运营。

内置浏览器使用合成 v17 Runtime DB 完成桌面与 390×844 手机视口只读验收。任务弹窗、
动态动作字段和 Automation 固定配置均可读；手机页面无整页横向溢出。验收中发现并修复
弹窗内部 26px 横向溢出，以及统一“价格数值”标签不符合动作语义的问题。修复后弹窗
`scrollWidth == clientWidth == 344`，目标价格、加/降价金额、上架价格及字段显隐均正确。

## 6. 测试与真实副作用边界

专项与受影响集成回归：`130 passed, 21 subtests passed`。覆盖人工任务四类动作、重放/冲突、
精确执行授权与 v4/v5 复用、桌面/手机原子 Review、Automation 配置/版本/补跑、薄 Handler、
CLI 边界和新 Web PRG/零 Queue 副作用。

完整本地验收结果：

- 完整 pytest：`1280 passed, 3 skipped, 102 subtests passed`；
- 隔离系统冒烟：`16 passed, 0 failed`，Schema exact v17；
- Ruff、`compileall` 与 `git diff --check`：通过；
- wheel/sdist 构建、严格制品边界、源码 secret scan、wheel 隔离安装：通过；
- Windows 临时 ShadowBot fixture 的同步、部署、Hash 漂移和缺失宿主失败语义：通过。

所有本地测试均使用操作系统临时目录和合成数据。Linux Core 与最终 Windows Core 以 Draft
PR 的 GitHub 托管 CI 实际结果为准。

7E 不包含真实平台写验收。任何 COMMIT、紧急下架或真实 Queue 投递都需要新的明确授权，
不能由本报告或代码合并推定。

合并后维护补丁专项：`23 passed`；完整 pytest：
`1227 passed, 3 skipped, 82 subtests passed`；隔离系统冒烟：`16 passed, 0 failed`。使用合成
Runtime DB 覆盖完整换版、旧配置保留、事务失败整体回滚、策略/Job 漂移 fail closed、管理员
脚本备份与回读，以及每日 Task 三个审计字段。
