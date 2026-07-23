# AGENTS.md

## 项目名称

鲜花多平台销售自动化管理系统

## 项目背景

本项目用于帮助切花月季种植/销售业务管理多个线上销售平台的商品价格、上下架状态和执行记录。

业务现状：
- 商品主要是切花月季等鲜切花。
- 销售渠道分散在多个线上平台。
- 当前第五阶段已选定一个真实平台作为单平台适配和实机验收对象：蚂蚁花团供应商微信小程序；平台专属差异必须封装在 ShadowBot adapter/executor 中。
- 多平台仍是长期目标，但第二平台适配尚未进入当前任务；公共数据、任务、审批、日志和验收层仍必须保持平台无关。
- 影刀 RPA 当前已经是本阶段的受控执行层。开发实机测试在投递完整 COMMIT 队列前按固定商品清单获得一次批次授权；正式运行以任务中心有效任务、页面动态唯一定位和旧价校验为执行依据，不再依赖运行时用户确认。

## 项目目标

当前阶段的目标是同时维护平台无关的基础系统，并在已授权的单一平台上完成受控闭环：

1. 商品主数据管理
2. 价格规则管理
3. 上下架规则管理
4. 任务生成
5. 执行日志记录
6. 为未来 RPA 执行层预留接口或任务表
7. 在当前单一平台上完成结构化 READ_ONLY、单次完整 COMMIT 队列和 RECONCILE

最终希望实现：
- 用户只维护一份商品和规则数据。
- 系统根据库存、成本、销售状态等生成待执行任务。
- RPA 执行层读取任务并到各平台执行改价、上架、下架。
- 执行结果回写到系统。

## 当前阶段范围

### 已完成并继续复用的平台无关核心

- 商品主表
- 价格规则表
- 上下架规则表
- 任务表
- 任务生成逻辑
- 执行日志结构
- 基础管理界面或命令行工具

### 当前第五阶段允许实施的单平台能力

- 当前蚂蚁花团供应商微信小程序的结构化 READ_ONLY 商品读取和状态模型（任务11已完成）。
- 同一平台多商品动态唯一定位、按页面顺序严格串行并以单次请求完成 COMMIT（任务12）；开发测试使用批次清单授权，正式运行不携带开发确认字段。
- 提交后独立 READ_ONLY、UNKNOWN、唯一 RECONCILE、暂停/恢复和部分完成账本。
- 影刀登录、验证码人工介入和残留窗口收尾等执行层操作，但必须遵守本文件的影刀客户端操作约束。

### 当前明确不在范围内

- 第二个平台适配器或跨平台混合批次。
- 上架、下架和 OFFLINE 跨页面状态对账（任务13负责）。
- 价格历史事实表、AI 自动定价、AI 自动审批或自动发起 COMMIT。
- 绕过任务中心任务有效性、页面旧价校验或唯一身份匹配直接执行真实写操作。
- 支付、订单、资金相关功能。

平台专属页面定位、元素、登录和动作逻辑可以存在于当前平台 adapter/executor；公共服务层不得依赖具体页面细节，也不得把当前平台实现复制成第二平台方案。

## 核心业务概念

### 商品主数据

商品主数据是系统的核心，不依赖任何平台。

典型字段：
- internal_sku：内部商品编码
- product_name：标准商品名
- variety：品种
- grade：等级
- stem_length：枝长或规格
- unit：单位
- base_cost：基础成本
- current_stock：当前库存
- sale_enabled：是否允许销售
- remark：备注

### 价格规则

价格规则用于根据成本、平台、库存、等级等因素生成目标售价。

当前阶段可以先实现简单规则：
- 固定加价
- 百分比加价
- 最低价限制
- 取整规则

### 上下架规则

上下架规则用于根据库存和销售状态生成任务。

示例：
- 库存小于等于 0 时生成下架任务
- 库存高于指定阈值时允许上架
- sale_enabled 为 false 时强制下架

### 任务

任务是系统和 RPA 执行层之间的中间格式。

典型任务类型：
- update_price
- set_online
- set_offline
- sync_status

任务表不应绑定具体 RPA 工具。

## 推荐数据表

优先保持数据结构简单，便于后续接入 Excel、SQLite、MySQL、飞书多维表或 RPA 工具。

建议至少包含：

