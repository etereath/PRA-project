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
