# Business Decision Specification

> SQLite 运行态持久化的详细开发计划已拆分到 `docs/sqlite_runtime_persistence_plan.md`。后续任务系统、人工复核、通知记录和状态历史的实现，应以该文档为准。

## 1. 文档目的

本文档用于定义鲜切花预测性销售场景下的平台无关业务决策规则，作为下一阶段工作的统一依据。

本文档的目标不是指导当前阶段立即改代码，而是：

- 更新项目业务定位
- 固化关键业务概念与决策边界
- 明确下一阶段需要新增或改造的数据模型
- 明确下一阶段需要新增或改造的服务模块
- 明确后续测试清单
- 明确当前阶段暂不实现事项

本文档基于以下事实整理：

- 当前项目代码已经完成 Excel 主数据、价格规则、上下架规则、任务预览、任务导出、模拟执行回写等 MVP 能力
- 当前系统仍然是平台无关业务核心，不接真实平台、不接真实 RPA、不接真实 AI
- 当前系统已预留 `recommended_price`、任务系统边界、执行日志与 AI 接口承接能力

---

## 2. 项目定位更新

### 2.1 新定位

本项目不是传统“已有库存 -> 上架 -> 销售 -> 扣库存”的常规电商系统。

鲜切花业务的核心特点是：

1. 质量最好的花不适合先采收、加工、入库后再等待销售
2. 更合理的流程是先预测次日产量与价格，再提前销售
3. 次日根据实际采收的新鲜花材去匹配订单
4. 交易期间根据市场情况动态调整价格和上下架状态

因此，项目定位应从：

`平台无关的规则任务核心`

升级为：

`鲜切花预测性销售决策系统 + 多平台执行任务系统`

### 2.2 当前阶段边界

当前阶段系统仍只负责平台无关业务核心，重点包括：

1. 商品主数据
2. 预测结果接入预留
3. 产量、库存、包装能力约束
4. 上下架决策
5. `recommended_price` 定价锚点
6. 交易期价格调整规则
7. 标准任务生成
8. 后续 RPA、人工、平台 API 执行边界预留

当前阶段仍然不做：

- 真实平台接入
- 真实 RPA 接入
- 真实 AI 模型接入

---

## 3. 当前代码实现状态

### 3.1 已完成能力

当前仓库已经具备以下 MVP 能力：

1. Excel 主数据导入与字段级校验
2. 商品、价格规则、上下架规则的基础模型定义
3. 任务预览与任务导出
4. 模拟执行与执行日志回写
5. Web 管理页、表格编辑页、执行回写页
6. `recommended_price` 字段接入预留
7. 时间型上下架条件基础支持
8. AI 定价建议输入输出边界预留

### 3.2 当前实现的真实角色

当前系统更准确的角色是：

`业务规则验证器 + 任务生成器 + 后续执行层边界定义器`

它还不是：

- 完整的运营后台
- 完整的预测系统
- 完整的执行系统
- 完整的价格引擎

### 3.3 当前代码与新业务规则的关系

当前代码可以承接下一阶段规划，但还没有实现以下关键业务对象和决策层：

- `trade_date` 交易窗口体系
- 预测产量对象
- 预测价格对象
- 包装产能计划
- 冷库容量压力
- 风险调整后的可承诺量
- 清库存阶段价格策略
- `break_even_price` / `absolute_min_price` 双底线体系
- 运营提醒类任务与人工复核任务

因此，下一阶段应优先补文档、补模型设计、补服务边界，再进入代码改造。

---

## 4. 核心业务流程

### 4.1 预测性销售主流程

鲜切花预测性销售建议遵循以下业务主链路：

1. 前一天预测第二天产量
2. 前一天预测第二天价格
3. 在交易窗口内提前上架销售
4. 第二天根据实际采收花材匹配订单
5. 交易期间根据市场与库存变化调整价格、上下架状态
6. 收盘后做剩余量处理、日志回写与人工复核

### 4.2 当前系统在主流程中的职责

当前系统不负责第 1、2 步的预测模型实现，只负责接收产量预测和价格预测结果，并基于这些结果完成第 3 至第 6 步中的业务决策和任务生成。

---

