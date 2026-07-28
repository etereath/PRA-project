# 任务 13.5-0：基线冻结、临时能力审计与正式开工清单

- 形成日期：2026-07-29
- 父级权威：[GitHub Issue #20](https://github.com/etereath/PRA-project/issues/20)
- 适用阶段：13.5-0；本文件不授权提交 Schema v14 或业务实现
- 工作分支：`codex/task13-5-0-baseline-and-inventory`
- 当前判断：13.5-0 文档收尾已开始；第 2.3 节的 ShadowBot 生命周期、部署 hash
  和实机只读基线均已收敛，进入 13.5-1 编码前仍须先评审六级质量矩阵与日结状态机

## 1. 当前推进门禁

1. `13.5-0` 已获准开始。
2. `13.5-1` 可以进行合同与 Schema 设计；六级数据质量矩阵和
   `PROVISIONAL → OBSERVED → RECONCILED → FINAL` 状态机通过评审前，不得提交
   v14 迁移或相关业务代码。
3. 无订单 ID 的多重集合语义必须在 13.5-4 实现前冻结。
4. 库存估算区间合同必须在 13.5-5 实现前冻结。
5. `SYSTEM_EMERGENCY` 来源类型和扩展边界在核心 v14 预留；S4 最终策略不属于
   13.5-1 门禁，也不在核心 v14 提前固化。
6. S4 仍是任务 13.5 目标。13.5-6 必须先完成 Incident 人工闭环，再根据真实扫描、
   销售与人工处置数据冻结策略、迁移策略结构并实现受控自动紧急下架。
7. 任务 14 只做包含 S4 在内的综合验收、正式授权和观察版本冻结，不补做 13.5
   控制面。

## 2. 黄金基线卡

### 2.1 代码与数据库

| 项目 | 基线 |
| --- | --- |
| 分支 | `main` |
| main commit | `268c4ed9facd1ec39698843301d31fff9e860495` |
| 远端对齐 | `main == origin/main` |
| 13.5-0 工作分支 | `codex/task13-5-0-baseline-and-inventory` |
| 回滚点 | 上述 main commit；本分支不修改 Runtime DB 或 ShadowBot 业务代码 |
| Runtime Schema | v13；代码权威 `LATEST_RUNTIME_SCHEMA_VERSION = 13` |
| Runtime DB 快照 | `runtime-db-sha256:4878131a1b76ce7078a51fc8659aaa14583dbb89287b6f52bed44a442db7c723` |
| DB 自检 | `quick_check=ok`；外键错误 0；迁移版本 1–13 完整 |
| 当前 main 新鲜回归 | 2026-07-29：`679 passed, 3 skipped, 97 subtests passed in 144.54s` |
| 最近远端回归 | PR #19 交接记录：Windows Core 与 Linux Core 通过 |

本轮开始时工作树已有任务 13.5 文档草稿和用户自有未跟踪文件。它们均被保留，未执行
重置或覆盖；`.codex_tmp/` 和任务 12 DOCX 不属于本子 PR。Runtime DB、队列和影刀
应用目录继续排除在 Git 提交范围外。

### 2.2 任务 13 代表性证据

| 能力 | 代表性证据 |
| --- | --- |
| 完整两页同步 | `ATTEMPT-T13-POST-PREFLIGHT-RESCAN-20260727-01` |
| 单 SKU 上下架往返 | `BATCH-T13-AISHA-A-SET-ONLINE-20260726-02` / `BATCH-T13-AISHA-A-SET-OFFLINE-20260726-04` |
| 多商品上架/下架 | `BATCH-T13-OPTIMIZED-SET-ONLINE-20260726-02` / `BATCH-T13-OPTIMIZED-SET-OFFLINE-20260726-02` |
| 已处于目标状态零写 | `BATCH-T13-ALREADY-APPLIED-20260727-01` |
| 全批次预检零写 | `BATCH-T13-PREFLIGHT-ZERO-WRITE-20260727-01` |
| 严格串行 UNKNOWN | `BATCH-T13-CONTROLLED-UNKNOWN-20260726-01` |
| UNKNOWN→VERIFIED | `BATCH-T13-AUTO-RECONCILE-CONTROLLED-UNKNOWN-20260727-03` |
| UNKNOWN→NOT_APPLIED | `BATCH-T13-UNKNOWN-NOT-APPLIED-20260727-01` |

统一脱敏索引为 `docs/evidence/task13/index.md`，详细结论见
`docs/reports/task13_final_handoff_20260727.md`。后续只复用这些事实，不为建立
13.5 基线重复制造真实平台副作用。

### 2.3 Worker 部署与生命周期

首次只读审计发现生命周期记录、心跳事实和部署代码不一致。随后按项目规则完成停机
确认、编辑器关闭确认、官方脚本同步、部署复验和应用列表重启。最终状态如下：

| 项目 | 结果 |
| --- | --- |
| 同步工具 | `scripts/sync_shadowbot_test2.py`；官方 `--check` |
| 同步结果 | 6 个受控文件全部 `CURRENT` |
| 部署验证 | `scripts/verify_shadowbot_deployment.py`：`PASS` |
| 生命周期记录 | `recorded_state=RUNNING`；最后请求为本节下方成功只读基线 |
| 心跳事实 | 新鲜 `RUNNING`；Worker 健康检查全部通过 |
| 队列 | `inbox=0`、`working=0`、`results=0` |
| 停止信号 | 不存在 |
| 旧文件保护 | 4 个差异文件已保留 `*.bak_queue_20260729031642` 备份 |

最终部署 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `module1.py` | `3722101375fc0e3d7a1840aa1e07dbbfffe2ceef8bfcd6e4b90bbdf63676c0b8` |
| `shadowbot_credentials.py` | `a0afb42cfaa26a7540ae06786d50ce835bb37a299901392d89c3b3c3695d563b` |
| `shadowbot_contract_primitives.py` | `ed612da180375c960eacb3666d4b7bae9e9b3fa8703bb1d8daa0b2f484abdd63` |
| `vertical_slice_read_price.py` | `691a5423d5d90f3f49065fc36a49147acbfb9dac8c806289c9b486c90dd99143` |
| `shadowbot_queue_worker.py` | `f6c38b9a3ef64a2333ab96fa39dfaf42dd78c015f5b85376d5a337850c4da9d0` |
| `product_identity_mapping.json` | `24f0dd9f88f0fe5e42587cc2b7866f035fef88bcbface3d0353afd99c4fa5ce6` |

`shadowbot_worker_config.json` 是本机运行配置，只检查存在性，不以示例文件覆盖或要求
hash 相同。后续每次同步仍须重新执行停机、编辑器关闭、备份、`--check`、部署验证和
新鲜心跳门禁；本次成功不能替代未来的逐次检查。

### 2.4 2026-07-29 部署后只读黄金基线

部署后使用现有 v5 `SYNC_STATUS` 合同执行完整“上架中 + 待上架”只读扫描：

| 项目 | 结果 |
| --- | --- |
| batch | `BATCH-POST-DEPLOY-READONLY-20260729-03` |
| execution attempt | `ATTEMPT-POST-DEPLOY-READONLY-20260729-03` |
| result / snapshot | `RESULT-e7e5729d606b05ba4956a62d` / `SNAPSHOT-e7e5729d606b05ba4956a62d` |
| 合同 | `execution_mode=READ_ONLY`、`action_type=sync_status` |
| 结果 | `VERIFIED`；`snapshot_complete=true` |
| 页面完整性 | online/waiting 扫描与结束标记全部通过 |
| 副作用 | `side_effect_state=NOT_STARTED` |
| 结果文件 SHA-256 | `608f1efc79fd96588fe3193087a1e293d860bd8b598212bb69982250aea31d60` |
| 导入 | ACK、Runtime DB batch/receipt/snapshot 均为 `VERIFIED` |
| 收尾 | 队列清空、`stop.signal` 不存在、Worker 保持健康 `RUNNING` |

快照共有 17 项：`online_only=2`、`waiting_only=5`、`neither=5`、
`ambiguous=5`；其中 5 项为 `UNMAPPED_PRODUCT`，5 项为
`ABSENT_FROM_BOTH_LISTS`。这些是 13.5-2 映射与扫描器提取的输入，不阻止
13.5-1 合同评审，也不得被自动解释为写任务或授权。

同一部署下的 `-01`、`-02` 两次只读请求均在副作用开始前安全失败并完成失败归档。
用户随后确认小程序当时停留在尚未录入影刀操作的特殊页面，并恢复到正常页面；`-03`
随即完整通过。因此本基线不把前两次失败定性为已确认的选择器漂移，也不以失败快照
覆盖 `-03` 的可信完整投影。

## 3. 禁止重写资产

13.5 可以增加 Adapter Capability、观察合同、Automation Service 和 Web Presenter，
但下列已验收边界只能复用或以兼容方式扩展，不能另建旁路：

| 资产 | 冻结入口 |
| --- | --- |
| v5 请求、manifest、phase、result 与 hash | `build_listing_action_request`、`build_listing_action_reconcile_request`、`validate_listing_action_request`、`validate_listing_action_phase`、`validate_listing_action_result` |
| 上下架提案、发布、导入和唯一对账 | `propose_listing_action_batch`、`publish_listing_action_batch`、`import_listing_action_result`、`ensure_listing_action_reconcile_attempt` |
| 两页状态同步与原子投影 | `prepare_listing_sync_batch`、`publish_listing_sync_batch`、`import_listing_sync_result` |
| Review、位置事实和共享写锁门禁 | `evaluate_automation_gate`、`review_block_reasons` |
| Result Importer、Watchdog 和唯一 RECONCILE | `ShadowBotResultImporter.import_one`、`ShadowBotQueueWatchdog.inspect`、`_automatic_reconcile_payload` |
| Worker 领取、校验、phase 和结果发布 | `ShadowBotQueueWorker._claim_next`、`_validate_request`、`_execute_claimed`、`_write_phase` |
| 平台 v5 扫描与动作 | `_v5_scan_page`、`_run_listing_sync_v5`、`_run_set_online_v5`、`_run_set_offline_v5`、`_run_listing_action_reconcile_v5` |
| Runtime v13 历史兼容 | v4/v5 批次、operation/attempt、共享写锁、receipt、ACK 和 v13 迁移 |
| Web 安全 | 登录、Session、CSRF、POST logout、PRG、路径白名单和 Mobile Review token |

若新需求需要改变其中任何语义，必须先给出兼容证明和回归矩阵；不得以“定时任务”
“Web 按钮”或“S4 更紧急”为理由复制点击链路或绕过 Importer。

## 4. 脚本与入口盘点

分类含义：

- **正式服务**：长期运行进程，13.5 中继续维护。
- **薄 CLI**：保留命令行入口，但业务逻辑必须下沉到应用服务。
- **运维工具**：只做配置、健康、备份、部署、修复或受控迁移。
- **验收工具**：只用于测试、证据导出、故障注入或复算。
- **归档候选**：历史阶段专用；先完成引用审计，再决定移动或删除。

### 4.1 正式服务与启动入口

| 文件 | 当前分类 | 13.5 处理 |
| --- | --- | --- |
| `scripts/run_shadowbot_queue_services.py` | 正式服务 | 继续作为 Importer/Watchdog 服务；与 Automation Service 分进程 |
| `scripts/start_local.ps1` | 运维启动器 | 评估拆分 Web、Queue Service、Automation Service 生命周期 |
| `start_web.bat` | 运维启动器 | 保持 ASCII + CRLF；只启动 Web，不承载调度 |
| `scripts/local_env.example.ps1` | 配置模板 | 增加 13.5 配置时只写占位值，不写真实 secret |
| `scripts/local_env.ps1` | 本机私有配置 | 不提交、不复制内容、不进入文档或证据 |

13.5-3 新增正式入口 `scripts/run_automation_service.py`，但其长期逻辑位于
`app/services/`，脚本只负责配置、进程生命周期和退出码。

### 4.2 薄 CLI 与受控人工入口

| 文件 | 13.5 处理 |
| --- | --- |
| `scripts/evaluate_business_rules.py` | 下沉统一 evaluator service；CLI 保留 dry-run/apply 适配 |
| `scripts/run_shadowbot_listing_sync.py` | 转为正式只读扫描服务的手工触发适配 |
| `scripts/run_shadowbot_executor.py` | 保留明确 `task_id` 普通写入口 |
| `scripts/run_shadowbot_commit_batch.py` | 保留兼容；不得成为 Scheduler 的批量 pending 入口 |
| `scripts/reconcile_shadowbot_listing_skus.py` | 保留唯一 RECONCILE 的受控人工入口 |
| `scripts/run_mock_platform_executor.py` | 移入系统维护测试工具，不与真实任务混显 |
| `scripts/create_sample_workbooks.py` | 保留开发/示例数据生成，不进入运营主流程 |
| `scripts/generate_shadowbot_markdown_report.py` | 下沉报告服务或保留薄适配 |

### 4.3 运维、健康、部署与修复工具

| 文件 | 13.5 处理 |
| --- | --- |
| `scripts/check_runtime_env.py` | 正式运维检查 |
| `scripts/check_shadowbot_readiness.py` | 正式实机前置检查 |
| `scripts/check_shadowbot_worker_health.py` | 正式 Worker 健康检查 |
| `scripts/release_backup.py` | 正式备份/恢复工具 |
| `scripts/sync_shadowbot_test2.py` | 正式受控部署同步；不得由 Web 请求线程调用 |
| `scripts/verify_shadowbot_deployment.py` | 正式部署 hash 验收 |
| `scripts/repair_shadowbot_expired_attempt.py` | 管理员受控修复，默认不出现在运营首页 |
| `scripts/migrate_price_rules_scope.py` | 一次性迁移工具；完成引用审计后归档 |
| `scripts/setup_shadowbot_evidence_share.ps1` | 本机证据共享初始化；保持显式权限和脱敏边界 |

### 4.4 验收与证据工具

以下脚本保留为 CI、回归、故障注入或脱敏证据复算工具，不得被 Automation Service
当作生产业务入口：

```text
scripts/export_task12_sanitized_evidence.py
scripts/export_task13_already_applied_evidence.py
scripts/export_task13_multi_success_evidence.py
scripts/export_task13_preflight_zero_write_evidence.py
scripts/export_task13_round_trip_evidence.py
scripts/export_task13_sanitized_evidence.py
scripts/export_task13_serial_unknown_evidence.py
scripts/export_task13_unknown_reconcile_evidence.py
scripts/inject_shadowbot_stop_after_submit_intent.py
scripts/patch_shadowbot_queue_request_fault.py
scripts/prepare_shadowbot_commit_acceptance.py
scripts/prepare_shadowbot_e2e_chain.py
scripts/run_e2e_flow_tests.py
scripts/run_linux_core_tests.py
scripts/run_shadowbot_filequeue_recovery_acceptance.py
scripts/run_system_smoke_tests.py
scripts/verify_core_wheel_install.py
scripts/verify_packaging.py
scripts/verify_shadowbot_filequeue_acceptance.py
scripts/verify_task12_sanitized_evidence.py
scripts/verify_task13_already_applied_evidence.py
scripts/verify_task13_multi_success_evidence.py
scripts/verify_task13_preflight_zero_write_evidence.py
scripts/verify_task13_round_trip_evidence.py
scripts/verify_task13_sanitized_evidence.py
scripts/verify_task13_serial_unknown_evidence.py
scripts/verify_task13_unknown_not_applied_evidence.py
scripts/verify_task13_unknown_reconcile_evidence.py
scripts/verify_windows_core_fixture.py
```

### 4.5 归档候选

```text
scripts/build_task11_human_report_payload.py
scripts/build_task11_three_round_handoff.py
scripts/freeze_task13_v12_baseline.py
scripts/run_shadowbot_e2e_local_demo.py
```

它们在任务 14 交接前只标记为归档候选，不立即删除。必须先检查 CI、文档、证据复算和
运维手册引用；仍承担历史证据复算的脚本继续保留。

## 5. 13.5-0 合同草案

### 5.1 正交数据维度

```text
fact_source:
  ORDER_OBSERVED | SCAN_ESTIMATED

quality_level:
  ORDER_COMPLETE | ORDER_PARTIAL |
  SCAN_ESTIMATED_HIGH | SCAN_ESTIMATED_MEDIUM |
  SCAN_ESTIMATED_LOW | UNAVAILABLE

summary_status:
  PROVISIONAL -> OBSERVED -> RECONCILED -> FINAL
```

只有 `FINAL` 是终态。FINAL 后迟到数据创建新版本，并通过
`supersedes_summary_id` 指向旧版本；不得原地覆盖。

### 5.2 大扫描与能力结果

```text
FULL_MARKET_SCAN
├─ LISTING_STATUS_SCAN
│  ├─ online
│  └─ pending
└─ ORDER_SCAN
   └─ ORDER_HISTORY_IMPORT
```

Adapter 必须声明 `supports_order_scan`、`supports_current_trade_day` 和
`supports_historical_trade_day`。当前平台分别为 `true / false / true`。子结果使用：

- `UNSUPPORTED`：平台明确没有能力，属于预期能力结果。
- `UNAVAILABLE`：平台支持，但请求日期或数据范围当前不可用。
- `FAILED`：能力应可用，但本次登录、网络、页面、解析或运行失败。

两个子结果独立接受；一个失败不得使另一个已满足合同的事实失效。

### 5.3 订单多重集合

- `source_row_fingerprint` 只用于候选分组和完整性校验，不是 canonical ID。
- `occurrence_no` 表示同一批次中相同指纹的每个真实实例。
- `occurrence_count` 表示同一批次按指纹分组的对账计数，可派生或固化。
- 不得建立会吞掉真实重复行的指纹唯一索引。
- 跨批次比较多重集合，不把不同观察批次累加为销量。
- 结算只选择最新、已接受的完整批次；旧批次保留审计和差异。

### 5.4 库存估算区间

每个区间至少包含：

```text
interval_started_at
interval_ended_at
inventory_before
inventory_after
known_inventory_adjustment
known_adjustment_source_refs
mapping_version
estimation_eligible
estimation_reason
confidence
supporting_observation_ids
```

人工库存修改、上架重设、`target_inventory` 写入、无法解释的库存增加、期间离线、
映射漂移、扫描不完整、字段不可读或跨 18:00 无法逐项归属时，必须
`estimation_eligible=false`。

### 5.5 S4 扩展边界

核心 v14 只在 `tasks.origin_type` 预留 `SYSTEM_EMERGENCY`，并保留
`origin_ref_id / approval_policy / policy_version`。`emergency_action_policies` 的最终
字段、约束和迁移在 13.5-6 编码前冻结；Schema 版本号到该阶段再决定。

## 6. 受影响文件与子 PR 顺序

| 子 PR | 主要影响范围 | 前置门禁 |
| --- | --- | --- |
| 13.5-0 | `docs/plans/`、文档索引、当前状态、审计脚本清单 | 不改业务代码或 Runtime DB |
| 13.5-1 | `app/runtime_schema.py`、`app/models.py`、`app/enums.py`、`app/repositories/sqlite_runtime_repository.py`、时间服务和迁移测试 | 质量矩阵与日结状态机先评审 |
| 13.5-2 | `app/services/shadowbot_listing_sync.py`、新扫描/映射服务、ShadowBot adapter、Importer 和扫描测试 | Worker 部署状态先收敛 |
| 13.5-3 | 新 Automation Service、`scripts/run_automation_service.py`、租约/调度/健康测试 | 13.5-1/2 合同稳定 |
| 13.5-4 | 订单 Adapter、不可变订单批次、Importer、Repository 和证据 | 多重集合合同先冻结 |
| 13.5-5 | 销售估算、结算、计划输入、报告和 Web 只读投影 | 库存估算区间合同先冻结 |
| 13.5-6 | Incident、重复通知、人工闭环、策略结构、`SYSTEM_EMERGENCY` | 人工闭环与支撑数据先评审 |
| 13.5-7 | Web/CLI/Scheduler 公共应用服务、任务来源、脚本收口 | 不引入第二 gate 或第二队列 |
| 13.5-8 | `app/webapp/` 架构、模板、静态资源、兼容路由和 wheel | 仅架构拆分，不改变业务语义 |
| 13.5-9 | 八个运营入口、Presenter、响应式、文案和可用性 | 对应后端合同已可读 |
| 13.5-10 | 全量回归、实机证据、交接报告和任务 14 tag | 所有阶段门禁通过 |

每个子 PR 独立迁移、测试和回滚；不得把 v14、Automation、订单、S4 和 Web 重写合成
一个无法审查的大改动。

## 7. 正式开工判定

### 7.1 已满足

- [x] main 与 origin/main 对齐并记录 SHA。
- [x] Runtime Schema v13、脱敏 DB 快照和代表性证据已冻结。
- [x] 全部脚本和现行 Web 路由已盘点并分类。
- [x] 禁止重写的 v4/v5、Importer、写锁和 RECONCILE 资产已列出。
- [x] 独立 Web 审计文档已形成。
- [x] 数据质量、日结、多重集合、库存估算和 S4 扩展边界草案已形成。
- [x] 受影响文件和 13.5-0 至 13.5-10 子 PR 顺序已形成。
- [x] ShadowBot 生命周期、部署 hash 和部署后完整 READ_ONLY 基线已收敛。
- [x] 13.5-0 独立分支与 main commit 回滚点已建立。

### 7.2 正式编码前仍需执行

- [x] 本文件和本地实施计划已纳入 13.5-0 分支的文档评审范围；仍待提交 PR 审查。
- [x] 本轮 main 新鲜完整回归通过：`679 passed, 3 skipped, 97 subtests passed`。
- [ ] 在 13.5-1 迁移编码前评审
  [六级质量矩阵与日结状态机合同](task13_5_1_quality_and_settlement_contract_review.md)。
- [x] 本轮 ShadowBot 请求和代码同步前置门禁已收敛；后续每次请求仍须逐次复核。
- [x] 13.5-0 已建立独立分支、验收清单和回滚点；13.5-1 至 13.5-10 随各阶段建立。

满足上述对应门禁后再进入相应阶段；“可以开始 13.5-0”不等于“一次性批准全部
13.5 实现”。

## 8. 13.5-0 子 PR 验收与回滚

本子 PR 只允许包含：

- `AGENTS.md` 中任务 13.5 的现行权威和边界。
- `docs/plans/task13_5_*.md` 计划、对齐评估和独立 Web 审计。
- `docs/index.md`、`docs/project_current_status.md`、复用手册和旧 Web 计划的权威指向。

明确排除：

- Runtime DB、队列、生命周期 JSON、ShadowBot 应用目录和本机配置。
- Schema v14、Automation Service、订单、销售日结、Incident、S4 和 Web 业务实现。
- `.codex_tmp/`、任务 12 DOCX 和其他用户自有未跟踪文件。

合并前必须通过：

1. 所有纳入范围的 Markdown 使用 UTF-8 回读，标题和中文样例正确。
2. 相对链接目标存在，Issue #20 外链保持唯一宏观权威。
3. `git diff --check` 通过；本分支没有业务源码或 Runtime DB 变更。
4. 基线中的 main SHA、测试数量、Run ID、hash、数据库回读和 Worker 状态彼此一致。

若审查不通过，回滚到
`268c4ed9facd1ec39698843301d31fff9e860495`，只撤销本分支文档变更；不得用 Git
覆盖 Runtime DB、队列、生命周期文件或 ShadowBot 应用目录。