- products
- price_rules
- listing_rules
- tasks
- execution_logs
- platform_mappings，当前可预留但不强制使用

## 设计原则

1. 先做简单可运行版本，不要过度设计。
2. 业务逻辑和 RPA 执行逻辑必须分离。
3. 任务表是业务系统和 RPA 之间的边界。
4. 平台相关逻辑必须封装在未来的 adapter 或 executor 中。
5. 不假设任何平台提供 API；当前平台仍通过封装后的 ShadowBot adapter/executor 执行。
6. 平台名称、登录、元素定位和动作细节只能出现在平台适配层或配置中，公共核心不得写死页面细节；当前单平台适配不等于完成多平台抽象。
7. 所有账号、密码、token 必须通过环境变量或配置文件管理，不得写入代码。
8. 所有执行任务都应有状态、时间、错误信息和可追踪日志。
9. 对失败任务要保留错误原因，不要静默忽略。
10. 优先保证可维护性，而不是功能堆叠。

## 影刀客户端操作约束

### 长期 Worker 与生命周期状态

- `test2/module1` 是常驻队列 Worker，默认最多连续运行 8 小时或处理 50 个请求；完成单个请求后不会自动返回。正常开发和连续实机验证默认复用同一个 Worker，不再执行“每轮检查 → 启动 → 停止 → 再检查”的完整生命周期，也不为了单轮收尾把 Worker 改成 `max_tasks=1`。
- 生命周期记录固定保存在 `D:\PRA_Runtime\shadowbot_queue\control\shadowbot_lifecycle_state.json`，不得写入仓库。`recorded_state` 只允许为 `RUNNING / STOPPED / UNKNOWN`；至少记录 `schema_version`、`app_name`、`recorded_state`、`worker_started_at`、`worker_processed_count`、`last_used_at`、`last_execution_attempt_id`、`shadowbot_window_state`、`updated_at` 和 `reason`。文件使用 UTF-8 JSON，不得包含账号、密码、token、任务业务数据或授权正文。
- 生命周期记录是“上一次已核实状态”，不是运行真值。`heartbeat.json`、`inbox/working/results` 的磁盘内容和已归档结果仍是事实来源。每次使用影刀前先读取生命周期记录，再做一次轻量一致性检查；不得仅凭记录文件直接投递任务。
- 若记录为 `RUNNING`，且 Worker 心跳为 30 秒内更新的 `RUNNING`、`stop.signal` 不存在，则直接复用当前 Worker。此时只检查是否存在上一请求的活动 `working` 或未导入 `results`：有则继续跟踪或先完成导入，不得启动第二个应用或重复投递；均无则可直接开始新请求。
- 若记录为 `STOPPED`，且心跳确为 `STOPPED`、队列无活动文件、`stop.signal` 不存在，则只需从影刀应用列表启动一次 `test2`，等待新心跳变为 `RUNNING`，再把生命周期记录更新为 `RUNNING`。启动时必须确认应用名为 `test2`；不要根据固定屏幕坐标或列表排序猜测目标应用。
- 每次请求完成后，先由 Result Importer 导入并归档结果，再更新 `last_used_at`、`last_execution_attempt_id`、`worker_processed_count` 和 `updated_at`。只要 Worker 仍为新鲜的 `RUNNING` 且未达到运行上限，就保持应用运行，不创建 `stop.signal`，并把 `recorded_state` 保持为 `RUNNING`。
- 只有以下情况才计划停止并重新启动 `test2`：需要同步或修改 `test2` 的 Python/流程/元素；Worker 已接近或达到 8 小时、50 个请求的上限；Worker 自身正常返回；用户明确要求停止；或进入下述异常恢复链路。普通 READ_ONLY、COMMIT、结果导入和任务切换都不是重启理由。
- 当前已核实的 `主流程.flow` 顺序是“调用 `module1` → 等待 1 秒 → 调用 `关闭.flow`”。不得在主流程开头调用 `关闭.flow`；它仅在 Worker 返回后执行收尾，后面不得再安排结果写入、日志落盘或业务动作。

### 代码同步与计划重启