## 5. 交易日与交易窗口规则

### 5.1 trade_date 定义

`trade_date` 指商品实际交易归属日，不等同于自然日 `00:00 ~ 24:00`。

### 5.2 交易窗口定义

对于某个 `trade_date`，定义：

- `trade_open_at = trade_date 前一天 23:00`
- `clearance_start_at = trade_date 当天 15:30`
- `trade_close_at = trade_date 当天 17:00`

示例：

若 `trade_date = 2026-05-04`

则：

- `trade_open_at = 2026-05-03 23:00`
- `clearance_start_at = 2026-05-04 15:30`
- `trade_close_at = 2026-05-04 17:00`

### 5.3 窗口期业务规则

1. 不要求交易开始前必须完成上架
2. 交易窗口内可以随时上架、下架、改价
3. `15:30` 后进入清库存阶段，允许更积极的调价策略
4. `17:00` 后平台关闭交易，不再生成新的上架任务和改价任务
5. `17:00` 后只允许生成收尾类任务，例如：
   - 下架
   - 状态同步
   - 执行日志回写
   - 人工复核

### 5.4 下一阶段待改造点

当前代码尚未引入正式的 `trade_date` / `trade_open_at` / `clearance_start_at` / `trade_close_at` 计算模型，后续需新增独立交易窗口服务。

---

## 6. 预测产量规则

### 6.1 预测粒度

预测产量粒度应为：

`品种 + 等级`

而不是 SKU。

建议定义：

`forecast_group_key = variety + grade`

示例：

- 艾莎 + A级
- 卡罗拉 + B级
- 荔枝泡泡 + A级

### 6.2 Product 与 forecast_group_key 的关系

当前系统中的 `Product` 更接近 SKU 粒度，可能还包含：

- 长度
- 规格
- 单位

因此，后续需要从 `Product` 映射到 `forecast_group_key`。

### 6.3 预测产量数据结构建议

建议新增 `harvest_forecasts`：

- `forecast_id`
- `forecast_date`
- `target_trade_date`
- `forecast_group_key`
- `variety`
- `grade`
- `predicted_harvest_qty`
- `lower_bound_qty`
- `upper_bound_qty`
- `confidence`
- `source`
- `generated_at`
- `note`

### 6.4 当前阶段说明

预测任务本身不在当前项目实现范围内，但系统必须预留消费预测结果的能力。

---

## 7. 田间弹性缓冲与等级兼容规则

### 7.1 田间弹性缓冲

鲜花是否可采摘通常不是一个绝对点，而是一个区间。

建议字段：

- `field_buffer_qty`

默认规则：

- `field_buffer_qty` 按品种 `variety` 计算
- 默认每个品种约 `50` 扎

注意：

- 预测产量按 `品种 + 等级` 计算
- 田间缓冲按 `品种` 计算

### 7.2 等级兼容规则

高等级花可以替代低等级售卖，低等级不能替代高等级。

建议定义：

- `grade_rank(A) = 3`
- `grade_rank(B) = 2`
- `grade_rank(C) = 1`

兼容条件：

`source_grade_rank >= target_grade_rank`

示例：

- A 可以满足 A/B/C
- B 可以满足 B/C
- C 只能满足 C

### 7.3 当前阶段说明

当前阶段只需固化概念与规则，不要求立刻实现复杂跨等级调拨算法。

---

## 8. 实际库存、可销售量与短缺风险

### 8.1 关键概念

系统应区分以下数量：

- `actual_stock_qty`
- `predicted_harvest_qty`
- `reserved_qty`
- `safety_buffer_qty`
- `field_buffer_qty`
- `inventory_based_available_qty`
- `risk_adjusted_available_qty`

### 8.2 建议公式

基础可销售量：

`inventory_based_available_qty = predicted_harvest_qty + actual_stock_qty - reserved_qty - safety_buffer_qty`

风险调整后可承诺量：

`risk_adjusted_available_qty = predicted_harvest_qty + actual_stock_qty + field_buffer_qty - reserved_qty - safety_buffer_qty`

### 8.3 默认策略

当前默认策略应偏保守：

不要主动把 `field_buffer_qty` 全部当作普通上架量使用。

