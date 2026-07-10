# 上下架规则输入表单说明

## 当前定位

`/business-inputs` 中的“上下架规则管理”用于维护日常上下架判断规则，并继续保存回 `listing_rules.xlsx`。Excel 仍是业务输入来源，SQLite 仍只保存运行态任务、复核、通知、执行日志和脚本运行记录等运行态事实。

上下架规则用于判断某个商品、等级、平台在任务生成或自动规则评估阶段是否应该建议上架、下架或进入人工复核。保存规则不会直接操作销售平台，真实平台动作仍留给后续 RPA 或平台执行器。

本阶段不新增 SQLite schema，不迁移 Excel 主数据，不接真实平台/RPA/AI Agent，不删除旧 `/tables` 表格编辑入口。

## 字段结构

上下架规则已从旧结构：

- `condition_type`
- `condition_value`
- `action`

升级为新的三维筛选和策略结构：

- `variety_filter`
- `grade_filter`
- `platform_filter`
- `stock_threshold`
- `listing_strategy`

旧字段不再作为 Web 表单或任务生成主路径。

当前 `listing_rules.xlsx` 字段为：

| 字段 | 运营含义 |
| --- | --- |
| `rule_id` | 规则编号 |
| `rule_name` | 规则名称 |
| `variety_filter` | 品种筛选，`*` 表示不限制 |
| `grade_filter` | 等级筛选，`*` 表示不限制 |
| `platform_filter` | 平台筛选，`*` 表示不限制 |
| `stock_threshold` | 库存阈值 |
| `listing_strategy` | 上下架策略 |
| `active` | 是否启用 |
| `priority` | 优先级，数字越小越优先 |
| `remark` | 备注 |

## 三维筛选语义

三个维度共同决定上下架规则是否命中商品和平台：

- 品种：`*` 或 `products.xlsx` 中已有品种。
- 等级：`* / A / B / C / D / E / 0`。
- 平台：`*` 或 `platform_mappings.xlsx` 中平台。

`*` 表示该维度不参与筛选，空字符串不能替代 `*`。页面显示为“不限制”，Excel 中保存为 `*`。

示例：

| 三维筛选 | 页面含义 |
| --- | --- |
| `(*, *, *)` | 全部商品 |
| `(艾莎, *, *)` | 艾莎 / 全部等级 / 全部平台 |
| `(艾莎, B, *)` | 艾莎 / B级 / 全部平台 |
| `(艾莎, B, 蚂蚁)` | 艾莎 / B级 / 蚂蚁 |
| `(*, A, 珍情)` | 全部品种 / A级 / 珍情 |

初始商品库存仍是公共库存，不绑定平台；`platform_filter` 只用于上下架规则命中判断。

## 策略枚举

当前支持：

| `listing_strategy` | 页面含义 | 说明 |
| --- | --- | --- |
| `allow_online` | 允许上架 | 命中范围内可建议上架 |
| `prohibit_online` | 禁止上架 | 命中范围内建议下架或阻止上架 |
| `stock_below_offline` | 库存低于阈值下架 | 当前库存小于等于阈值时建议下架 |
| `stock_above_online` | 库存高于阈值允许上架 | 当前库存大于等于阈值时允许上架 |

`sale_enabled=false` 仍由商品主表最高优先级处理。只要商品禁止销售，系统不得被上下架规则覆盖为上架。

## Web 表单

`/business-inputs` 已新增“上下架规则管理”页签。

表单字段：

- 规则名称
- 品种
- 等级
- 平台
- 规则策略
- 库存阈值
- 是否启用
- 优先级
- 备注

列表展示：

- 规则名称
- 适用范围
- 规则策略
- 库存阈值
- 是否启用
- 优先级
- 备注
- 编辑

保存后提示：

“保存的是业务输入数据，若要影响任务中心，请重新生成运行态任务或运行对应规则评估。”

## 校验规则

保存时必须校验：

- 规则名称不能为空。
- 品种必须为 `*` 或当前允许品种。
- 等级必须为 `* / A / B / C / D / E / 0`。
- 平台必须为 `*` 或当前允许平台。
- 库存阈值必须是数字且大于或等于 `0`。
- 策略必须来自允许枚举。
- 是否启用必须为“是/否”或后端认可布尔值。
- 优先级必须是整数且大于或等于 `0`。

错误提示面向运营人员，不显示 Python traceback。

## ListingRuleEvaluator

当前已接入保守版 `ListingRuleEvaluator`：

- Evaluator 只生成 Proposal，不直接写 SQLite 业务表。
- `dry-run` 只写 `script_runs / script_run_items`。
- `apply` 通过 `RuntimeTaskService / ReviewTaskService / NotificationSender` 链路落成业务结果。
- 每条 proposal 有 `dedupe_key / message / payload / decision_trace`。
- 重复 apply 不会重复生成同一业务复核。

第一版策略保守：当上下架规则建议下架时，生成 `manual_review` 复核 proposal，而不是直接生成可执行平台动作。人工确认后，后续执行链路再决定如何消费。

当前不接真实平台，不接真实 RPA，不直接发送飞书，不绕过运行态服务。

## 旧入口兼容

旧 `/tables` 表格编辑入口仍保留为高级兼容入口，适合批量维护和排障。

旧字段 `condition_type / condition_value / action` 不再作为主路径。若历史测试数据仍使用旧字段，应迁移或重建为新字段结构。
