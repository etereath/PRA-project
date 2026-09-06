# 文档来源与入口迁移记录

角色：Current Documentation Map；主角色规则见[索引](../index.md)，当前阶段见[状态](../project_current_status.md)。本表仅映射出处和处置，不复制业务规则。

| 原入口/输入 | 本次处置 | 现役位置/证据 |
|---|---|---|
| README | 重写短入口，去旧页面/Excel 库存能力声明 | 产品/业务/实现/架构阅读集 |
| docs/index | 按问题与角色导航 | 本次唯一文档身份入口 |
| project_current_status | 缩为阶段/能力/证据边界 | 原长时间线固定 SHA 保留 |
| docs/project_overview + doc/project_overview | 前者产品/Roadmap，后者转向 | 不再并列维护两套当前总览 |
| G1 baseline draft | 正文升级到 business_contract，原路径转向 | PR #43 的 Closure 与 G1 报告保持原样 |
| G2 current map | 指定生产 SHA，压缩当前链与差距 | 原 G2 内容可由 Git 版本追溯 |
| G2 target matrix + incremental addendum | 有效职责及 IG-01～IG-11 合入 target matrix | 增量报告/补充原样保留，PR #44 仅 donor |
| business_decision_spec | 历史转向入口 | 旧时间/库存语义不再作为当前规则 |
| ai_agent_integration_spec | 转向当前架构 Agent 章节 | 旧 Task14 排除规则不生效 |
| governance v0.1 | 同路径承接用户提供 v2.0，并明确 R1～R4 配置与复杂度策略 | 不创建第三套相似治理规范 |
| 临时 AGENTS | 正式候选审查后原样 archive 再替换 | 独立 cold-start 必须读实际生效正式版 |

规划及实施基线：`08041bfe25a7f31f032564a2abca35e5eb5f5330`。原文件均可通过[此 Git 版本](https://github.com/etereath/PRA-project/tree/08041bfe25a7f31f032564a2abca35e5eb5f5330)追溯；原报告、归档和 evidence/hash 绑定不随入口整理改写。

审核方法来源：用户在本次 Task13.6-3 会话明确提供《PRA 项目审核治理规范 v2.0》并要求吸收历史总纲开发策略。未获得独立《Codex Development Workflow v2.0》全文，不假称已导入该文件；可确认的开发方法进入现役治理与 AGENTS。

撤回意见：要求当前必须有自动销售 Controller、以物理库存封顶 Exposure、认定 19:00 必然混入新日订单，均不作为 13.7 待修缺陷。旧 20:00 双日界、旧工作包编号不随历史策略吸收而恢复。旧 G1 候选及 G2 INPUT 状态由正式 Gate 与当前状态覆盖。