`field_buffer_qty` 的主要用途应是：

- 处理预测误差
- 处理超售风险
- 作为可控弹性而不是常规承诺量

### 8.4 短缺风险定义

建议规则：

若：

`sold_qty <= predicted_harvest_qty + actual_stock_qty`

则：

`shortage_risk = low`

若：

`sold_qty <= predicted_harvest_qty + actual_stock_qty + field_buffer_qty`

则：

`shortage_risk = manageable`

否则：

`shortage_risk = high`

### 8.5 超售处理顺序

1. 先到田里寻找采摘遗漏、接近可采摘线的花
2. 若仍不足，再委托平台购买同类产品补充
3. 若超出田间缓冲能力，则标记 `high shortage risk` 并进入人工复核

### 8.6 风险说明

- `shortage_risk = manageable` 表示通常还能通过田间弹性缓冲解决
- `shortage_risk = high` 表示可能需要外部采购或人工干预

---

## 9. 冷库容量规则

### 9.1 核心概念

基地存在共享冷库，可暂存剩余鲜花。

建议字段：

- `cold_storage_total_capacity_qty = 500`
- `cold_storage_current_qty`
- `cold_storage_available_capacity`

### 9.2 公式

`cold_storage_available_capacity = cold_storage_total_capacity_qty - cold_storage_current_qty`

### 9.3 业务含义

若交易后剩余量可进入冷库，则可延后销售或后续处理。

若预计全场剩余量超过冷库可用容量，则系统应：

- 提高 `clearance_pressure`
- 在清库存阶段更积极降价或处理

### 9.4 规则区分

文档中应明确区分：

- 单品/品种剩余压力
- 全场冷库压力

---

## 10. 包装产能与临时工规则

### 10.1 核心业务约束

包装产能是下一阶段必须加入的核心业务约束。

建议字段：

- `normal_packing_capacity_qty = 250`
- `temp_worker_capacity_qty = 100`

### 10.2 基本规则

默认情况下，基地每天正常包装能力约 `250` 扎。

每名临时工每天可额外增加约 `100` 扎包装能力。

当前阶段不同品种包装耗时统一按“扎数”处理，不区分品种。

### 10.3 临时工需求计算

建议：

`extra_packing_qty = max(0, predicted_total_harvest_qty - normal_packing_capacity_qty)`

`required_temp_workers = ceil(extra_packing_qty / temp_worker_capacity_qty)`

示例：

- 预测总产量 `420`
- 正常包装能力 `250`
- 超出产能 `170`
- 每名临时工能力 `100`
- 则 `required_temp_workers = 2`

### 10.4 决策时点

临时工应在 `trade_date` 前一天下午至晚上完成决策。

因此，系统应在 `trade_date` 前一天生成强提醒任务：

- `capacity_warning`
- `labor_required`

### 10.5 默认策略

默认策略应为：

`少上架，强提醒`

即：

若 `predicted_total_harvest_qty > normal_packing_capacity_qty` 且未确认临时工，则系统不应自动承诺超过正常包装能力的销售量。

### 10.6 建议字段

- `confirmed_temp_worker_count`
- `confirmed_temp_labor_capacity_qty`
- `confirmed_packing_capacity_qty`

公式：

`confirmed_temp_labor_capacity_qty = confirmed_temp_worker_count * temp_worker_capacity_qty`

`confirmed_packing_capacity_qty = normal_packing_capacity_qty + confirmed_temp_labor_capacity_qty`

若未确认临时工：

- `confirmed_temp_worker_count = 0`
- `confirmed_packing_capacity_qty = 250`

### 10.7 最终可承诺量

最终可承诺销售量应受包装能力约束：
confirmed_packing_capacity_qty 是 trade_date 维度的全场共享包装能力，不是每个商品或预测组独占额度。

在计算单个 forecast_group 或 SKU 的 committable_qty 时，必须先通过 allocation_rule 将全场包装能力分配到各 forecast_group / SKU。

当前 MVP 阶段可采用简单分配策略：
1. 按 predicted_harvest_qty 占比分配包装能力；
2. 或按人工指定 listing_quota 分配；
3. 在未实现分配规则前，不应把 250 扎作为每个商品的独立上限。

