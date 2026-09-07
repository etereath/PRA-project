# AGENTS.md — PRA 项目工作指令

本文件是仓库工作入口。业务主定义、实现事实和当前阶段分别由下列文档承担，不把历史计划当永久权限。

## 先读什么

1. `docs/project_current_status.md`：当前阶段、证据与开工门槛。
2. `docs/project_overview.md`：产品目标与路线。
3. `docs/business_contract.md`：当前业务定义。
4. `docs/rebaseline/task13_6_current_implementation_map.md`：指定 SHA 的实际实现。
5. `docs/rebaseline/task13_6_target_responsibility_and_gap_matrix.md`：目标 owner、复用/gap、IG-01～IG-11、13.7/14 边界。
6. `docs/pra_review_risk_and_complexity_governance.md`：审核和开发方法；其他材料按 `docs/index.md` 身份读取。

## 工作判断

- PRA 是鲜切花持续观察、人工经营决策、可靠执行与恢复系统。当前 Controller 是 Human/Operations Web；未来 Agent 经过既有校验、授权、执行与恢复接口。
- 每次新任务先核验最新 main/PR head、正文/评论、changed files/commits、适用 AGENTS、相关文档、正式生产入口与调用链、测试/CI。报告指定 SHA；合并、部署、实机、长期运行分别证明。
- 对业务应该怎样运行、代码实际怎样运行、验证到何程度，分别采用最新用户裁决、指定 SHA 源码和相应范围证据。历史设计可修订，原始历史事实不改写。
- 同时审业务可行性、安全正确性、开发维护效率。出现施工边界妨碍目标时，显式 `BOUNDARY CONFLICT`：证据、后果、最小替代方案；不静默扩权，也不增加旁路掩盖矛盾。
- 检查完整业务旅程：非终态的 owner、下一步、触发、失败、重启和收尾。人工等待授权可以合法；已最终确认后的执行不能只靠页面或内存承担恢复。
- 逻辑责任不等于新表/状态机。新增复杂度须证明具体事故与复用不足；优先既有 v4/v5、Queue、Worker、Importer、Watchdog、Review/Outbox 和库存 ledger。
- Review Profile 按影响选 R1～R4，Task Type 单列。首审尽量冻结完整 blocker，复审检查原问题与直接回归；用户明确要求完整实现重审时重新完整审。较大任务分别给 Implementation Review（P1/P2/Merge Gate）与 Stage Goal（PASS/FAIL/NOT YET VALIDATED）。

## 轻量化开发与验证

- 增量读取：首次建立规则、版本和调用链基线；同一任务后续只核验变更、新评论及受影响片段，复用已读且未变的材料。新会话仍加载适用规则，不把无上下文的旧结论当基线。
- 接口联动：修改函数、DTO、状态或返回结构时，先搜索生产调用处和测试调用处，一次列出受影响范围并同步适配，避免到全量回归才发现旧测试输入。
- 开发期默认定向验证：Bugfix 覆盖原问题复现、修复、直接回归和必要集成；展示/文档修改不自动带上整套系统验收。Review Profile 按本次实际影响选择，不因父任务为 R4 就继承全部验证投入。
- 正式提交审核或阶段交付前，在稳定候选提交集中完成完整门禁。已有 CI 能完成同等门禁时，默认不再本地重复全量；新增改动、失败或未解除的风险才扩大或重跑，并简述依据。通过的证据按源码、测试、依赖和环境的变化范围复用。
- 测试结构按责任分层：状态枚举、文案和纯转换优先轻量参数化测试，避免每个分支重复初始化工作簿、Runtime 和整条授权链；持久化、身份绑定、幂等及终态使用必要的真实数据库测试。涉及授权、事务、并发和恢复的关键交接仍保留真实集成验证。
- 文档集中收口：开发中保留简短变更与验证记录，交付时统一更新必要报告、PR 说明和状态入口。仓库报告绑定已存在的实现/测试提交；最终 Head 与 CI 链接放 PR 说明，避免为更新文档自身 SHA 反复提交。不为每次小修新增独立长报告或改写历史证据。
- 提交与推送分开：可本地小步提交，相关验证完成后集中推送；遵循用户明确的推送时机。仅修改本文件或整理过程记录不自动意味着需要推送、触发整轮 CI 或开展全量回归。
- CI 分层通过工作流落实：开发期轻检、纯文档检查、正式交付完整门禁；同环境同输入下，已被全量覆盖的测试子集不重复运行，有独立环境或隔离目的的检查保留。工作流调整须纳入实际任务范围；本规范不自动改变当前触发规则或绕过既有 Gate。
- 工具输出保持精简：独立检索合并调用，优先局部 diff；正常日志只读结果，失败时再取相关细节。等待期间减少无变化轮询，进度更新简短。含中文文件每批写后仍严格回读验证，只输出摘要及必要样例，不反复回显整份文件或大量 Unicode 转义。
- 轻量化保留主体授权、精确范围、幂等、原子事务、UNKNOWN/唯一 RECONCILE、凭据保护和编码硬约束。文件正确、测试通过、CI 通过及真实业务验收分别陈述；不以减少成本为由删去必要断言或虚报完成。

## 硬边界

- 真实写经正式授权链：写前读取→比较预期旧状态→执行→写后读取。外部员工修改平台是正常经营；过时 Intent 不默认写回。
- UNKNOWN/NEEDS_RECONCILIATION 不能用第二次猜测写解决；沿既有唯一 RECONCILE，不能猜测平台事实。
- PENDING 未授权 Task 不自动执行；Coordinator 只拥有已形成持久 execution continuation 的对象。Observation S4 不继承 Emergency S4 下架权限。新旧业务 authority 不得同时扣减/生成计划。
- 区分 platform_name/account_id/internal_sku/platform_product_identity；平台 UI/登录/selector 留在 Adapter/Executor/ShadowBot。
- 凭据、完整 Review token URL、本地生产配置及 Runtime 数据不进入 Git/日志/公开证据。
- 开始修改前检查 branch/worktree，保留他人改动。文本 UTF-8 严格回读；历史/hash-bound evidence、原样 archive 不为格式修改。按实际风险验证，不用 CI 代替业务验收。

## 阶段与权限

- 当前阶段只从状态页读取。Task13.5 STOPPED/SUPERSEDED；旧 7G 不继续。Task13.6 Overall PASS 前不开始 13.7。
- 13.6 限纯文档；不修改生产代码/Schema/运行配置/真实 DB/Queue/Worker，不运行平台操作。其目标是认知和交接准确，不要求提前修完 13.7 缺口。
- 正式 AGENTS 替换与 Canonical 收口完成后，由项目负责人主持不携带历史聊天的独立 AI cold-start；实施者可准备中性问题和分开的评分依据，不以自答或实施者预检代替正式验收。新会话启动前，受测工作树必须已放置固定 SHA 的正式 AGENTS；在已注入旧指令的会话内补读正式版，不能消除已注入上下文。记录原始输入/回答、版本和环境限制，语义修订按直接影响定向复核，方法见计划与治理。负责人对最终交付确认前，Overall 保持 NOT YET VALIDATED。
- 用户当前明确授权优先且在会话内持续有效。未经明确要求不 merge、不结束 Draft、不修改无关分支/远端状态。创建任务分支和提交 PR 的授权不等于合并或部署授权。最终报告说明是否合并。