- 外部同步 `test2` Python 文件前，必须先停止 Worker 并关闭影刀编辑器；不得在 Worker 运行期间覆盖宿主代码。停止前先确认结果已导入归档，且 `inbox/working/results` 均无活动文件，然后创建 `D:\PRA_Runtime\shadowbot_queue\control\stop.signal`。
- Worker 仅在没有活动 `working` 请求时响应 `stop.signal`；正常响应后写出 `heartbeat.status=STOPPED` 并从 `module1` 返回，主流程等待 1 秒后调用 `关闭.flow`。确认返回影刀应用列表后，立即删除 `stop.signal` 并回读确认不存在，再把生命周期记录更新为 `STOPPED`。
- 外部同步后默认保持在影刀“应用”主页面，不进入“编辑”页面；选中 `test2` 后点击该应用行内的圆形“运行应用”图标启动。直接从应用列表运行可避免已打开设计器把内存缓存或旧流程写回磁盘。
- 只有需要人工捕获或修改流程元素时才进入编辑器。进入编辑器前必须完成上述正常停止；编辑完成后保存并退出编辑器。后续若再由外部工具同步 Python，仍必须先关闭编辑器。
- 同步并校验部署哈希后启动 `test2`，等新心跳变为 `RUNNING`，记录新的 `worker_started_at`、初始处理计数和部署原因。除非同步内容本身要求一次性退出，否则恢复长期监听模式。

### 状态不一致与异常恢复

- 生命周期记录与事实不一致时，先将其视为 `UNKNOWN` 并进行只读核对，不要直接重复启动或强制结束进程。若记录为 `STOPPED`，但心跳是新鲜的 `RUNNING`，以心跳为准更新记录并复用 Worker；这属于状态记录滞后，不需要重启。
- 若记录为 `RUNNING`，但心跳为 `STOPPED` 或已超过 30 秒未更新：先检查 `working/results`。存在活动 `working` 或未落盘结果时，保留请求和 phase 证据并进入故障处理；队列为空且影刀主窗口可定位、处于应用列表时，可正常重新启动 `test2` 并更新记录。
- 若 Worker 已停止或心跳失效，同时影刀主窗口无法调出或不可定位，则进入影刀异常重启链路：确认没有活动请求和未保存编辑内容；必要时结束已核实路径的残留 `ShadowBot.Shell.exe`；重新启动影刀；等待至少 20 秒完成登录；定位应用列表中的 `test2`；启动 Worker；核对新鲜 `RUNNING` 心跳；最后更新生命周期记录。
- 不依赖 Computer Use 是否能枚举 `RobotRunnerView` 判断 Worker 是否运行或窗口是否已关闭；当前环境已确认该窗口可能不可见。仅当“Worker 已停止/失去心跳”与“影刀主窗口不可定位”同时成立时，运行窗口不可见才构成异常重启依据。
- `stop.signal` 不是强制中断。若创建后任务仍卡在活动 `working`、结果尚未落盘或 Worker 无法正常响应，先保留请求和 phase 证据，再发送全局快捷键 `Ctrl+Alt+Q`。发送后重新检查队列、心跳和影刀主窗口；只有主窗口仍不可定位时，才按上述条件结束 `ShadowBot.Shell.exe` 并重启。


## 建议目录结构

如果使用 Python，可以采用：

```text
flower_automation/
  app/
    models/
    services/
    rules/
    tasks/
    repositories/
    adapters/
  data/
    templates/
  docs/
  tests/
  scripts/
```

## 强制执行协议

本节用于把 Codex global guidance 落地为本项目的可执行门禁。它不是建议；无法满足门禁时必须暂停写入、说明阻塞原因，并先修复门禁。

### 规则优先级

1. 用户本轮明确要求。
2. Codex global guidance 和开发者安全约束。
3. 本文件的项目约束。
4. 代码、脚本和工具的默认行为。

低优先级规则不得覆盖高优先级规则。工具默认编码、终端代码页和影刀编辑器缓存都不能视为项目规范。

### 任务开始前门禁

- 先确认项目规则文件位置，并完整读取本文件；不要假设规则已经被工具自动加载。
- 检查工作区状态，保留用户已有改动，不用重置或覆盖无关文件。
- 涉及中文、CSV、JSON、Markdown、Windows 脚本、影刀或外部同步时，先写出本次任务的编码和同步边界。

### 编码与字符集门禁