`committable_qty = min(inventory_based_available_qty, confirmed_packing_capacity_qty)`


### 10.8 结论

包装能力是上架量决策的重要约束。

未确认临时工时，系统不应自动承诺超过正常包装能力的销售量。

预测产量超过包装能力时，系统应生成强提醒，而不是自动扩大上架量。

---

## 11. 上架与下架决策规则

### 11.1 影响因素

上架决策不应只由库存决定，而应同时考虑：

1. 当前是否处于交易窗口内
2. `sale_enabled`
3. 预测产量
4. 实际库存
5. `reserved_qty`
6. `safety_buffer_qty`
7. `field_buffer_qty`
8. `confirmed_packing_capacity_qty`
9. 质量风险
10. 人工禁售或锁定状态

### 11.2 最高优先级禁售规则

`sale_enabled = false` 是最高优先级禁售规则。

规则为：

只要 `sale_enabled = false`，无论库存、预测、时间规则、推荐价格如何，都不得生成上架任务。

必要时应生成下架任务。

### 11.3 上架判断建议

允许上架的建议条件：

- 当前时间在交易窗口内
- `sale_enabled = true`
- `committable_qty > 最小上架阈值`
- 没有人工强制下架
- 没有高质量风险

### 11.4 下架条件建议

以下任一情况可触发下架：

1. `sale_enabled = false`
2. `committable_qty <= 0`
3. 已到交易关闭时点
4. 质量风险过高
5. 人工强制下架
6. 平台或执行异常导致需要暂停

### 11.5 收盘规则

`17:00` 后不得再生成新的上架任务，只允许生成：

- 下架
- 状态同步
- 收尾类任务

### 11.6 当前代码待改造点

当前代码的上下架逻辑仍以库存与 `sale_enabled` 为主，尚未真正接入：

- `trade_date` 窗口约束
- 包装能力约束
- 质量风险
- 冷库压力
- 预测产量与 `committable_qty`

这些应在下一阶段通过独立决策服务重构。

---

## 12. recommended_price 定义

### 12.1 正式定义

`recommended_price` 是针对某个 `trade_date`、某个 `forecast_group`（品种 + 等级）提前生成的次日预测基准价。

单位：

`元 / 扎`

### 12.2 它不是什么

`recommended_price` 不是：

- 最终成交价
- 最低价
- 人工锁价

### 12.3 它的用途

1. 第二天初始上架价的核心锚点
2. 交易期动态调价的中心参考
3. 为不同平台派生平台目标价
4. 为 AI 或外部预测系统接入预留输入边界

### 12.4 价格预测数据结构建议

建议新增 `price_forecasts`：

- `forecast_id`
- `forecast_date`
- `target_trade_date`
- `forecast_group_key`
- `variety`
- `grade`
- `recommended_price`
- `lower_bound_price`
- `upper_bound_price`
- `confidence`
- `source`
- `generated_at`
- `note`

`source` 可包括：

- `manual`
- `rule_based`
- `ai_model`
- `market_data`
- `hybrid`

### 12.5 当前阶段说明

预测任务本身不在本项目中实现，但本项目必须能够消费预测结果。

### 12.6 当前代码待改造点

当前代码中的 `recommended_price` 仍只是商品字段级输入预留，尚未升级为：

- 面向 `trade_date`
- 面向 `forecast_group_key`
- 具备置信区间
- 具备来源字段

下一阶段应将其从 Product 字段逐步提升为独立预测对象。

---

## 13. 平台价格规则

### 13.1 基本原则

不同平台允许不同价格。

因此：

`recommended_price` 是基础预测价，不是所有平台的最终售价。

### 13.2 平台价格派生

建议平台目标价通过平台价格规则派生：

`platform_target_price = recommended_price * platform_price_factor + platform_fixed_adjustment`

### 13.3 建议数据结构

建议预留 `platform_price_rules`：

- `platform_name`
- `forecast_group_key` 或 `product_scope`
- `price_factor`
- `fixed_adjustment`
- `min_price`
- `max_price`
- `rounding_rule`
- `active`
- `remark`

### 13.4 当前阶段说明

