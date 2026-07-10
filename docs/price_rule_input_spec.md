# 价格规则输入与三维适用范围说明

## 当前定位

`/business-inputs` 中的“价格规则管理”用于维护日常定价规则，并继续保存回 `price_rules.xlsx`。Excel 仍是业务输入来源，SQLite 仍只保存运行态任务、复核、通知、执行日志等运行态事实。

本阶段不新增 SQLite schema，不迁移 Excel 主数据，不接真实平台/RPA/AI Agent，不新增 `absolute_min_price / break_even_price / target_price / manual_review_required` 等字段。

## 字段结构

价格规则适用范围已从旧的单维结构：

- `scope_type`
- `scope_value`

一次性升级为新的三维筛选结构：

- `variety_filter`
- `grade_filter`
- `platform_filter`

`scope_type / scope_value` 已废弃，不再作为价格规则主路径读取或写入。旧测试数据已迁移；备份文件 `price_rules_backup_before_scope_refactor.xlsx` 仅用于人工回溯，任务生成流程、Web 表单和测试主路径不得读取该备份文件作为正式数据。

当前 `price_rules.xlsx` 字段为：

| 字段 | 运营含义 |
| --- | --- |
| `rule_id` | 规则编号 |
| `rule_name` | 规则名称 |
| `variety_filter` | 品种筛选，`*` 表示不限制 |
| `grade_filter` | 等级筛选，`*` 表示不限制 |
| `platform_filter` | 平台筛选，`*` 表示不限制 |
| `pricing_method` | 定价方式 |
| `markup_value` | 改价值，正数表示涨价，负数表示降价 |
| `min_price` | 最低价 |
| `rounding_rule` | 取整规则 |
| `rounding_step` | 取整步长 |
| `active` | 是否启用 |
| `priority` | 优先级，数字越小越优先 |
| `remark` | 备注 |

## 三维筛选语义

三个维度共同决定价格规则是否命中候选商品和平台。`*` 表示该维度不参与筛选，空字符串不能替代 `*`。

示例：

| 三维筛选 | 页面含义 |
| --- | --- |
| `(*, *, *)` | 全部商品 |
| `(艾莎, *, *)` | 艾莎 / 全部等级 / 全部平台 |
| `(艾莎, B, *)` | 艾莎 / B级 / 全部平台 |
| `(艾莎, B, 蚂蚁)` | 艾莎 / B级 / 蚂蚁 |
| `(*, A, 珍情)` | 全部品种 / A级 / 珍情 |
| `(*, *, 花伍)` | 全部品种 / 全部等级 / 花伍 |

价格规则只作用于任务生成流程已经判定为可销售或待处理的候选商品。`(*, *, *)` 不会强行让禁售商品、无库存商品或非候选商品进入任务。

## 匹配规则

价格规则匹配逻辑：

```python
def price_rule_matches(rule, product, platform_name):
    return (
        matches_filter(rule.variety_filter, product.product_name)
        and matches_filter(rule.grade_filter, product.grade)
        and matches_filter(rule.platform_filter, platform_name)
    )

def matches_filter(filter_value, actual_value):
    return filter_value == "*" or normalize(filter_value) == normalize(actual_value)
```

标准化要求：

- 品种去除前后空格。
- 等级转为大写字符串，等级 `0` 保持字符串 `"0"`。
- 平台名称去除前后空格。
- `*` 统一表示通配符。
- 不允许用空字符串表示全部。
- 不把中文展示值写坏任务生成需要的标准值。

## 单条规则胜出

价格规则已从旧的“多条命中规则叠加应用”改为“单条规则胜出”。

多条价格规则同时命中同一个候选商品/平台组合时：

1. 先按 `priority` 升序选择，数字越小越优先。
2. `priority` 相同，则选择具体度更高的规则。
3. 如果 `priority` 和具体度都相同，且多条规则冲突，不允许静默随机选择。

具体度定义为 `variety_filter / grade_filter / platform_filter` 中非 `*` 条件数量：

- `(*, *, *)`：具体度 0
- `(艾莎, *, *)`：具体度 1
- `(艾莎, B, *)`：具体度 2
- `(艾莎, B, 蚂蚁)`：具体度 3

冲突处理要求：

- 不应生成不确定价格的可执行任务。
- 当前实现会返回明确的价格规则冲突错误。
- 若后续需要按候选项局部阻断，应在 `decision_trace_json` 中记录 `price_rule_conflict`，并避免生成该候选项的可执行改价任务。

## Web 表单

价格规则管理页面不再显示“适用范围 / 范围对象”旧控件，改为：

- 品种：`*` + 当前 `products.xlsx` 中已有品种。
- 等级：`* / A / B / C / D / E / 0`。
- 平台：`*` + `platform_mappings.xlsx` 中维护的平台。

页面显示中，`*` 展示为“不限制”；Excel 中仍保存为 `*`。

平台选项优先从 `platform_mappings.xlsx` 读取。如果文件缺失、为空或读取失败，页面不得返回 500，可使用兜底平台列表：

`寻梦 / 花伍 / 珍情 / 花易宝 / 蚂蚁 / 花宝宝`

页面应显示可读提示，说明已使用默认平台列表。

## 校验规则

保存价格规则时至少校验：

- 品种必须为 `*` 或当前允许品种。
- 等级必须为 `* / A / B / C / D / E / 0`。
- 平台必须为 `*` 或当前允许平台。
- 不允许空字符串代表全部，必须使用 `*`。
- 规则名称不能为空。
- 价格类型必须来自现有 `pricing_method` 枚举。
- 改价值必须是数字且不能为 `0`；正数表示涨价，负数表示降价。
- 最低价可以为空；不为空时必须是数字且 `>= 0`。
- 取整规则必须来自现有 `rounding_rule` 枚举。
- `rounding_rule=step` 时，`rounding_step` 必须大于 `0`。
- 是否启用必须为“是 / 否”或后端认可的布尔值。
- 优先级必须是整数且 `>= 0`。

错误提示必须面向运营人员，不显示 Python traceback。

## 平台与库存边界

- 商品库存录入阶段仍是公共库存，不绑定平台。
- 价格规则可以按平台匹配，是因为任务生成阶段会面向具体平台生成候选任务。
- 平台不进入 SKU。
- 平台不参与商品库存补充匹配。
- `platform_filter` 只用于价格规则命中判断。

## SKU 级价格规则

本次三维结构暂不支持 SKU 级价格规则。不要把 SKU 强行塞入 `variety_filter` 或其他字段。未来如果需要 SKU 级价格规则，应单独规划字段、校验和任务生成逻辑。

## 当前阶段不做

- 不新增 SQLite schema。
- 不迁移 Excel 主数据到 SQLite。
- 不接真实平台。
- 不接真实 RPA。
- 不接 AI Agent 自动决策。
- 不改 ReviewTaskService / RuntimeTaskService / NotificationSender。
- 不删除旧 `/tables`。
- 不引入 React / Vue。
- 不重写整个定价引擎。
- 不新增 `absolute_min_price / break_even_price / target_price`。
