# Task 13.7-1：已满足目标的确认与授权回执修复

角色：Implementation / Validation Evidence；日期：2026-09-07。源码与测试版本 `5604a4d8807d49ba70d09bd5dc6c2e603df970a5`；后续文档提交不改变该源码。承接 [PR #47](https://github.com/etereath/PRA-project/pull/47) 的 P1 交付 Head `869f1a4d7ccff6a83293551b5d68f8202dd255d6`，main 为 `f227cd2517687e4a6dfadea90c2e126a5da69711`。按用户“开始修复并推送”授权修复两项非阻塞 P2，继续原分支与 Draft PR；未合并、未部署、未操作真实平台。

Task Type：Bugfix / Integration；Review Profile：沿用 R4，含 R3 授权边界。复核范围为首审 P1、此次明确授权修复的两个 P2 及其直接回归，不扩大到其他业务切片。

## 问题与复用

[首审](https://github.com/etereath/PRA-project/pull/47#pullrequestreview-5126336815)冻结 1 个 P1 blocking、2 个 P2 nonblocking、0 个额外 Merge Gate。[P1 修复报告](task13_7_1_p1_human_resolution_20260907.md)保留其指定 SHA 的历史证据；本报告补充以下实现。

| 问题 | 原行为 | 修复后的业务行为 |
|---|---|---|
| P2-47-01：最终授权前已人工达到目标价 | 首次 Task 预期旧价 12、目标价 13；外部员工已改为 13 后，prepare/submit 因旧价不符拒绝，任务没有正常结束入口 | 新鲜观察证明目标已满足时，Web 明示“无需改价，确认结束本次决定”；用户确认后，原 Queue Service 复核观察并原子结束决定 |
| P2-47-02：关闭后的旧授权回执仍称继续推进 | 相同幂等请求只返回批次 ID，Web 缓存通用成功元组；即使授权已经关闭，页面仍显示执行服务将继续推进 | 重放返回原批次的持久 outcome、closed_at、message；同一回执页面每次 GET 刷新持久状态，展示实际进度或终态及任务详情链接 |

复用 v18 continuation、v4 PREPARED batch、Task/history、原 Coordinator 和 Web 回执缓存。没有新表、迁移、daemon、Queue 或第二种人工复核。删除原通用成功回执的固定文案路径，沿既有 DTO 传递状态。

## 结束决定的授权边界

1. prepare/submit 允许“当前观察价格等于目标价”，同时保留主体/capability、精确任务集合、映射、成本、库存 authority、有效期、观察来源与 30 分钟新鲜度、上下架状态、Review、锁和 predecessor 校验。仅预览仍没有执行 continuation，也不会自动结束 Task。
2. `resolution_only` 写入确认摘要、不可变授权 envelope 和 AUTH history。它表示此次确认只允许结束决定。部分商品已满足、部分仍需改价时明确拒绝整批并提示分别选择，不静默减少任务范围。
3. 用户确认后仍由既有 Queue Service 接手。Coordinator 复核新鲜观察；`ALREADY_APPLIED` 在同一 SQLite 写事务中再次核对 Task 版本、观察来源/时间/价格/上下架状态及无已发布工作，然后将 Task 置为 `skipped`，关闭 continuation，保存观察证据。没有 COMMIT attempt、operation、写锁或 ready 请求。
4. 预览后事实变化使确认摘要失效；接受后价格不再满足时结束此次授权为 `RECONFIRM`，Task 保留，交还用户重新预览。发布器另有硬校验，拒绝用 `resolution_only` 授权进行任何平台写入，不能把“结束决定”自动转换成改价权限。
5. 观察读取与收口事务之间发生价格/上下架变化时，本轮不关闭 Task，由原服务下轮重新核对。既有 blocker 继续由原服务在授权有效期内检查；超时仍沿原 EXPIRED 出口，不无限保持旧价格目标。

Task `skipped` 表示本次决定无需再执行；ALREADY_APPLIED history 保存平台观察来源和时间，不伪造本系统写入成功。P1 的 UNKNOWN 人工收口仍走正式 Review 和证据入口；价格恰好匹配不能绕过未决 predecessor 或写锁。

## 回执与重放

旧授权重放保留原主体、幂等键、确认摘要和精确 task_ids 绑定，校验 envelope 摘要，仅读取原 continuation；不重新授权、不发布、不重新开放关闭的记录。展示 ACCEPTED/BLOCKED/TRACKING/RECONCILING/HUMAN/RETRY_PENDING，以及 COMPLETE/EXPIRED/RECONFIRM/SUPERSEDED/ALREADY_APPLIED/HUMAN_RESOLVED。终态显示结束时间和真实消息，历史消息缺失时显示“本次授权已结束”。

Web 缓存保留会话所属回执身份；GET 从 Runtime 更新状态，避免最初 ACCEPTED 回执长期滞留。读取异常明确显示暂不可用并提供任务详情，不以缓存值声称服务仍在推进。重启后原 POST 可凭持久幂等记录获得终态回执。v5 沿用原投递回执，明确提示“已投递，请查看任务详情”，没有虚构不存在的 v4 continuation。

## 验证证据与限制

新增 24 个定向用例：`tests/test_price_authorization_no_write.py` 的 11 个用例通过正式 Web、真实授权服务、发布器和重启后的 Queue Service 验证零写结束、冻结权限、过期/无来源观察、混合范围拒绝、未决前驱、收口竞争、页面刷新和读取失败；`tests/test_execution_authorization_receipts.py` 的 13 个用例验证 12 种持久状态和缺失历史消息，核对重放后的 AUTH/continuation/attempt 数量和身份绑定。

原授权及完整链路 31 个用例通过，P1 人工收口 6 个用例通过。P2 零写链路的平台价格由隔离观察 fixture 提供；测试没有宣称实际人工平台操作已经发生。P1 回归复用生产 Worker/Importer 和最低层 UI 替身。

首轮全量与 Linux CI 检出一项旧展示测试仍传入两元素回执；已更新为正式 DTO，保留原内部批次/attempt 标识隐藏断言，并补充 run 标识隐藏与任务链接断言。修正后 Web foundation 34 个用例通过；该测试修正提交未改变生产源码。

本地全量命令：`python -m pytest -q tests --disable-warnings --junitxml=<隔离证据目录>/p2-final.xml`。全量结果、源码 SHA 和最终 Head 的 Windows/Linux Core CI 链接记录于 [PR 交付说明](https://github.com/etereath/PRA-project/pull/47)，不能用上一次 P1 的测试数字或 CI 代替本次版本。

| 判断维度 | 结果 |
|---|---|
| P2 实现及定向隔离验证 | COMPLETE / PASS；待正式复核 |
| Implementation Review | 首审 FAIL；P1 与两个 P2 修复等待审核者复核，不由开发者自行关闭 |
| Merge Gate | NOT RELEASED；当前 Head CI 与正式复核分别确认，合并仍由负责人决定 |
| Stage Goal / 实机 / 长期运行 | NOT YET VALIDATED；未使用生产 Runtime 或真实平台，未开展现场重启及通知送达验证 |

环境：PowerShell 7.6.5 Core、Python 3.11.15；所有修改源码/测试/Markdown 严格 UTF-8 回读，验证首行与中文样例；Python AST、Markdown 相对链接与 Git 空白检查通过。未新增 CSV/TSV/bat。文件编码正确与隔离业务通过分别证明，控制台显示不作为文件内容真值；真实业务目标仍待受控现场验收。
