# PRA Owner Product Boundary

角色：Owner Product Boundary / Accepted Override。日期：2026-09-09。

本文记录负责人对 PRA 实际产品边界的最新明确裁决，用于纠正 Task 13.7 中已经出现的企业化、多账号化和过度证明倾向。若本文与较早的 `business_contract.md`、阶段计划或历史审核意见在部署规模、账号维度、身份认证、人工工作流或施工顺序上冲突，以本文为准；历史实现与历史验收事实不因此改写。后续在对应业务章节自然修改时，再把本文件内容归并回主合同，而不是为了文档整洁一次性重写历史。

## 1. 真实使用场景

PRA 是一个家庭农场内部经营自动化系统，不是面向普通企业、外部客户或多租户市场的通用软件。

当前产品边界：

- 农场规模约 10 人；
- 实际使用 PRA 的管理者不超过 3 人；
- 当前 Sales Controller 是人类管理者；
- 每个平台在一个 PRA 部署实例中只使用一个受控经营账号；
- 不建设 SaaS、多租户、企业 IAM、组织目录、复杂审批层或企业值班升级体系；
- 最终外部使用入口计划收敛为 **微信小程序 + PRA 后端 API**；当前 Operations Web 是过渡管理界面，不应继续演化为第二套长期正式客户端。

“未来企业可能需要”或“理论上可以支持多个账号”不能成为新增表、字段、状态机、Service、认证层、Review 工作流或 blocker scope 的充分理由。

新增抽象必须能回答：

> 如果今天不加它，在当前家庭农场的真实经营中会发生什么具体事故？

没有具体事故或近期已批准需求，不施工。

## 2. 平台与商品身份边界

当前公共经营身份只要求：

```text
platform_name
+ platform_product_identity
→ internal_sku
```

其中 `platform_product_identity` 用于在同一平台内部唯一定位业务商品，例如商品名、等级或稳定平台商品 ID；它不是“整个平台页面认证”。

当前不建设以下公共业务层：

```text
account_id 经营 scope
active session/account binding
page identity proof
platform session attestation
同平台多账号 blocker 隔离
```

现有 Runtime Schema、Repository 或历史数据里已经存在的 `account_id` 字段暂时视为**兼容实现字段**：

- 不立即为了删列新增迁移；
- 不再扩散到新的业务合同、UI、qualification、Queue blocker 或人工操作步骤；
- 当前代码若因 #53 兼容路径暂时需要一个内部值，应在实现边界内吸收，不要求经营人员配置或理解；
- 未来只有出现真实的“同平台多经营账号”需求后，才重新设计该能力。

不同平台的页面元素、流程和 ShadowBot Adapter/Executor 差异很大。错误平台通常无法通过目标商品读取、写前比较和写后回读，因此不再额外建设 page/session identity 认证系统。

真实平台写的安全仍由业务副作用链保证：

```text
读当前值
→ 比较 expected old
→ 执行目标动作
→ 写后重新读取
```

页面元素无法匹配、商品无法定位或当前值不可读取时，应在副作用前失败，而不是通过额外身份认证层补救。

## 3. 外部网络与权限边界

目标上线形态：

```text
微信小程序
    ↓ HTTPS
PRA 后端 API
    ↓
Runtime / Automation / ShadowBot
```

网络安全按微信小程序和后端 API 的真实攻击面实施：

- 服务端认证与授权；
- HTTPS；
- 请求参数、权限和幂等校验；
- 凭据仅保留在服务端受控配置；
- Runtime SQLite、file Queue、ShadowBot Worker 不直接暴露公网；
- 真实平台写仍经过人工授权和确定性执行安全链。

不建设 Zero Trust、mTLS 客户端证书体系、企业 SSO、组织目录、多租户网络隔离或独立设备身份平台，除非出现新的真实业务需求。

“最终使用微信小程序”不等于可以删除后端认证。当前浏览器入口退役前，其适用 Cookie / CSRF / 服务端权限保护继续保留；未来迁移完成后收敛成一套正式服务端认证边界。

## 4. 四类事实必须分开

后续设计和审核不得再要求下面四件事处处同时完美，才允许经营继续：

1. **Human Decision**：管理者现在决定做什么；
2. **Execution Responsibility**：这次决定是否已经进入真实平台执行、谁负责继续推进；
3. **Current Platform Fact**：平台现在实际是什么状态；
4. **Historical Evidence**：过去一次操作到底能证明多少。

允许合法存在：

```text
历史 side effect = UNKNOWN
旧 one-shot responsibility = CLOSED
当前平台事实 = QUALIFIED
新经营决定 = 可以继续
```

不能因为历史证据不完整，就无限阻塞当前经营；也不能为了恢复经营而伪造历史成功或失败。

## 5. 必须保留的 13.7-1 安全内核

以下能力有当前真实事故对应关系，不属于企业化过度开发：

- 最终人工确认后的 durable handoff：页面关闭或服务重启后不能丢失授权，也不能重复平台写；
- 一个长期后台执行 owner，继续推进已持久接受的授权；
- Task / operation / attempt / write lock / receipt 的现有副作用账本；
- 写前读、比较 expected old、真实写、写后回读；
- UNKNOWN 不猜测，不进行第二次不安全写；
- 最多一次既有 RECONCILE；
- 幂等；
- 服务端认证与授权；
- `platform_product_identity → internal_sku`；
- Product Master 与 Inventory 事实分离。

减法不能破坏这些能力。

## 6. 明确停止继续扩张的方向

后续阶段不得默认继续实现：

