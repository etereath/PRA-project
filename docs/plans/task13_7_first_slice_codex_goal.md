# Codex Goal：Task 13.7-1 首条人工改价闭环

在 `etereath/PRA-project` 实现首条纵向切片：1 SKU、一次人工 UPDATE_PRICE，从 Human Intent、Runtime Task、正式人工授权、持久交接，经现有v4/file Queue/Worker/Importer到明确终态与平台回读，并证明重启或blocker解除后有人持续推进。

先读根级AGENTS与当前状态，再读 `docs/plans/task13_7_human_update_price_vertical_slice.md` 及其引用的业务合同、实现图和目标职责。Task13.6已由负责人验收PASS，接受语义版本为 `4d51f51edcafc4168149928f6ee64467cd12421a`；不要重新要求负责人确认13.6。

PR #46已合并，交接分支 `codex/task13-7-1-human-update-price` 已从 main `f227cd2517687e4a6dfadea90c2e126a5da69711` 创建。fetch 后跟踪此远端分支，在它的现有 Draft PR 上承接实现，不需要先合并文档PR或另建重复计划PR。先检查branch/worktree并保留他人改动，核对GitHub最新main/PR Head、正文/评论、changed files、提交、AGENTS、相关源码/测试/CI；若远端已前进，按最新Head接续，不覆盖提交。

你是本切片的开发主体，负责实现、测试和证据；ChatGPT负责业务/架构/代码审核，负责人负责现场验收与合并决定。当前PR初始提交只有交接材料，不能当成业务实现；完成局部复用分析后继续施工，不停在重新生成计划。

优先复用Task/origin/history、v4 batch/items、operation/attempt、write lock、receipt、Importer/Watchdog、Review/Outbox。先在实现PR说明中明确：最新决定保存在哪里；最终授权怎样形成可恢复交接；Coordinator如何找到它、避免重复派发并收口。必要时再做最小持久结构调整，不机械新增Intent/Task/Attempt三张表。

特别核对现有事实：Web prepare就可能持久化PREPARED批次，它不等于已最终授权；submit现有AUTH history写入与v4 publish之间有崩溃窗口。Coordinator托管现有长期Queue Service，只拥有明确持久授权交接，不能扫描所有pending Task或PREPARED batch自行执行。保留主体/capability、精确scope/目标、有效期、幂等与执行前重验。

旧Task开放不能拒绝记录新的有效人工决定。同SKU价格决定按副作用边界替代或等待旧执行收口，回读后必要correction仍走正常授权。外部人工漂移、已人工完成、TTL失效、UNKNOWN/唯一RECONCILE均要明确出口；不无限维持旧one-shot目标，不猜测重写。

同步接入Web，优先使一条链运行，再补该链的直接异常与恢复。单continuation/Coordinator业务异常不得拖垮Importer/Watchdog/Review/Outbox。真实写保持写前读取、比较旧状态、执行、写后确认。不要重写成熟v4/v5或新建daemon/Queue。

本切片不顺带实现Exposure、Supply、Commitment、Closing、Health、旧Settlement authority切换、自动销售Agent或跨平台Allocator。若纯改价未触及Exposure，不无条件捆绑两处库存上限修改；不得以此为由省略价格决定与授权后的持续owner。

用同一隔离Runtime和正式Service/file Queue/Importer验证完整旅程，覆盖未授权不执行、最终确认边界崩溃、blocker恢复、单对象异常隔离、新旧决定/漂移及未知执行。只替换外部UI/平台边界，不把关键交接替换成成功stub。保持现有CI；读取新Head结果。真实平台写入须有相应现场授权与受控方案；无实机证据时，不把隔离测试写成真实闭环PASS。

在同一PR交付代码、必要迁移/运行说明和绑定SHA的证据，更新PR说明与当前状态，列明实现范围及未验证项。分别报告Implementation Review（P1/P2/Merge Gate）与Stage Goal Validation（PASS/FAIL/NOT YET VALIDATED），说明哪些已实机验证、哪些尚未验证。首审冻结blocker，复审只处理原问题和直接回归。未经明确要求不merge、不结束Draft、不操作无关分支。最终说明是否执行合并。