- Python、JSON、Markdown、普通文本默认使用 UTF-8；CSV/TSV 默认使用 UTF-8-SIG，确保 Excel/WPS 正确识别中文。
- 所有 Python `open`、`Path.read_text`、`Path.write_text`、`subprocess` 文本管道都必须显式指定 encoding；读取外部 JSON 可使用 `utf-8-sig` 兼容 BOM，写出 JSON 必须使用 UTF-8。
- JSON 的业务文件使用 `ensure_ascii=False` 保留中文；若跨越不可靠的日志/命令行边界，优先传递文件路径或 ASCII 安全的摘要，不把终端显示当作数据真值。
- 不依赖当前 PowerShell 代码页判断内容是否正确。读取或写入含中文文件时显式指定 `-Encoding UTF8`，写后必须回读并验证 UTF-8 解码、首行/表头和 1 至 3 行样例。
- 修改源码必须使用 `apply_patch`；不得用 `cat`、重定向、未指定编码的 `Set-Content` 或临时 shell 写文件替代受控修改。
- 运行 Python/影刀子进程前，若会输出中文，设置 `PYTHONIOENCODING=utf-8`，并在程序入口尝试将 stdout/stderr `reconfigure(encoding="utf-8", errors="replace")`；若宿主不支持，必须把结果落盘后再读取验证。
- 换行符告警（LF/CRLF）与字符集错误是两类问题，必须分别检查，不得用其中一项代替另一项。

### 影刀同步与运行门禁

- 每次开始影刀工作时先读取 `D:\PRA_Runtime\shadowbot_queue\control\shadowbot_lifecycle_state.json`，再核对 Worker 心跳、`stop.signal` 和队列活动文件。状态一致且 Worker 为新鲜的 `RUNNING` 时直接复用；不得仅为了开始新请求而停止或重启应用。
- 只有在 Worker 为 `STOPPED` 时才执行启动门禁：确认应用名为 `test2`、`stop.signal` 不存在、队列无上一轮活动文件，并从应用列表启动。启动成功以新心跳变为 `RUNNING` 为准，不要求捕获 `RobotRunnerView`。
- 外部同步 `test2` Python/流程/元素前必须完成正常停止并关闭影刀编辑器；同步后保持应用列表页，不进入编辑器确认代码。同步完成、哈希一致且重新启动成功后恢复长期监听。
- 队列 JSON、phase、result 和校验文件以磁盘内容为准；影刀日志、PowerShell 输出和控制台显示只能作为辅助证据。
- 每轮结果落盘后先由 Result Importer 导入归档；若未触发代码同步、运行上限、正常返回、用户停止或异常恢复条件，则保持 Worker `RUNNING`，更新生命周期记录后结束本轮，不创建 `stop.signal`。
- 计划停止时，确认 `inbox/working/results` 均无活动文件，再创建 `stop.signal`；等待心跳变为 `STOPPED` 和主流程任务日志写出“执行结束”，确认应用返回列表后立即删除信号并更新生命周期记录。不得把 Computer Use 未枚举到 `RobotRunnerView` 当作窗口关闭的单独证据。
- 状态不一致时按“状态不一致与异常恢复”处理；只有 Worker 已停止或心跳失效且影刀主窗口不可定位时才进入强制重启。强制重启前确认没有未保存编辑内容，重启后等待至少 20 秒登录完成。

### 完成前验收门禁

- 回读本次修改的中文文件并进行编码自检。
- 对 JSON/CSV/结果文件验证结构、中文字段、校验和及关键样例。
- 对普通影刀请求同时核对：结果文件状态、证据文件哈希、Result Importer 导入归档、队列活动文件、`stop.signal` 不存在，以及生命周期记录与新鲜心跳一致。Worker 可以保持 `RUNNING`，不得把 `STOPPED` 和关闭运行窗口作为每轮完成条件。
- 对涉及 `test2` 代码同步、解释器或部署变更的任务，额外核对实际解释器路径和版本、部署文件哈希、编辑器已关闭，以及重启后的新鲜 `RUNNING` 心跳；若本轮按计划停止，则核对 `STOPPED`、应用列表状态和生命周期记录。
- 报告中必须区分“文件内容正确”“控制台显示正确”“影刀实际运行成功”；三者不能互相替代。
- 未通过任一门禁时不得写“已完成”或“验收通过”，必须列出未通过项和下一步。
