# PRA：鲜切花销售观察与受控执行

PRA 支持鲜切花预测性销售：持续观察平台与供给事实，供管理者通过 Operations Web 作出决定，再经授权、执行、回读和恢复形成闭环。当前销售 Controller 是人类。

当前进行 **Task 13.6-3 文档入口与 AI 上下文收口**。G1/G2 已合并；13.6 Overall 尚未验收通过，13.7 不得开工。[最新状态与证据](docs/project_current_status.md)

## 阅读入口

1. [产品目标与路线图](docs/project_overview.md)
2. [当前业务合同](docs/business_contract.md)
3. [当前实现及差距](docs/rebaseline/task13_6_current_implementation_map.md)
4. [目标职责与 13.7 交接](docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md)
5. [审核治理](docs/pra_review_risk_and_complexity_governance.md)

开发 AI 从根级 [AGENTS.md](AGENTS.md) 开始。其他文件按[文档索引及身份](docs/index.md)读取。

已有成熟资产包括 v4 改价、v5 上下架、文件 Queue、Worker/Importer/Watchdog、UNKNOWN/RECONCILE、人工授权、Review/Outbox 与 DB 实物库存。新业务持续执行 owner、Commitment、Closing 等尚需实现；目标架构不等于部署状态。

## 本地运行与运维

当前 Web 主入口为 `/today`、`/database`、`/management`、`/system`；数据质量位于 `/database/quality`。商品/规则/预测等仍有工作簿输入；实物库存使用 DB authority，平台 Exposure 与实物库存是不同事实。

安装与配置按[核心发行物部署](docs/core_wheel_shadowbot_deployment.md)及[环境变量](docs/runtime_environment_variables.md)执行。现有 Windows 启动入口：`scripts/start_local.ps1` 只启动 Web，`scripts/start_local_services.ps1` 独立管理 Queue Service。运行或部署操作需匹配用户当前授权；13.6 文档工作不启动这些服务。

ShadowBot 的凭据使用部署机本地 Windows Credential Manager；真实 target、账号/密码、Token、Webhook、本地配置和 Runtime DB 不提交 Git。[Worker/Queue 运维](docs/shadowbot_file_queue_operations.md)包含显式 app-dir、部署核对、登录与回读要求。

## 验证

按任务影响选择验证，见[审核治理](docs/pra_review_risk_and_complexity_governance.md)和[Core CI](docs/core_ci.md)。CI、受控实机操作与长期运行分别报告。文档改动检查编码/链接/差异并执行所需理解验收，不为获得“通过”运行真实平台写操作。
