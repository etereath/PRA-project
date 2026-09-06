# 项目文档同步检查（2026-07-11）

## 检查结论

本轮重点核对 ShadowBot 当前状态、下一步、开发规范和历史验收报告。发现的主要冲突是：提交意图后停止、真实 UNKNOWN→RECONCILE、登录/网络/证据故障注入已经完成，但部分索引、当前状态和历史报告仍把它们列为未来任务。

现已统一为以下口径：

- 已完成 8 小时 READ_ONLY 常驻 Worker 观察。
- 已完成提交意图后 `stop.signal` 实机验收。
- 已完成真实商品 `COMMIT -> UNKNOWN -> 自动 RECONCILE -> VERIFIED`。
- 已完成登录失效、网络异常、证据目录不可写和证据 hash 不一致实机注入。
- 白屏没有稳定实机触发机制，只声明分类逻辑和单元测试，不声明实机通过。
- 当前剩余运维项是长期告警、磁盘清理、证据保留、服务账号运行，以及元素版本漂移/白屏的可重复测试夹具。
- 项目仍不承诺无人值守生产改价。

## 已修正文档

- `docs/index.md`
- `docs/project_current_status.md`
- `docs/shadowbot_wechat_price_update_development_spec.md`
- `docs/business_decision_spec.md`
- `docs/review_token_implementation_plan.md`
- `docs/reports/shadowbot_8h_readonly_observation_pass_20260703.md`
- `docs/reports/shadowbot_filequeue_real_machine_acceptance_20260701.md`
- `docs/reports/shadowbot_wechat_miniprogram_feasibility_20260615.md`
- `docs/reports/shadowbot_post_intent_stop_acceptance_20260706.md`
- `docs/reports/shadowbot_unknown_reconcile_attempt_20260709.md`

历史报告不改写当时的原始结论，而是增加后续同步说明并链接 `project_current_status.md`。这样既保留时间点证据，也避免把旧待办误认为当前待办。

## 权威层级

1. 当前项目状态和下一步：`docs/project_current_status.md`。
2. 文档入口与摘要：`docs/index.md`。
3. 实现契约：对应 development spec 和 operations 手册。
4. `docs/reports/`：按报告日期保存历史现场，不单独承担当前进度定义。