当前阶段不接具体平台清单，也不应写死平台名称。

---

## 14. 价格阶段与调价规则

### 14.1 阶段划分

交易期分为三个阶段：

1. 正常交易阶段  
   `T-1 23:00 ~ T 15:30`

2. 清库存阶段  
   `T 15:30 ~ T 17:00`

3. 交易关闭后  
   `T 17:00` 之后

### 14.2 改价频次

每天改价次数不限制。

### 14.3 涨价规则

涨价幅度当前不设上限。

### 14.4 降价规则

正常交易阶段：

- 单次自动降价不得超过 `10%`

清库存阶段：

- 单次自动降价不得超过 `20%`

建议字段：

- `max_discount_rate_normal = 0.10`
- `max_discount_rate_clearance = 0.20`

---

## 15. 最低价体系

### 15.1 两类最低价

应明确存在两个最低价概念：

- `break_even_price`
- `absolute_min_price`

通常关系：

`absolute_min_price <= break_even_price`

### 15.2 自动化规则

正常交易阶段：

- 自动定价不得低于 `break_even_price`

清库存阶段：

- 如果建议价格 `>= break_even_price`，可以自动生成改价任务
- 如果 `absolute_min_price <= 建议价格 < break_even_price`，不得自动执行，必须生成人工确认任务
- 如果建议价格 `< absolute_min_price`，禁止直接生成该价格，应标记规则错误或自动抬升到 `absolute_min_price`

### 15.3 底线解释

- `absolute_min_price` 是硬底线，任何自动策略都不得低于它
- `break_even_price` 是自动化底线，低于它必须人工确认

### 15.4 建议任务类型

- `manual_price_review`
- `below_break_even_review`

### 15.5 当前代码待改造点

当前代码的价格逻辑仍主要围绕：

- 成本
- 固定加价
- 百分比加价
- 最低价
- 取整

尚未引入：

- `trade_date` 分阶段价格决策
- `break_even_price`
- `absolute_min_price`
- 人工确认型价格任务

---

## 16. 任务类型建议

### 16.1 当前可保留任务类型

当前已有：

- `update_price`
- `set_online`
- `set_offline`
- `sync_status`

### 16.2 建议新增或预留任务类型

- `capacity_warning`
- `labor_required`
- `manual_price_review`
- `below_break_even_review`
- `shortage_warning`
- `cold_storage_warning`
- `clearance_warning`
- `manual_review`

### 16.3 说明

这些任务不一定都由 RPA 执行。

其中相当一部分属于：

- 运营提醒
- 人工复核
- 决策确认

而不是平台动作执行任务。

---

## 17. 任务系统边界

任务系统仍然是业务核心与执行层之间的边界。

### 17.1 业务层负责

业务层负责决定：

- 要不要上架
- 上架多少
- 要不要下架
- 初始价格是多少
- 是否需要调价
- 是否需要人工确认
- 是否需要雇佣临时工
- 是否存在短缺风险
- 是否存在冷库压力

### 17.2 执行层负责

执行层负责：

- 读取任务
- 执行平台操作或人工处理
- 回写状态
- 提供执行结果和错误信息

### 17.3 当前阶段说明

当前不要接真实 RPA，不要接真实平台，不要接真实 AI。

---

## 18. 后续待改造点（基于当前代码）

以下内容与当前业务规则已经存在明显差距，但当前阶段只记录为待改造点，不直接改代码：

1. `trade_date` 尚未成为正式一等对象
2. 当前 Product 仍偏 SKU 视角，预测视角对象尚未独立
3. `recommended_price` 当前仍挂在商品侧，不是独立的价格预测对象,迁移期间允许 Product.recommended_price 作为兼容字段存在，但新的业务逻辑应优先读取 PriceForecast.recommended_price；若没有 PriceForecast，则可回退到 Product.recommended_price。
4. 当前上下架决策尚未引入包装能力、冷库压力、短缺风险和人工锁定
5. 当前任务系统以平台动作任务为主，运营提醒类和人工复核类任务仍需扩展
6. 当前价格决策尚未支持交易阶段、单次最大降价幅度、双最低价体系
7. 当前库存逻辑尚未区分 `actual_stock_qty`、`reserved_qty`、`safety_buffer_qty`、`field_buffer_qty`
8. 当前未正式引入 `forecast_group_key = variety + grade`
9. 当前未正式支持等级兼容调拨建模
10. 当前未建立全场共享冷库容量状态对象