- `account_id` 作为经营 scope、qualification scope 或 blocker scope；
- active session/account binding 或 page identity proof；
- Review owner 分配、显式 claim/转交、第二次提醒 escalation；
- 为 1-SKU 决定提前建设复杂多项授权批次处理；
- 把 Queue 绝对路径、applet URI 或无关扫描元数据当作用户经营授权的业务身份；
- 新的 blocker framework、状态机或 Service，仅因为理论上以后可能多平台、多账号或企业化；
- 为了删除兼容字段而主动引入新的 schema migration。

## 7. 对 Task 13.7-2C / PR #54 的修订

PR #54 继续完成 Qualified Observation，但按本边界收缩。

### 必须修复

1. **Source acquisition completion / attempt binding**
   - qualification 必须确认目标商品对应的实际采集 attempt 已完成、source snapshot/content 可验证、immutable ProductObservation 已正式接收；
   - 不再机械要求整个 Automation Run 进入 terminal；archive/ACK/父流程收尾属于 delivery/lifecycle health，不能单独否定已经成立的经营事实。

2. **Freshness**
   - `fresh_until = SKU observed_at + max_age`；
   - `scan_completed_at` 用于证明扫描覆盖完成，不给较早观察重新续鲜。

3. **Durable PREPARED cleanup**
   - `prepare_listing_sync_batch()` 之后、publish 之前的 validation/binding 异常必须明确收口该 READ_ONLY batch，不能留下无人负责的 PREPARED 对象。

4. **移除 account/session 公共语义**
   - qualification 不把 `account_id`、`active_session_verified`、session/page proof 作为公共经营条件；
   - #53 现有兼容字段留在内部实现，不扩大。

5. **相关 Mapping 变化才使事实失效**
   - 一个 SKU 的无关 Product 修改、备注或其他商品 mapping 变化，不应仅因为共享 `authority_generation` 变化就让其他 SKU 当前 observation 失效；
   - 优先用当前 SKU 的相关 mapping identity / mapping IDs / platform product identity digest 判断，而不是新增一套 SKU 版本系统。

### 不在 #54 顺带施工

- UNKNOWN closure；
- Queue blocker policy；
- account/session/page identity；
- 新缓存服务；
- 为简化而删除底层 manifest/result/content integrity evidence。

## 8. 13.7-1 后续瘦身顺序

### 13.7-S1 — UNKNOWN / Human Recovery

目标：让人工恢复真正成为自动恢复失败后的兜底，而不是另一套多人审批系统。

收缩方向：

- 任一有权限管理者可直接处理；记录实际处理者，不要求 claim；
- 删除 owner 转交、提醒计数和 escalation；
- 后端自动选最新 qualified current observation，不要求操作者选择 snapshot ID/digest；
- 当前价与 target 的关系由系统判断，操作者只确认“终止旧决定”；
- 保留历史 UNKNOWN；
- 人工 fallback 允许在自动 RECONCILE 证据不完整时工作，只要能证明旧执行已停止、旧请求不会再次消费，并取得 stopped boundary 之后的可信当前事实；
- 不能仅凭超时释放写锁；
- 零写终止旧责任不要求恢复历史 Queue 绝对路径、applet URI 或 profile。

### 13.7-S2 — Decision / Authorization

目标：把“用户授权了什么”与“执行前必须验证什么”分开。

收缩方向：

- 当前事实已经满足 target 时，不再走完整 v4 batch + continuation + Coordinator 的 `resolution_only` 链；在确认没有活动写责任后本地原子结束 one-shot；
- 用户授权身份聚焦于平台商品、SKU、动作、expected old、target、有效期和必要业务边界；
- 扫描时间、无关库存版本、其他 SKU 主数据变化、Queue 目录等不应改变同一个用户经营决定的身份；
- 执行端仍实时读取并比较 expected old，不一致则 zero write；
- 不再通过 `_refresh_correction()` 修改历史 one-shot 的 expected old；事实变化后结束旧决定并建立新的正常决定；
- UPDATE_PRICE 不被无关 Inventory maintenance 或其他 action 的 Review 连坐。

### 13.7-S3 — Migration / Engineering Retirement

后置，不阻塞经营功能：

- Product/Mapping 实际 cutover 稳定后，为 workbook rollback/recutover 和 migration-only `PLATFORM_SIDE_EFFECT` 钩子定义退役点；
- 现有 `account_id` 在生产业务不再依赖后，再决定是否值得物理删列；
- 删除同一环境中完整 pytest 后重复运行的测试子集；
- Current Status 只在阶段、范围、blocker 或验收结论发生实质变化时更新，GitHub Head/CI 作为即时版本真值；
- 测试中的真实时间等待逐步改为 injectable clock/shared fixture；
- 浏览器正式入口只在微信小程序认证真正上线后退役。

## 9. PR #55 重规划条件

PR #55 暂停主体施工，等待 #54 + S1 + S2 的接口稳定后重写。

未来 Queue Operational Continuity 首先只回答四个真实事故：

1. 旧 pending Task 存在时，新人工决定仍能保存；
2. 旧写未过副作用边界则安全终止；已过边界则只阻止冲突的同 SKU 写，新决定仍保留；
3. UNKNOWN 不阻止安全 READ_ONLY；
4. 已过期、已终止、历史对象退出 Current Queue，不再拥有当前 blocker 权力。

如果现有 Task / operation / attempt / write lock / Worker / Queue 已能表达这些条件，就不新建 blocker Service、第五套状态机或企业化全局治理框架。

## 10. R0 完成条件

Task 13.7-R0 本身是 docs-only 边界纠偏：

- 本文进入 main；
- Developer Governance / AGENTS 不再要求 account/session/page identity 或“每次 push 必须同步状态页”的旧流程；
- #54 / #55 施工评论引用新边界；
- 不改生产代码；
- 不删除现有 schema 字段；
- 不执行 Runtime cutover、部署或真实平台动作。
