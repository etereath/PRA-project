# Task 13.7-1：P1-47-01 人工终态收口修复

角色：Implementation / Validation Evidence；日期：2026-09-07。源码与测试版本 `b22420887a1df7129ba67dd7d4f9a858fd2ccf2e`；文档提交不改变该源码。承接 [PR #47](https://github.com/etereath/PRA-project/pull/47) Head `ceb88a0f554bbeb4ff3de3c3470aa6c63f7235dc`，main 仍为 `f227cd2517687e4a6dfadea90c2e126a5da69711`。同一分支继续，保持 Draft；未合并、未部署、未操作真实平台。

Task Type：Bugfix / Integration；Review Profile：R4，含 R3 人工授权及 UNKNOWN 安全边界。

## 问题与结论

[首审](https://github.com/etereath/PRA-project/pull/47#pullrequestreview-5126336815)冻结 1 个 P1 blocking、2 个 P2 nonblocking、0 个额外 Merge Gate。旧版本已证明失败：唯一 RECONCILE 无法确认后仅显示 HUMAN，普通 MANUAL_HANDLED 备注既不能证明平台事实，也没有原子结束 Task、operation、continuation 和锁的正式入口；新决定会一直被 predecessor 阻塞。

本次修复 P1 与直接回归。授权前 already-applied 和关闭后的授权回执措辞两项 P2 保留，不扩展到其他业务切片。

| 判断维度 | 结果 | 范围 |
|---|---|---|
| P1 实现 | COMPLETE / 待正式复核 | 正式 Web 入口、平台证据、旧执行停止检查、原子收口、后续新决定可执行 |
| Implementation Review | 首审 FAIL；修复待 ChatGPT 复核 | 开发者回归结果不代替审核者关闭 P1 |
| 隔离链路 | PASS | 真实应用、publisher、file Queue/Worker、Importer；平台边界为隔离替身 |
| Merge Gate | NOT RELEASED | 最终 Head CI 与 P1 正式复核分别核验，负责人决定合并 |
| Stage Goal / 实机 / 长期运行 | NOT YET VALIDATED | 未使用生产 Runtime 或真实平台，没有现场写入、重启及通知送达证据 |

## 正式操作与证据

1. Queue Service 在 HUMAN 分支生成稳定 ID 的 `price_execution_unknown` Review 和 Outbox 通知。初始负责人为原最终授权主体；重启修补交接，不创建第二次 COMMIT 或 RECONCILE。
2. 操作者登录经营管理，从待处理复核中的“打开人工核验”或任务/复核详情进入。提交和接手均要求已认证主体、`HANDLE_REVIEW`、CSRF；通用“通过/取消/到期”与 Mobile token 入口不能完成此类型复核。
3. 检查原 COMMIT、唯一且绑定源 attempt 的 RECONCILE 已结束、租约不再 active、旧 ready/working 请求已归档。唯一对账须有已返回的结果身份与摘要；只发生超时或租约到期、仍有旧请求、缺失结束回执时继续冻结，由原负责人恢复/隔离原执行链并核对回执，不凭时间或备注解锁。
4. 停止边界成立后使用既有 `MANUAL_REVIEW` operation 和 `REVIEW_BLOCKED` 锁表示人工等待。仍禁止同商品冲突写入，但不再用 UNKNOWN 的全局 UI 优先级阻断已有只读自动扫描。若停止边界尚未证明，原 UNKNOWN 保护保持有效。
5. 通过已有完整市场/商品状态扫描取得 v5 READ_ONLY 快照；运营 Web 可配置既有扫描方案，扫描执行仍由原 Automation/Worker 负责。等新快照入库后刷新详情，不新建第二个 RECONCILE。
6. 人工从服务器展示的合格证据中选择结论：“当前目标已满足，终止旧决定”，或“当前目标未满足，终止旧决定，后续重新决定”。备注只作说明，不参与平台事实判定。

合格事实来自已入库的 `listing_sync_snapshots/items` 与 `shadowbot_listing_result_receipts`：完整 VERIFIED 快照、唯一线上/待售位置、平台/SKU/页面身份一致、回执与 attempt/instruction 绑定、有效结果/manifest 摘要、扫描晚于旧执行停止边界、观察不超过 30 分钟且不在未来。当前价格投影必须仍指向此观察，不得已有更新快照；正在运行的平台自动观察结束后才可提交。Web 与原授权还须匹配 queue root、profile 和 applet 摘要。本切片沿用当前单平台账户配置，没有把 platform_name 虚构成独立 account_id。

新鲜观察只能说明当前平台价格，不能反推历史未知写入成功。两个结论都终止旧 one-shot，原 batch、item、attempt 和 receipt 中的 UNKNOWN 历史保留。

## 一致收口与记录位置

`PriceExecutionResolutionApplicationService.resolve` 在 `BEGIN IMMEDIATE` 获取写事务后重新读取当前时钟、Review owner、旧执行、证据及锁归属，再一次提交：

- Task → `skipped`（明确终止旧决定）；
- operation → `MANUAL_HANDLED`，同时记录 `resolution_status/resolved_by/resolved_at`；
- 仅释放归属于原 operation、child attempt 和 write identity 的锁；
- Review → `cancelled`，业务含义为旧决定已终止；详情展示人工结论，撤销旧 token 与未发送通知；
- 整批已收口时 continuation → `HUMAN_RESOLVED` 并关闭；批内仍有其他未决对象时继续由 Coordinator 负责，不提前丢弃它们。

主记录为 `task_status_history.metadata_json`，reason 为 `price_execution_human_resolved`：主体、处理时间、Task/operation/batch、结论及中文标签、证据 ID/摘要/价格/观察时间/平台/SKU/页面身份、停止边界、请求摘要、备注和 `historical_side_effect=UNKNOWN`。Review 的 resolution payload 指向该 history ID。固定 operation 对应唯一 history ID；相同主体和完全相同请求可幂等重放，冲突请求拒绝。旧对账的无结论回执重放不会覆盖人工终态。

释放锁之后，新 Task 仍需正常预览、最终授权、写前比较及写后回读；此入口没有平台写权限，不维持旧目标。

## 人工复核超时

| 情形 | 负责人与推进 | 保留的约束 |
|---|---|---|
| 第一次逾期 | 原负责人继续负责，原 Queue Service Review 维护生成一次持久提醒 | 不判定平台成功/失败，不关闭 Review，不释放锁 |
| 第二次及后续逾期 | 提醒内容升级到指定接手人或运营管理员，保留原 owner | Outbox 发送结果独立记录，入队不等于送达或接手 |
| 他人接手 | 有权限的已登录用户点击“确认由我接手”，history 记录前后 owner、主体与时间 | 通知收件人、显示名字和自动时间推进均不能代替接手确认 |
| 通知异常、服务重启 | 原 Outbox 重试/不确定发送保护及 Queue Service 后续周期继续 | Review 及 owner 持久保留，不用通知失败作为业务终态 |

复用当前配置的通知通路与运营接收目标；升级主体进入提醒正文和 Review payload，不声称已通过另一个私信通路直接送达。该类通知引导登录经营管理，不生成可直接处置的 Mobile token。

| 配置 | 默认及含义 |
|---|---|
| `PRA_PRICE_REVIEW_REMINDER_MINUTES` | 30；允许 1～1440，初次处理期限和后续提醒间隔 |
| `PRA_PRICE_REVIEW_ESCALATION_SUBJECT` | 空时显示运营管理员；第二次逾期起的候选接手主体 |
| 现有通知 channel/recipient 配置 | 继续决定实际发送通路和接收目标；本次未修改生产配置 |

没有新表、Schema 迁移、daemon 或 Queue。复用现有 Task history、Review、Outbox、v5 快照与 v18 continuation。直接修正到期跳过分支引用不存在的 `skipped_review_tasks` 字段，改用已有 `skipped_source_tasks`，避免本类复核进入到期维护时崩溃。

## 回归与交付证据

`tests/test_price_execution_resolution.py` 的六组定向回归覆盖：正式 Web 收口和重复提交、Queue Service/Web 重启、随后新改价完整执行；目标已满足仍保留历史 UNKNOWN；权限/无证据/错误证据/过期/活跃执行/结论冲突拒绝；关闭事务末端失败时全部回滚；新观察使旧页面证据失效；超时催办幂等、升级、显式接手、通用入口封堵；旧 v4/RECONCILE 回执重放；其他 SKU 正常获得执行。

COMMIT 和 RECONCILE 使用生产流程及最低层 UI 替身；新增 v5 观察由平台结果 fixture 提供，再经过真实 publisher、file Worker 与 Importer，未直接插入成功快照或替换关键交接。隔离测试不能证明真实页面、通知送达及真实账户配置可用。

本地全量命令为 `python -m pytest -q tests --disable-warnings --junitxml=<隔离证据目录>/p1-final.xml`；完整 Windows 门禁和 Linux Core 继续由原 Core CI 执行。[PR 说明](https://github.com/etereath/PRA-project/pull/47)按修复源码和最终 Head 分别记录本地回归结果与 CI 链接，不使用首版 `8a1040d` 的测试数字替代本修复版本。复核时先确认 PR Head 没有再次前进。

解释器与环境：Python 3.11.15，`C:\Users\etere\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`；PowerShell 7.6.5 Core。新增/修改的 11 个源码和测试文件严格 UTF-8 回读、首行和中文样例检查、AST 解析通过；Markdown 写后同样回读并校验相对链接。未新增 CSV/TSV/bat，未读取生产凭据、配置或 Runtime。文件编码正确、隔离业务通过、真实平台未验证分别报告。