---

## 19. 后续模型改造建议

### 19.1 HarvestForecast

职责：

- 承接次日产量预测结果
- 作为预测销售的数量基础输入

建议字段：

- `forecast_id`
- `forecast_date`
- `target_trade_date`
- `forecast_group_key`
- `variety`
- `grade`
- `predicted_harvest_qty`
- `lower_bound_qty`
- `upper_bound_qty`
- `confidence`
- `source`
- `generated_at`
- `note`

### 19.2 PriceForecast

职责：

- 承接次日价格预测结果
- 提供 `recommended_price` 基准锚点

建议字段：

- `forecast_id`
- `forecast_date`
- `target_trade_date`
- `forecast_group_key`
- `variety`
- `grade`
- `recommended_price`
- `lower_bound_price`
- `upper_bound_price`
- `confidence`
- `source`
- `generated_at`
- `note`

### 19.3 TradeWindow

职责：

- 根据 `trade_date` 计算交易窗口
- 判断当前处于正常阶段、清库存阶段还是收盘后

建议字段：

- `trade_date`
- `trade_open_at`
- `clearance_start_at`
- `trade_close_at`
- `phase`

### 19.4 PackingCapacityPlan

职责：

- 表达某个 `trade_date` 的包装能力计划
- 计算临时工需求与已确认产能

建议字段：

- `trade_date`
- `normal_packing_capacity_qty`
- `temp_worker_capacity_qty`
- `predicted_total_harvest_qty`
- `required_temp_workers`
- `confirmed_temp_worker_count`
- `confirmed_temp_labor_capacity_qty`
- `confirmed_packing_capacity_qty`
- `note`

### 19.5 ColdStorageStatus

职责：

- 记录全场共享冷库容量状态
- 为清库存压力判断提供输入

建议字段：

- `status_date`
- `cold_storage_total_capacity_qty`
- `cold_storage_current_qty`
- `cold_storage_available_capacity`
- `projected_remaining_qty`
- `clearance_pressure`
- `note`

### 19.6 InventoryPlan

职责：

- 统一表达某个预测组或 SKU 的可销售量与风险状态

建议字段：

- `trade_date`
- `forecast_group_key`
- `product_scope`
- `actual_stock_qty`
- `predicted_harvest_qty`
- `reserved_qty`
- `safety_buffer_qty`
- `field_buffer_qty`
- `inventory_based_available_qty`
- `risk_adjusted_available_qty`
- `committable_qty`
- `shortage_risk`
- `note`

### 19.7 ListingDecision

职责：

- 输出某个商品或范围在当前窗口内是否允许上架/下架

建议字段：

- `trade_date`
- `decision_time`
- `product_scope`
- `forecast_group_key`
- `sale_enabled`
- `manual_lock_state`
- `quality_risk`
- `committable_qty`
- `listing_allowed`
- `decision_reason`
- `suggested_action`

### 19.8 PricingDecision

职责：

- 输出初始价、动态调价建议、最低价约束与人工确认要求

建议字段：

- `trade_date`
- `decision_time`
- `forecast_group_key`
- `recommended_price`
- `platform_target_price`
- `price_phase`
- `break_even_price`
- `absolute_min_price`
- `suggested_price`
- `discount_rate`
- `requires_manual_review`
- `decision_reason`

### 19.9 PlatformPriceRule

职责：

- 从基础预测价派生平台目标价

建议字段：

- `platform_name`
- `forecast_group_key`
- `product_scope`
- `price_factor`
- `fixed_adjustment`
- `min_price`
- `max_price`
- `rounding_rule`
- `active`
- `remark`

### 19.10 ManualReviewTask 或 ReviewRequirement

职责：

- 承接人工确认、人工补货、价格复核、短缺复核等非自动执行任务

建议字段：

- `review_id`
- `trade_date`
- `review_type`
- `related_task_id`
- `related_scope`
- `priority`
- `reason`
- `status`
- `required_by`
- `created_at`
- `resolved_at`
- `note`

人工复核任务必须有 required_by。
临时工确认类任务 required_by 应早于 trade_date 前一天晚上，建议默认 trade_date 前一天 20:00。
低于 break_even_price 的价格复核任务 required_by 应早于 trade_close_at。
---

## 20. 服务模块改造建议

### 20.1 `trade_window.py`

职责：

- 计算交易窗口
- 判断当前时间属于哪个交易阶段

### 20.2 `harvest_forecast.py`

职责：

- 读取和校验产量预测
- 标准化 `forecast_group_key`

### 20.3 `price_forecast.py`

职责：

- 读取和校验 `recommended_price`
- 标准化价格预测对象

### 20.4 `capacity_planning.py`

职责：

- 计算包装能力
- 计算临时工需求
- 输出容量告警和可确认产能

### 20.5 `inventory_planning.py`

职责：

- 计算基础可销售量
- 计算风险调整后可承诺量
- 判断短缺风险和冷库压力

### 20.6 `listing_decision.py`

职责：

- 根据交易窗口、库存、预测、容量、质量风险输出上下架决策

### 20.7 `pricing_decision.py`

职责：

- 根据 `recommended_price`、交易阶段、最低价规则输出初始价和动态调价建议
- 判断是否需要人工确认

### 20.8 `task_generation.py`

升级方向：

- 支持运营提醒类任务
- 支持人工复核类任务
- 支持容量预警、短缺预警、冷库预警
- 支持平台执行任务与人工任务并存

---

## 21. 测试用例建议

建议至少新增以下测试：

1. `trade_date` 交易窗口计算测试
2. `23:00 ~ 17:00` 交易窗口判断测试
3. `15:30` 后进入清库存阶段测试
4. `17:00` 后不生成上架和改价任务测试
5. `sale_enabled = false` 强制禁售测试
6. 预测产量按 `品种 + 等级` 读取测试
7. `field_buffer_qty` 按品种计算测试
8. 高等级向低等级兼容规则测试
9. 超售 `shortage_risk = manageable` 测试
10. 超售 `shortage_risk = high` 测试
11. 冷库容量 `500` 扎全场共享测试
12. 预测产量超过 `250` 扎生成 `capacity_warning` 测试
13. `required_temp_workers` 计算测试
14. 未确认临时工时 `committable_qty` 不超过 `250` 测试
15. 确认临时工后 `committable_qty` 提高测试
16. `recommended_price` 单位为元/扎测试
17. 不同平台价格派生预留测试
18. 正常阶段单次降价不超过 `10%` 测试
19. 清库存阶段单次降价不超过 `20%` 测试
20. 正常阶段不得低于 `break_even_price` 测试
21. 清库存阶段低于 `break_even_price` 生成 `manual_price_review` 测试
22. 任何阶段不得低于 `absolute_min_price` 测试

---

## 22. 当前阶段暂不实现事项

当前阶段明确不做以下事项：

1. 不接具体销售平台
2. 不接真实 RPA
3. 不接真实 AI 模型
4. 不实现复杂多用户权限体系
5. 不在业务规则尚未稳定时废弃 Excel
6. 不把 `field_buffer_qty` 当成普通库存全量上架
7. 不在未确认临时工时自动承诺超过正常包装能力的销售量
8. 不让低于 `break_even_price` 的价格自动执行
9. 不让 `sale_enabled = false` 被任何规则覆盖

---

## 23. 下一阶段工作建议

下一阶段的第一目标不是马上扩代码，而是先让业务决策规则固化。

建议推进顺序：

1. 冻结本文档中的业务概念和决策口径
2. 明确 `recommended_price`、`break_even_price`、`absolute_min_price` 的来源和维护责任
3. 明确 `forecast_group_key` 与 SKU 的映射规则
4. 明确包装能力、临时工确认、冷库压力的运营输入方式
5. 基于本文档拆出模型改造任务、服务改造任务、测试任务
6. 在规则稳定后再进入数据库化与代码重构

这一步完成之后，系统才能从当前的 MVP 规则引擎，稳妥升级为“鲜切花预测性销售决策系统”。
