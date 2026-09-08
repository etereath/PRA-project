"""把 7C Read Model 渲染为运营者可读的 HTML 片段。"""

from __future__ import annotations

from app.operations_web.read_models import (
    DatabaseReadModel,
    DetailReadModel,
    ManagementReadModel,
    MobileReviewReadModel,
    NotificationDrawerReadModel,
    StateReadModel,
    SystemReadModel,
    TableReadModel,
    TaskQueueReadModel,
    TodayReadModel,
)
from app.operations_web.rendering import html


MANUAL_ACTION_LABELS = {
    "SET_PRICE": "调整价格到",
    "CHANGE_PRICE": "加/降价",
    "SET_OFFLINE": "下架",
    "SET_ONLINE": "上架",
    "update_price": "调整价格",
    "set_offline": "下架",
    "set_online": "上架",
}


def _manual_action_label(value: object) -> str:
    raw = getattr(value, "value", value)
    return MANUAL_ACTION_LABELS.get(str(raw), "平台操作")


def _margin_gap_label(gap: int, enabled: bool) -> str:
    if not enabled:
        return "预警未启用"
    if gap > 0:
        return f"高于安全余量 {gap} 扎"
    if gap == 0:
        return "已到安全余量"
    return f"低于安全余量 {-gap} 扎"


def _render_management_tabs(active: str) -> str:
    tabs = (
        ("tasks", "/management", "创建任务"),
        ("queue", "/management/queue", "任务队列"),
        ("reviews", "/management#reviews", "人工复核"),
        ("automation", "/management#automation", "自动化方案"),
        ("master-data", "/management#master-data", "商品资料"),
    )
    links = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, href, label in tabs
    )
    return f'<nav class="section-tabs" aria-label="业务管理分页">{links}</nav>'


def _trade_day_status_label(value: str) -> str:
    return {
        "OPEN": "营业中",
        "CLOSED": "已截单",
        "UNAVAILABLE": "暂不可用",
    }.get(str(value).upper(), str(value))


def _automation_schedule_label(job) -> str:
    if job.interval_minutes is not None:
        suffix = "检查一次" if job.job_type == "REVIEW_TIMEOUT_MAINTENANCE" else "运行一次"
        return f"每 {job.interval_minutes} 分钟{suffix}"
    if job.offset_minutes is not None:
        anchor = "销售计划数据准备后" if job.job_type == "DAILY_TASK_GENERATION" else "日结后"
        return f"{anchor} {job.offset_minutes} 分钟运行"
    schedule = str(job.schedule or "").strip()
    if len(schedule) == 5 and schedule[2] == ":" and schedule.replace(":", "").isdigit():
        return f"每天 {schedule} 运行"
    return "按已设置时间运行"


def render_today(model: TodayReadModel) -> str:
    metrics = "".join(
        f"""
        <article class="metric state-{html(item.state.value)}">
          <span>{html(item.label)}</span>
          <strong>{html(item.value)}</strong>
          <small>{html(item.note)}</small>
        </article>
        """
        for item in model.metrics
    )
    todo = "".join(
        f"""
        <a class="list-row" href="{html(item.url)}">
          <span><strong>{html(item.title)}</strong><small>{html(item.detail)}</small></span>
          <b>{html(item.severity)}</b>
        </a>
        """
        for item in model.todo_items
    ) or '<p class="empty-copy">当前没有待处理事项。</p>'
    timeline = "".join(
        f"<li><time>{html(moment)}</time><strong>{html(title)}</strong><span>{html(detail)}</span></li>"
        for moment, title, detail in model.timeline
    ) or '<li class="empty-copy">今天还没有新的业务动态。</li>'
    return f"""
    <section class="hero compact-hero">
      <div>
        <p class="eyebrow">当前销售日</p>
        <h1>{html(model.platform_trade_date)}</h1>
        <p>{html(model.phase_label)} · 截至 {html(model.observed_at)}</p>
      </div>
      <span class="status-pill">{html(_trade_day_status_label(model.trade_day_status))}</span>
    </section>
    {render_state(model.state)}
    <section class="metric-grid">{metrics}</section>
    <section class="panel">
      <header class="panel-header"><div><h2>品种销售与库存</h2><p>今日销量、成交均价、销售额与数据库库存</p></div><a href="/database/sales-analysis">查看销售分析</a></header>
      {render_table(model.products)}
    </section>
    <section class="panel">
      <header class="panel-header"><div><h2>平台可购上限</h2><p>客户在各平台最多可购买的数量；不等于真实库存或销量</p></div><a href="/database?dataset=prices">查看平台价格</a></header>
      {render_table(model.platform_limits)}
    </section>
    <div class="two-column">
      <section class="panel"><header class="panel-header"><div><h2>需要处理</h2><p>等待人工确认的业务事项</p></div></header>{todo}</section>
      <section class="panel"><header class="panel-header"><div><h2>今日动态</h2><p>最近的自动任务和平台操作结果</p></div></header><ol class="timeline">{timeline}</ol></section>
    </div>
    """


def render_database(model: DatabaseReadModel) -> str:
    section_tabs = (
        ("/database", "业务数据", model.section == "business"),
        ("/database/project", "项目运行数据", model.section == "project"),
        ("/database/sales-analysis", "销售分析", model.section == "sales-analysis"),
        ("/database/dictionary", "字段说明", model.section == "dictionary"),
        ("/database/quality", "质量与新鲜度", model.section == "quality"),
    )
    tabs = "".join(
        f'<a class="{"active" if active else ""}" href="{href}">{label}</a>'
        for href, label, active in section_tabs
    )
    datasets = "".join(
        f'<a class="chip {"active" if key == model.selected_dataset else ""}" href="{html(url)}">{html(label)}</a>'
        for key, label, url in model.dataset_options
    )
    notice = f'<p class="notice">{html(model.notice)}</p>' if model.notice else ""
    platform_options = '<option value="">全部平台</option>' + "".join(
        f'<option value="{html(item)}" {"selected" if item == model.platform_name else ""}>{html(item)}</option>'
        for item in model.platform_options
    )
    filters = (
        f"""
        <form class="filterbar" method="get" action="{html(model.filter_action)}">
          <input type="hidden" name="dataset" value="{html(model.selected_dataset)}">
          <label>交易日<input type="date" name="trade_date" value="{html(model.trade_date)}"></label>
          <label>平台<select name="platform">{platform_options}</select></label>
          <button class="secondary" type="submit">查看</button>
        </form>
        """
        if model.show_business_filters
        else ""
    )
    return f"""
    <section class="hero compact-hero">
      <div><p class="eyebrow">经营与运行记录</p><h1>数据库</h1><p>查看{html(model.section_title)}</p></div>
      <span class="status-pill">交易日 {html(model.trade_date or "不可用")}</span>
    </section>
    <nav class="section-tabs" aria-label="数据库分页">{tabs}</nav>
    {notice}
    <section class="panel">
      <header class="panel-header"><div><h2>{html(model.section_title)}</h2></div></header>
      {filters}
      <nav class="chip-row" aria-label="数据集">{datasets}</nav>
      {render_table(model.table)}
    </section>
    """


def render_management(
    model: ManagementReadModel,
    *,
    csrf_token: str,
    task_scope_options=None,
    task_preview=None,
    task_preview_token: str = "",
    task_receipt: tuple[str, ...] = (),
    task_error: str = "",
    execution_preparation=None,
    execution_receipt: tuple[str, str] | None = None,
    execution_error: str = "",
    review_receipt: tuple[str, str, str] | None = None,
    review_error: str = "",
    automation_receipt: str = "",
    automation_error: str = "",
    master_data_receipt: str = "",
    master_data_error: str = "",
) -> str:
    inventory_options = "".join(
        f'<option value="{html(sku)}" data-qty="{qty}" data-version="{version}">'
        f'{html(label)} · 当前 {qty} 扎</option>'
        for sku, label, qty, version in model.inventory_options
    )
    first_qty = model.inventory_options[0][2] if model.inventory_options else 0
    first_version = model.inventory_options[0][3] if model.inventory_options else 0
    inventory_form = (
        f"""
        <form class="inventory-form" method="post" action="/management/inventory-adjustments" data-inventory-form>
          <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
          <input type="hidden" name="idempotency_key" value="{html(model.inventory_idempotency_key)}">
          <input type="hidden" name="expected_version" value="{first_version}" data-inventory-version>
          <label>商品<select name="internal_sku" data-inventory-product>{inventory_options}</select></label>
          <label>调整值<input name="inventory_delta" type="number" step="1" required placeholder="正数入库，负数减少"></label>
          <p class="form-hint">调整前 <strong data-inventory-before>{first_qty} 扎</strong> · 调整后 <strong data-inventory-after>填写调整值后显示</strong></p>
          <label>调整来源<select name="source_type"><option value="NEW_FLOWER_INBOUND" selected>新花入库</option><option value="MANUAL_STOCKTAKE">人工盘点修正</option><option value="LOSS_ADJUSTMENT">损耗修正</option><option value="RECONCILIATION_CORRECTION">对账修正</option></select></label>
          <label>调整原因<input name="reason" value="新花入库" required></label>
          <button type="submit">确认并记录</button>
        </form>
        """
        if model.inventory_options and model.inventory_state.state.value == "ready"
        else ""
    )
    variety_inventory_rows = "".join(
        f"""
        <tr><td>{html(item.variety)}</td><td>{html(item.grade_summary)}</td><td>{item.current_qty} 扎</td><td>{item.safety_margin_qty} 扎</td><td>{html(_margin_gap_label(item.margin_gap, item.alert_enabled))}</td></tr>
        """
        for item in model.inventory_variety_summaries
    )
    variety_inventory_table = (
        '<div class="table-wrap"><table><thead><tr><th>品种</th><th>等级明细</th><th>真实库存</th><th>安全余量</th><th>当前状态</th></tr></thead>'
        f'<tbody>{variety_inventory_rows}</tbody></table></div>'
        if variety_inventory_rows
        else '<p class="empty-copy">当前没有可展示的品种库存。</p>'
    )
    receipt = ""
    if model.inventory_receipt is not None:
        sku, before, delta, after = model.inventory_receipt
        receipt = (
            '<div class="state-banner state-ready"><strong>库存调整已记录</strong>'
            f'<p>{html(sku)}：{html(before)} {html(delta)} → {html(after)}</p></div>'
        )
    inventory_error = (
        render_state(model.inventory_error)
        if model.inventory_error is not None
        else ""
    )
    task_controls = _render_manual_task_controls(
        csrf_token=csrf_token,
        options=task_scope_options,
        preview=task_preview,
        preview_token=task_preview_token,
        receipt=task_receipt,
        error=task_error,
        idempotency_key=model.task_idempotency_key,
    )
    execution_controls = _render_execution_controls(
        csrf_token=csrf_token,
        task_options=model.pending_task_options,
        preparation=execution_preparation,
        receipt=execution_receipt,
        error=execution_error,
        idempotency_key=model.execution_idempotency_key,
    )
    review_controls = _render_review_controls(
        csrf_token=csrf_token,
        reviews=model.pending_review_options,
        receipt=review_receipt,
        error=review_error,
    )
    automation_controls = _render_automation_controls(
        model,
        csrf_token=csrf_token,
        receipt=automation_receipt,
        error=automation_error,
    )
    master_data_controls = _render_master_data_controls(
        model,
        csrf_token=csrf_token,
        receipt=master_data_receipt,
        error=master_data_error,
    )
    return f"""
    <section class="hero compact-hero">
      <div><p class="eyebrow">业务管理</p><h1>任务、复核与自动化</h1><p>选择任务范围，逐项确认后直接发送执行。</p></div>
    </section>
    {_render_management_tabs("tasks")}
    {task_controls}
    <section class="panel"><header class="panel-header"><div><h2>人工库存调整</h2><p>真实库存按品种汇总、按等级录入；剩余库存自动转入下一交易日</p></div></header><div class="form-shell">{render_state(model.inventory_state)}{variety_inventory_table}{inventory_error}{receipt}{inventory_form}</div></section>
    <section class="panel" id="tasks"><header class="panel-header"><div><h2>当前任务</h2><p>这里也可继续发送其他来源或上次未成功发送的待执行任务</p></div></header>{execution_controls}{render_table(model.pending_tasks)}</section>
    <section class="panel" id="reviews"><header class="panel-header"><div><h2>人工复核</h2><p>查看原因并选择处理结果</p></div></header>{review_controls}{render_table(model.pending_reviews)}</section>
    <section class="panel" id="automation"><header class="panel-header"><div><h2>自动化方案</h2><p>设置定时扫描、日结、任务生成和库存预警</p></div></header>{automation_controls}{render_table(model.automation_runs)}</section>
    {master_data_controls}
    """


def render_task_queue(
    model: TaskQueueReadModel,
    *,
    csrf_token: str,
    cancellation_receipt: str = "",
    cancellation_error: str = "",
    operation_receipt: str = "",
    operation_error: str = "",
) -> str:
    metrics = "".join(
        f"""
        <article class="metric state-{html(item.state.value)}">
          <span>{html(item.label)}</span>
          <strong>{html(item.value)}</strong>
          <small>{html(item.note)}</small>
        </article>
        """
        for item in model.metrics
    )
    component_rows = "".join(
        "<tr>"
        f"<td><strong>{html(item.name)}</strong></td>"
        f"<td>{html(item.state.title)}</td>"
        f"<td>{html(item.state.detail)}</td>"
        "</tr>"
        for item in model.components
    )
    source_options = (
        ("all", "全部来源"),
        ("manual", "人工任务"),
        ("automation", "自动任务"),
        ("emergency", "紧急保护"),
    )
    stage_options = (
        ("all", "全部阶段"),
        ("pending", "待发送"),
        ("queued", "排队中"),
        ("running", "执行中"),
        ("results", "等待回收"),
        ("attention", "需要处理"),
    )
    source_select = "".join(
        f'<option value="{value}" {"selected" if value == model.selected_source else ""}>{label}</option>'
        for value, label in source_options
    )
    stage_select = "".join(
        f'<option value="{value}" {"selected" if value == model.selected_stage else ""}>{label}</option>'
        for value, label in stage_options
    )
    feedback = ""
    if cancellation_receipt:
        feedback += (
            '<div class="state-banner state-ready"><strong>任务已取消</strong>'
            f"<p>{html(cancellation_receipt)}</p></div>"
        )
    if cancellation_error:
        feedback += (
            '<div class="state-banner state-incomplete"><strong>任务未取消</strong>'
            f"<p>{html(cancellation_error)}</p></div>"
        )
    if operation_receipt:
        feedback += (
            '<div class="state-banner state-ready"><strong>平台状态已确认</strong>'
            f"<p>{html(operation_receipt)}</p></div>"
        )
    if operation_error:
        feedback += (
            '<div class="state-banner state-incomplete"><strong>平台状态未保存</strong>'
            f"<p>{html(operation_error)}</p></div>"
        )
    queue_table = _render_task_queue_table(
        model,
        csrf_token=csrf_token,
    )
    return f"""
    <section class="hero compact-hero">
      <div><p class="eyebrow">业务管理</p><h1>任务队列</h1><p>查看任务从创建、发送、执行到结果回收的当前进度。</p></div>
    </section>
    {_render_management_tabs("queue")}
    {feedback}
    {render_state(model.overall)}
    <section class="metric-grid queue-metric-grid">{metrics}</section>
    <section class="panel">
      <header class="panel-header"><div><h2>执行通路</h2><p>确认任务能正常发送、执行并回收结果</p></div><a href="/system">查看系统状态</a></header>
      <div class="table-scroll"><table><thead><tr><th>环节</th><th>当前状态</th><th>说明</th></tr></thead><tbody>{component_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <header class="panel-header"><div><h2>当前任务</h2><p>紧急任务按实际执行优先级排在前面</p></div><a href="/database/project?dataset=tasks">查看任务历史</a></header>
      <form class="filterbar" method="get" action="/management/queue">
        <label>来源<select name="source">{source_select}</select></label>
        <label>阶段<select name="stage">{stage_select}</select></label>
        <button class="secondary" type="submit">查看</button>
      </form>
      {queue_table}
    </section>
    """


def _render_task_queue_table(
    model: TaskQueueReadModel,
    *,
    csrf_token: str,
) -> str:
    table = model.table
    if not table.columns:
        return render_state(table.state)
    has_cancellable = any(model.cancellable_task_ids)
    select_all = (
        '<input type="checkbox" data-queue-cancel-all aria-label="选择本页可取消任务">'
        if has_cancellable
        else "—"
    )
    head = (
        f'<th scope="col" class="queue-select-column">{select_all}</th>'
        + "".join(f'<th scope="col">{html(item)}</th>' for item in table.columns)
        + '<th scope="col">处理</th>'
    )
    body_rows: list[str] = []
    operation_dialogs: list[str] = []
    for index, row in enumerate(table.rows):
        url = table.row_urls[index] if index < len(table.row_urls) else ""
        task_id = (
            model.cancellable_task_ids[index]
            if index < len(model.cancellable_task_ids)
            else ""
        )
        selector = (
            f'<input type="checkbox" name="task_ids" value="{html(task_id)}" '
            'data-queue-cancel-item aria-label="选择取消此任务">'
            if task_id
            else "—"
        )
        cells = [f'<td class="queue-select-column">{selector}</td>']
        for column_index, value in enumerate(row):
            content = html(value)
            if column_index == 0 and url:
                content = f'<a href="{html(url)}">{content}</a>'
            cells.append(f"<td>{content}</td>")
        operation = (
            model.operation_resolution_options[index]
            if index < len(model.operation_resolution_options)
            else None
        )
        if operation is None:
            cells.append("<td>—</td>")
        else:
            dialog_id = f"operation-resolution-{index}"
            cells.append(
                '<td><button class="secondary compact-action" type="button" '
                f'data-dialog-open="{html(dialog_id)}">确认平台状态</button></td>'
            )
            operation_dialogs.append(
                f"""
                <dialog id="{html(dialog_id)}" class="modal-dialog compact-dialog">
                  <form method="post" action="/management/queue/resolve-operation">
                    <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
                    <input type="hidden" name="operation_id" value="{html(operation.operation_id)}">
                    <div class="dialog-header"><div><p class="eyebrow">人工确认平台状态</p><h2>{html(operation.scope)} · {html(operation.action_label)}</h2></div><button class="secondary" type="button" data-dialog-close>关闭</button></div>
                    <p>{html(operation.prompt)}</p>
                    <label>备注（可选）<input name="note" maxlength="500" placeholder="例如：已在平台商品列表中核对"></label>
                    <div class="dialog-actions operation-resolution-actions">
                      <button class="secondary" type="submit" name="outcome" value="TARGET_NOT_APPLIED">{html(operation.not_applied_label)}</button>
                      <button type="submit" name="outcome" value="TARGET_APPLIED">{html(operation.applied_label)}</button>
                    </div>
                  </form>
                </dialog>
                """
            )
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    body = "".join(body_rows)
    if not body:
        body = (
            f'<tr><td colspan="{max(1, len(table.columns) + 2)}">'
            f"{html(table.state.detail or table.state.title)}</td></tr>"
        )
    disabled = "" if has_cancellable else " disabled"
    action_note = (
        "可选择尚未发送或执行失败且尚未重试的任务。"
        if has_cancellable
        else "当前没有可以人工取消的任务。"
    )
    operation_dialog_markup = "".join(operation_dialogs)
    return f"""
    <form id="queue-cancel-form" method="post" action="/management/queue/cancel">
      <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
      <div class="table-state">{render_state(table.state, compact=True)}</div>
      <div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>
      {_render_table_pagination(table)}
      <div class="queue-action-bar"><span>{html(action_note)}</span><button class="danger" type="button" data-queue-cancel-open{disabled}>取消所选任务</button></div>
    </form>
    <dialog id="queue-cancel-dialog" class="modal-dialog compact-dialog">
      <div class="dialog-content"><h2>确认取消任务</h2>
        <p>将取消已选择的 <strong data-queue-cancel-count>0</strong> 项任务。取消后不会发送或自动重试，记录仍会保留在任务历史中。</p>
        <div class="dialog-actions"><button class="secondary" type="button" data-dialog-close>返回</button><button class="danger" type="submit" form="queue-cancel-form" data-queue-cancel-submit>确认取消</button></div>
      </div>
    </dialog>
    {operation_dialog_markup}
    """


def _render_master_data_controls(
    model: ManagementReadModel,
    *,
    csrf_token: str,
    receipt: str,
    error: str,
) -> str:
    feedback = ""
    if receipt:
        feedback += (
            '<div class="state-banner state-ready"><strong>商品资料已更新</strong>'
            f'<p>{html(receipt)}</p></div>'
        )
    if error:
        feedback += (
            '<div class="state-banner state-failed"><strong>商品资料未更新</strong>'
            f'<p>{html(error)}</p></div>'
        )
    key = html(model.master_data_idempotency_key)
    product_options = "".join(
        f'<option value="{html(item.internal_sku)}">{html(item.product_name)} · {html(item.grade)} · {html(item.internal_sku)}</option>'
        for item in model.product_master_options
    )
    product_rows = "".join(
        f"""
        <details class="master-data-row"><summary><strong>{html(item.product_name)} · {html(item.grade)}</strong><span>{html(item.internal_sku)} · 库存 {item.current_stock} {html(item.unit)} · {'销售中' if item.sale_enabled else '暂停销售'}</span></summary>
          <form method="post" action="/management/master-data/products" class="form-grid">
            <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
            <input type="hidden" name="idempotency_key" value="{key}:product:{html(item.internal_sku)}:{item.version}">
            <input type="hidden" name="expected_version" value="{item.version}">
            <input type="hidden" name="internal_sku" value="{html(item.internal_sku)}">
            <label>商品名称<input name="product_name" value="{html(item.product_name)}" required></label>
            <label>等级<input name="grade" value="{html(item.grade)}" required></label>
            <label>规格<input name="stem_length" value="{html(item.stem_length)}" required></label>
            <label>单位<input name="unit" value="{html(item.unit)}" required></label>
            <label>基础成本<input name="base_cost" inputmode="decimal" value="{html(item.base_cost)}" required></label>
            <label class="toggle-label"><input name="sale_enabled" type="checkbox" value="true"{' checked' if item.sale_enabled else ''}>允许销售</label>
            <label>备注<input name="remark" value="{html(item.remark)}"></label>
            <button type="submit">保存商品资料</button>
          </form>
        </details>
        """
        for item in model.product_master_options
    ) or '<p class="empty-copy">尚未录入商品。</p>'
    mapping_status_labels = {
        "VERIFIED": "已确认",
        "UNMAPPED": "待对应",
        "AMBIGUOUS": "需确认",
        "DISABLED": "停用",
    }
    mapping_rows = "".join(
        f"""
        <details class="master-data-row"><summary><strong>{html(item.platform_product_name)} · {html(item.grade)}</strong><span>{html(item.platform_name)} · {html(mapping_status_labels.get(item.mapping_status, item.mapping_status))}</span></summary>
          <form method="post" action="/management/master-data/mappings" class="form-grid">
            <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
            <input type="hidden" name="idempotency_key" value="{key}:mapping:{html(item.mapping_id)}:{item.version}">
            <input type="hidden" name="mapping_id" value="{html(item.mapping_id)}">
            <input type="hidden" name="expected_version" value="{item.version}">
            <label>平台<input name="platform_name" value="{html(item.platform_name)}" required></label>
            <label>平台商品名称<input name="platform_product_name" value="{html(item.platform_product_name)}" required></label>
            <label>等级<input name="grade" value="{html(item.grade)}" required></label>
            <label>内部商品<select name="internal_sku"><option value="">暂不对应</option>{_selected_product_options(model.product_master_options, item.internal_sku)}</select></label>
            <label>查找关键词<input name="search_keyword" value="{html(item.search_keyword)}"></label>
            <label>状态<select name="mapping_status">{_mapping_status_options(item.mapping_status)}</select></label>
            <label>备注<input name="remark" value="{html(item.remark)}"></label>
            <button type="submit">保存对应关系</button>
          </form>
        </details>
        """
        for item in model.product_mapping_options
    ) or '<p class="empty-copy">尚未录入平台商品对应关系。</p>'
    return f"""
    <section class="panel" id="master-data"><header class="panel-header"><div><h2>商品资料</h2><p>维护可销售商品和平台商品的对应关系</p></div><div class="inline-actions"><button type="button" data-dialog-open="new-product-dialog">新增商品</button><button type="button" class="secondary" data-dialog-open="new-mapping-dialog">新增对应关系</button></div></header>
      <div class="form-shell">{feedback}<h3>商品</h3>{product_rows}<h3>平台商品对应关系</h3>{mapping_rows}</div>
    </section>
    <dialog id="new-product-dialog" class="modal-dialog"><form method="post" action="/management/master-data/products" class="form-grid">
      <input type="hidden" name="csrf_token" value="{html(csrf_token)}"><input type="hidden" name="idempotency_key" value="{key}:new-product"><input type="hidden" name="expected_version" value="0">
      <header class="dialog-header"><h2>新增商品</h2><button type="button" class="icon-button" data-dialog-close aria-label="关闭">×</button></header>
      <label>商品 SKU<input name="internal_sku" required></label><label>商品名称<input name="product_name" required></label><label>等级<input name="grade" required></label><label>规格<input name="stem_length" required></label><label>单位<input name="unit" value="扎" required></label><label>基础成本<input name="base_cost" inputmode="decimal" required></label><label class="toggle-label"><input name="sale_enabled" type="checkbox" value="true" checked>允许销售</label><label>备注<input name="remark"></label>
      <p class="form-hint">新增后会同时建立数量为 0 的数据库库存；实际入库请使用“人工库存调整”。</p><footer class="dialog-actions"><button type="button" class="secondary" data-dialog-close>取消</button><button type="submit">新增商品</button></footer>
    </form></dialog>
    <dialog id="new-mapping-dialog" class="modal-dialog"><form method="post" action="/management/master-data/mappings" class="form-grid">
      <input type="hidden" name="csrf_token" value="{html(csrf_token)}"><input type="hidden" name="idempotency_key" value="{key}:new-mapping"><input type="hidden" name="expected_version" value="0"><input type="hidden" name="mapping_id" value="">
      <header class="dialog-header"><h2>新增平台商品对应关系</h2><button type="button" class="icon-button" data-dialog-close aria-label="关闭">×</button></header>
      <label>平台<input name="platform_name" value="蚂蚁花团供应商" required></label><label>平台商品名称<input name="platform_product_name" required></label><label>等级<input name="grade" required></label><label>内部商品<select name="internal_sku"><option value="">暂不对应</option>{product_options}</select></label><label>查找关键词<input name="search_keyword"></label><label>状态<select name="mapping_status">{_mapping_status_options('VERIFIED')}</select></label><label>备注<input name="remark"></label>
      <footer class="dialog-actions"><button type="button" class="secondary" data-dialog-close>取消</button><button type="submit">新增对应关系</button></footer>
    </form></dialog>
    """


def _selected_product_options(products, selected_sku: str) -> str:
    options = []
    for item in products:
        selected = " selected" if item.internal_sku == selected_sku else ""
        options.append(
            f'<option value="{html(item.internal_sku)}"{selected}>'
            f'{html(item.product_name)} · {html(item.grade)}</option>'
        )
    return "".join(options)


def _mapping_status_options(selected: str) -> str:
    options = []
    for value, label in (
        ("VERIFIED", "已确认"),
        ("UNMAPPED", "待对应"),
        ("AMBIGUOUS", "需确认"),
        ("DISABLED", "停用"),
    ):
        selected_attr = " selected" if value == selected else ""
        options.append(
            f'<option value="{value}"{selected_attr}>{label}</option>'
        )
    return "".join(options)


def _render_review_controls(
    *,
    csrf_token: str,
    reviews,
    receipt: tuple[str, str, str] | None,
    error: str,
) -> str:
    feedback = ""
    if receipt is not None:
        _, status, created_task_id = receipt
        task_note = (
            "；已生成后续任务"
            if created_task_id
            else ""
        )
        feedback += (
            '<div class="state-banner state-ready"><strong>复核已提交</strong>'
            f'<p>处理结果：{html(status)}{task_note}</p></div>'
        )
    if error:
        feedback += (
            '<div class="state-banner state-failed"><strong>复核未提交</strong>'
            f'<p>{html(error)}</p></div>'
        )
    cards = []
    for review in reviews:
        forms = []
        for action in review.actions:
            target_price = (
                '<label>目标价格<input name="target_price" inputmode="decimal" required placeholder="不得低于基础成本"></label>'
                if action.requires_target_price
                else ""
            )
            forms.append(
                f"""
                <form method="post" action="/management/reviews/resolve" class="review-action-form">
                  <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
                  <input type="hidden" name="review_task_id" value="{html(review.review_task_id)}">
                  <input type="hidden" name="action" value="{html(action.value)}">
                  {target_price}
                  <label>说明（可选）<input name="note" maxlength="500"></label>
                  <button type="submit">{html(action.label)}</button>
                </form>
                """
            )
        cards.append(
            f"""
            <article class="review-control-card">
              <div><h3>{html(review.title)}</h3><p>{html(review.scope)} · {html(review.reason)}</p></div>
              <div class="review-action-grid">{''.join(forms)}</div>
            </article>
            """
        )
    if not cards:
        cards.append('<p class="empty-copy">当前没有需要人工处理的复核。</p>')
    return '<div class="form-shell">' + feedback + "".join(cards) + "</div>"


def _render_automation_controls(
    model: ManagementReadModel,
    *,
    csrf_token: str,
    receipt: str,
    error: str,
) -> str:
    feedback = ""
    if receipt:
        feedback += (
            '<div class="state-banner state-ready"><strong>自动化方案已更新</strong>'
            f'<p>{html(receipt)}</p></div>'
        )
    if error:
        feedback += (
            '<div class="state-banner state-failed"><strong>自动化方案未更新</strong>'
            f'<p>{html(error)}</p></div>'
        )
    job_cards = []
    for job in model.automation_options:
        interval = ""
        if job.can_edit_interval:
            if job.job_type == "ONLINE_PULSE":
                limits = 'min="10" max="30" step="5"'
            elif job.job_type == "FULL_MARKET_SCAN":
                limits = 'min="60" max="180" step="30"'
            else:
                limits = 'min="5" max="30" step="5"'
            interval = (
                f'<label>间隔（分钟）<input name="interval_minutes" type="number" {limits} '
                f'value="{job.interval_minutes or ""}" required></label>'
            )
        offset = ""
        if job.can_edit_offset:
            offset_minimum = 0 if job.job_type == "DAILY_TASK_GENERATION" else 5
            offset_label = (
                "计划输入后偏移（分钟）"
                if job.job_type == "DAILY_TASK_GENERATION"
                else "结算后偏移（分钟）"
            )
            offset = (
                f'<label>{offset_label}<input name="offset_minutes" type="number" min="{offset_minimum}" max="30" step="1" value="{job.offset_minutes if job.offset_minutes is not None else 5}" required></label>'
            )
        source_controls = ""
        if job.job_type == "DAILY_TASK_GENERATION":
            price_checked = " checked" if "PRICE_RULES" in job.enabled_sources else ""
            listing_checked = " checked" if "LISTING_RULES" in job.enabled_sources else ""
            source_controls = f"""
            <fieldset class="automation-sources"><legend>任务来源</legend>
              <label class="toggle-label"><input type="checkbox" name="source_allowlist" value="PRICE_RULES"{price_checked}>价格规则</label>
              <label class="toggle-label"><input type="checkbox" name="source_allowlist" value="LISTING_RULES"{listing_checked}>上下架规则</label>
            </fieldset>
            """
        rerun = ""
        if job.can_rerun:
            rerun = f"""
            <form method="post" action="/management/automation/rerun" class="automation-rerun-form">
              <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
              <input type="hidden" name="job_id" value="{html(job.job_id)}">
              <input type="hidden" name="idempotency_key" value="{html(model.automation_rerun_idempotency_key)}:{html(job.job_type)}">
              <label>补跑日期<input name="target_trade_date" type="date" required></label>
              <button type="submit" class="secondary-button">补跑一次</button>
            </form>
            """
        checked = " checked" if job.enabled else ""
        job_cards.append(
            f"""
            <article class="automation-control-card">
              <div><h3>{html(job.title)}</h3><p>{html(_automation_schedule_label(job))}</p></div>
              <form method="post" action="/management/automation/configure" class="automation-config-form">
                <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
                <input type="hidden" name="job_id" value="{html(job.job_id)}">
                <label class="toggle-label"><input name="enabled" type="checkbox" value="true"{checked}>启用</label>
                {interval}{offset}{source_controls}<button type="submit">保存方案</button>
              </form>
              {rerun}
            </article>
            """
        )

    policy_cards = []
    for policy in model.inventory_alert_options:
        checked = " checked" if policy.enabled else ""
        policy_cards.append(
            f"""
            <form method="post" action="/management/automation/inventory-alert" class="alert-policy-form">
              <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
              <input type="hidden" name="scope_type" value="{html(policy.scope_type)}">
              <input type="hidden" name="scope_key" value="{html(policy.scope_key)}">
              <input type="hidden" name="expected_version" value="{policy.version}">
              <strong>每个品种共享安全余量</strong>
              <label class="toggle-label"><input name="enabled" type="checkbox" value="true"{checked}>启用预警</label>
              <label>安全余量（扎）<input name="threshold_qty" type="number" min="0" max="9999" value="{policy.threshold_qty}" required></label>
              <label>重复提醒（分钟）<input name="repeat_interval_minutes" type="number" min="30" max="1440" value="{policy.repeat_interval_minutes}" required></label>
              <button type="submit">保存预警</button>
            </form>
            """
        )
    jobs = "".join(job_cards) or '<p class="empty-copy">自动化方案暂不可用。</p>'
    policies = "".join(policy_cards) or '<p class="empty-copy">库存预警配置暂不可用。</p>'
    return (
        '<div class="form-shell">'
        + feedback
        + '<h3>定时方案</h3><div class="automation-control-grid">'
        + jobs
        + '</div><h3>品种库存预警</h3><p class="form-hint">每个品种的各等级真实库存合计达到安全余量时提醒；平台可购上限不参与计算。</p><div class="alert-policy-grid">'
        + policies
        + "</div></div>"
    )


def _render_manual_task_controls(
    *,
    csrf_token: str,
    options,
    preview,
    preview_token: str,
    receipt: tuple[str, ...],
    error: str,
    idempotency_key: str,
) -> str:
    if options is None:
        return '<section class="panel"><header class="panel-header"><h2>创建任务</h2></header><div class="state-banner state-unavailable"><strong>任务范围暂不可用</strong><p>请联系管理员检查商品资料和平台对应关系。</p></div></section>'

    def checks(name: str, values: tuple[str, ...]) -> str:
        return "".join(
            f'<label class="choice-chip"><input type="checkbox" name="{name}" value="{html(value)}"><span>{html(value)}</span></label>'
            for value in values
        )

    receipt_html = (
        '<div class="state-banner state-ready"><strong>任务已创建</strong><p>'
        + html(f"已创建 {len(receipt)} 个任务。")
        + "</p></div>"
        if receipt
        else ""
    )
    error_html = (
        f'<div class="state-banner state-failed"><strong>任务未创建</strong><p>{html(error)}</p></div>'
        if error
        else ""
    )
    platform_choices = checks("platforms", options.platforms)
    platform_ready = bool(options.platforms)
    if not platform_ready:
        platform_choices = (
            '<p class="empty-copy">当前没有可操作的平台商品。</p>'
        )
        error_html += (
            '<div class="state-banner state-unavailable"><strong>暂无可用平台</strong>'
            '<p>请联系管理员维护商品与平台的对应关系。</p></div>'
        )
    preview_html = ""
    if preview is not None:
        editable_blockers = frozenset(
            {
                "目标价格与当前价格相同，请修改后再执行。",
                "加/降价金额不能为 0。",
                "目标价格必须大于 0。",
                "目标价格不能低于商品基础成本。",
                "上架必须填写非负平台目标库存。",
            }
        )

        def item_inputs(item) -> str:
            current = (
                f"{item.current_status or '不可用'} · 价格 "
                f"{str(item.current_price) if item.current_price is not None else '—'} · "
                f"平台库存 {item.current_platform_inventory if item.current_platform_inventory is not None else '—'}"
            )
            price_value = (
                str(item.input_price_value)
                if item.input_price_value is not None
                else ""
            )
            inventory_value = (
                str(item.target_inventory)
                if item.target_inventory is not None
                else ""
            )
            if preview.request.action == "SET_OFFLINE":
                target = (
                    '<span>下架</span>'
                    '<input type="hidden" name="item_price_values" value="">'
                    '<input type="hidden" name="item_target_inventories" value="">'
                )
            elif preview.request.action == "CHANGE_PRICE":
                target = (
                    '<label class="compact-field">加/降价金额'
                    f'<input name="item_price_values" inputmode="decimal" value="{html(price_value)}" data-manual-task-price required></label>'
                    '<input type="hidden" name="item_target_inventories" value="">'
                )
            elif preview.request.action == "SET_ONLINE":
                target = (
                    '<div class="item-value-grid"><label class="compact-field">上架价格'
                    f'<input name="item_price_values" inputmode="decimal" value="{html(price_value)}" data-manual-task-price required></label>'
                    '<label class="compact-field">平台库存'
                    f'<input name="item_target_inventories" type="number" min="0" step="1" value="{html(inventory_value)}" data-manual-task-inventory required></label></div>'
                )
            else:
                target = (
                    '<label class="compact-field">目标价格'
                    f'<input name="item_price_values" inputmode="decimal" value="{html(price_value)}" data-manual-task-price required></label>'
                    '<input type="hidden" name="item_target_inventories" value="">'
                )
            blockers = tuple(getattr(item, "blockers", ()))
            structural_blockers = tuple(
                blocker for blocker in blockers if blocker not in editable_blockers
            )
            if structural_blockers:
                check_result = "需处理：" + "".join(structural_blockers)
            elif blockers:
                check_result = "需处理：" + "".join(blockers)
            else:
                check_result = "可执行"
            current_price = (
                str(item.current_price) if item.current_price is not None else ""
            )
            return (
                f'<tr data-manual-task-row data-action="{html(preview.request.action)}" '
                f'data-current-price="{html(current_price)}" '
                f'data-base-cost="{html(str(item.base_cost))}" '
                f'data-structural-blocked="{1 if structural_blockers else 0}">'
                f'<td data-label="执行"><input type="hidden" name="item_keys" value="{html(item.item_key)}">'
                f'<input type="checkbox" name="included_item_keys" value="{html(item.item_key)}" {"" if item.excluded else "checked"} data-manual-task-include aria-label="执行 {html(item.variety)} {html(item.grade)}"></td>'
                f"<td data-label=\"商品\"><strong>{html(item.variety)} · {html(item.grade)}</strong><br><span class=\"muted\">{html(item.platform_name)}</span></td>"
                f"<td data-label=\"最近记录\">{html(current)}</td><td data-label=\"本次目标\">{target}</td>"
                f'<td data-label="检查结果" data-manual-task-item-status>{html(check_result)}</td>'
                "</tr>"
            )

        rows = "".join(item_inputs(item) for item in preview.items)
        error_summary = (
            '<div class="state-banner state-failed"><strong>需要先处理</strong><p>'
            + html("；".join(preview.errors))
            + "</p></div>"
            if preview.errors
            else ""
        )
        warning_items = tuple(
            item
            for item in preview.included_items
            if getattr(item, "warnings", ()) and not getattr(item, "blockers", ())
        )
        warning_lines = "".join(
            "<li>"
            + html(f"{item.variety} · {item.grade} · {item.platform_name}：")
            + html("；".join(dict.fromkeys(getattr(item, "warnings", ()))))
            + "</li>"
            for item in warning_items
        )
        final_warning = (
            '<div class="state-banner state-warning"><strong>扫描信息需确认</strong>'
            '<p>以下扫描记录不完整或已过期，但不会阻止人工任务。请确认仍要继续：</p>'
            f'<ul class="compact-list">{warning_lines}</ul></div>'
            if warning_items
            else (
                '<div class="state-banner state-ready"><strong>任务信息已核对</strong>'
                '<p>未发现需要额外确认的扫描质量提醒。</p></div>'
            )
        )
        preview_creatable = (
            bool(preview.included_items)
            and not preview.errors
            and all(not getattr(item, "blockers", ()) for item in preview.included_items)
        )
        preview_html = f"""
        <dialog id="manual-task-confirm-dialog" class="modal-dialog wide-dialog" data-auto-open>
          <form id="manual-task-create-form" method="post" action="/management/tasks/create" data-preview-errors="{1 if preview.errors else 0}">
            <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
            <input type="hidden" name="preview_token" value="{html(preview_token)}">
            <header class="dialog-header"><div><p class="eyebrow">任务预览</p><h2>逐项确认价格与平台库存</h2><p>默认带入最近一次平台记录；只修改本次需要变化的项目。</p></div><button type="button" class="icon-button" data-dialog-close aria-label="关闭">×</button></header>
            {error_summary}
            <div class="table-scroll manual-preview-table"><table><thead><tr><th>执行</th><th>商品</th><th>最近记录</th><th>本次目标</th><th>检查结果</th></tr></thead><tbody>{rows}</tbody></table></div>
            <p class="form-hint">检查逐项数值后进入最终确认；只有在最终确认窗口中确认，任务才会创建并发送。</p>
            <footer class="dialog-actions"><button type="button" class="secondary" data-dialog-close>返回修改范围</button><button type="button" data-manual-task-final-open{' disabled' if not preview_creatable else ''}>继续确认</button></footer>
          </form>
        </dialog>
        <dialog id="manual-task-final-dialog" class="modal-dialog compact-dialog">
          <div class="dialog-content">
            <header class="dialog-header"><div><p class="eyebrow">最终确认</p><h2>确认创建并立即执行？</h2><p>确认后将创建 <span data-manual-task-final-count>{len(preview.included_items)}</span> 项任务，并通过既有授权通道立即发送。</p></div><button type="button" class="icon-button" data-manual-task-preview-return aria-label="关闭">×</button></header>
            {final_warning}
            <p class="form-hint">执行端仍会读取平台页面并校验商品身份、旧值和页面状态；本提示不会替代执行安全门禁。</p>
            <footer class="dialog-actions"><button type="button" class="secondary" data-manual-task-preview-return>返回预览</button><button type="button" data-manual-task-final-submit>确认执行</button></footer>
          </div>
        </dialog>
        """

    return f"""
    <section class="panel"><header class="panel-header"><div><h2>创建任务</h2><p>先选择范围，再逐项确认价格与平台库存</p></div><button type="button" data-dialog-open="manual-task-dialog">打开创建窗口</button></header>
      {receipt_html}{error_html}{preview_html}
    </section>
    <dialog id="manual-task-dialog" class="modal-dialog">
      <form method="post" action="/management/tasks/preview">
        <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
        <input type="hidden" name="idempotency_key" value="{html(idempotency_key)}">
        <header class="dialog-header"><div><p class="eyebrow">创建任务</p><h2>选择任务范围</h2></div><button type="button" class="icon-button" data-dialog-close aria-label="关闭">×</button></header>
        <fieldset><legend>品种（可多选）</legend><div class="chip-row">{checks('varieties', options.varieties)}</div></fieldset>
        <fieldset><legend>等级（可多选）</legend><div class="chip-row">{checks('grades', options.grades)}</div></fieldset>
        <fieldset><legend>平台（可多选）</legend><div class="chip-row">{platform_choices}</div></fieldset>
        <label>任务类型<select name="action"><option value="SET_PRICE">调整价格到</option><option value="CHANGE_PRICE">加/降价</option><option value="SET_OFFLINE">下架</option><option value="SET_ONLINE">上架</option></select></label>
        <footer class="dialog-actions"><button type="button" class="secondary" data-dialog-close>取消</button><button type="submit"{' disabled' if not platform_ready else ''}>生成逐项预览</button></footer>
      </form>
    </dialog>
    """


def _render_execution_controls(
    *,
    csrf_token: str,
    task_options: tuple[tuple[str, str], ...],
    preparation,
    receipt: tuple[str, str] | None,
    error: str,
    idempotency_key: str,
) -> str:
    checks = "".join(
        f'<label class="choice-row"><input type="checkbox" name="task_ids" value="{html(task_id)}"><span>{html(label)}</span></label>'
        for task_id, label in task_options
    ) or '<p class="empty-copy">当前没有可选择的待执行平台任务。</p>'
    error_html = (
        f'<div class="state-banner state-failed"><strong>未提交执行</strong><p>{html(error)}</p></div>'
        if error
        else ""
    )
    receipt_html = (
        '<div class="state-banner state-ready"><strong>执行请求已发送</strong><p>可在当前任务和执行记录中查看进度。</p></div>'
        if receipt
        else ""
    )
    confirmation = ""
    if preparation is not None:
        ids = "".join(
            f'<input type="hidden" name="task_ids" value="{html(task_id)}">'
            for task_id in preparation.task_ids
        )
        confirmation = f"""
        <div class="state-banner state-incomplete"><strong>请二次确认</strong><p>{html(preparation.platform_name)} · {preparation.item_count} 项 · {html(_manual_action_label(preparation.action_type))}；请在 {html(preparation.expires_at.strftime('%H:%M'))} 前确认</p></div>
        <form method="post" action="/management/executions/submit" class="inline-actions">
          <input type="hidden" name="csrf_token" value="{html(csrf_token)}">{ids}
          <input type="hidden" name="confirmation_digest" value="{html(preparation.confirmation_digest)}">
          <input type="hidden" name="idempotency_key" value="{html(preparation.idempotency_key)}">
          <button type="submit">确认并发送执行</button>
        </form>
        """
    return f"""
    <div class="form-shell"><h3>授权平台执行</h3><p class="form-hint">仅执行本次选择的任务。</p>{error_html}{receipt_html}{confirmation}
      <form method="post" action="/management/executions/prepare">
        <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
        <input type="hidden" name="idempotency_key" value="{html(idempotency_key)}">
        <div class="choice-list">{checks}</div><button type="submit">预览执行影响</button>
      </form>
    </div>
    """


def render_system(
    model: SystemReadModel,
    *,
    csrf_token: str,
    section: str,
    can_admin: bool,
    maintenance_receipt=None,
    maintenance_error: str = "",
    idempotency_keys: dict[str, str] | None = None,
) -> str:
    keys = idempotency_keys or {}
    tabs = [("status", "/system", "运行状态")]
    if can_admin:
        tabs.extend(
            [
                ("notifications", "/system/notifications", "通知通路"),
                ("data", "/system/data", "数据与备份"),
                ("diagnostics", "/system/diagnostics", "高级诊断"),
            ]
        )
    tab_html = "".join(
        f'<a class="section-tab {"active" if name == section else ""}" href="{url}">{label}</a>'
        for name, url, label in tabs
    )
    feedback = ""
    if maintenance_receipt is not None:
        replay = "（重复请求已复用原结果）" if maintenance_receipt.replayed else ""
        feedback = (
            '<div class="state-banner state-ready"><strong>请求已受理</strong>'
            f'<p>{html(maintenance_receipt.message + replay)}</p></div>'
        )
    elif maintenance_error:
        feedback = (
            '<div class="state-banner state-failed"><strong>请求未受理</strong>'
            f'<p>{html(maintenance_error)}</p></div>'
        )

    section_body = {
        "status": _render_system_status(
            model,
            csrf_token=csrf_token,
            can_admin=can_admin,
            idempotency_key=keys.get("worker", ""),
        ),
        "notifications": _render_system_notifications(
            model,
            csrf_token=csrf_token,
            idempotency_key=keys.get("notification", ""),
        ),
        "data": _render_system_data(
            model,
            csrf_token=csrf_token,
            idempotency_key=keys.get("backup", ""),
        ),
        "diagnostics": _render_system_diagnostics(model),
    }.get(section, "")
    return f"""
    <section class="hero compact-hero"><div><p class="eyebrow">状态与维护</p><h1>系统</h1><p>查看各项服务是否正常，以及需要采取的处理措施。</p></div></section>
    {feedback}
    <nav class="section-tabs" aria-label="系统分区">{tab_html}</nav>
    {section_body}
    """


def _render_system_status(
    model: SystemReadModel,
    *,
    csrf_token: str,
    can_admin: bool,
    idempotency_key: str,
) -> str:
    rows = "".join(
        "<tr>"
        f"<td><strong>{html(item.name)}</strong></td>"
        f"<td>{html(item.state.title)}</td>"
        f"<td>{html(item.state.detail)}</td>"
        f"<td>{html(item.checked_at)}</td>"
        "</tr>"
        for item in model.components
    )
    recovery = ""
    if can_admin:
        recovery = f"""
        <form method="post" action="/system/worker-recovery" class="inline-form">
          <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
          <input type="hidden" name="idempotency_key" value="{html(idempotency_key)}">
          <button type="submit">检查并恢复影刀执行端</button>
        </form>
        """
    return f"""
    {render_state(model.overall)}
    <section class="panel">
      <header class="panel-header"><div><h2>当前运行状态</h2><p>异常服务会同时说明业务影响和处理方式。</p></div>{recovery}</header>
      <div class="table-scroll"><table><thead><tr><th>服务</th><th>当前状态</th><th>业务影响或处理方式</th><th>检查时间</th></tr></thead><tbody>{rows}</tbody></table></div>
    </section>
    """


def _render_system_notifications(
    model: SystemReadModel,
    *,
    csrf_token: str,
    idempotency_key: str,
) -> str:
    state = next(
        (item.state for item in model.components if item.name == "通知发送"),
        model.overall,
    )
    return f"""
    <section class="panel">
      <header class="panel-header"><div><h2>通知通路</h2><p>发送一条测试通知，确认手机端可以正常收到。</p></div></header>
      {render_state(state)}
      <form method="post" action="/system/notifications/test" class="control-form">
        <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
        <input type="hidden" name="idempotency_key" value="{html(idempotency_key)}">
        <p>测试通知不会创建任务，也不会触发平台操作。</p>
        <button type="submit">发送测试通知</button>
      </form>
    </section>
    """


def _render_system_data(
    model: SystemReadModel,
    *,
    csrf_token: str,
    idempotency_key: str,
) -> str:
    database = next(
        (item.state for item in model.components if item.name == "业务数据库"),
        model.overall,
    )
    backup = next(
        (item.state for item in model.components if item.name == "数据备份"),
        model.overall,
    )
    return f"""
    <section class="two-column">
      <article class="panel"><header class="panel-header"><h2>业务数据库</h2></header>{render_state(database)}<p>此处仅检查数据是否可以正常读取；修复操作需要由管理员单独执行。</p></article>
      <article class="panel"><header class="panel-header"><h2>最近备份</h2></header>{render_state(backup)}<p>备份完成后会在这里显示最近一次可用记录。</p></article>
    </section>
    <section class="panel">
      <header class="panel-header"><div><h2>创建数据备份</h2><p>提交后将在后台创建，不影响当前页面使用。</p></div></header>
      <form method="post" action="/system/backups" class="control-form">
        <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
        <input type="hidden" name="idempotency_key" value="{html(idempotency_key)}">
        <label class="checkbox-row"><input type="checkbox" name="confirmation" value="CREATE_BACKUP" required> 我确认创建一份新的数据备份</label>
        <button type="submit">创建备份</button>
      </form>
    </section>
    """


def _render_system_diagnostics(model: SystemReadModel) -> str:
    rows = "".join(
        f"<tr><td>{html(item.name)}</td><td>{html(item.state.title)}</td><td>{html(item.state.detail)}</td></tr>"
        for item in model.components
    )
    return f"""
    <section class="panel">
      <header class="panel-header"><div><h2>高级诊断</h2><p>查看各项服务的检查结果和处理建议。</p></div></header>
      <div class="table-scroll"><table><thead><tr><th>服务</th><th>检查结果</th><th>业务影响或处理方式</th></tr></thead><tbody>{rows}</tbody></table></div>
      <a class="back-link" href="/database/project">查看项目运行历史</a>
      <details><summary>技术说明</summary><p>此页面不提供数据库编辑、脚本执行或本机路径修改功能。</p></details>
    </section>
    """


def render_detail(model: DetailReadModel) -> str:
    fields = "".join(
        f"<div><dt>{html(item.label)}</dt><dd>{html(item.value)}</dd></div>"
        for item in model.fields
    )
    related = "".join(
        f'<a class="list-row" href="{html(url)}"><strong>{html(label)}</strong><span>查看</span></a>'
        for label, url in model.related
    )
    related_panel = (
        f'<section class="panel"><header class="panel-header"><h2>相关记录</h2></header>{related}</section>'
        if related
        else ""
    )
    back = (
        f'<a class="back-link" href="{html(model.back_url)}">← {html(model.back_label)}</a>'
        if model.back_url
        else ""
    )
    return f"""
    {back}
    <section class="hero compact-hero"><div><p class="eyebrow">{html(model.subtitle)}</p><h1>{html(model.title)}</h1></div></section>
    {render_state(model.state)}
    <section class="panel"><dl class="detail-grid">{fields}</dl></section>
    {related_panel}
    """


def render_mobile_review(model: MobileReviewReadModel) -> str:
    operation_block = _render_mobile_operation_confirmations(model)
    action_block = (
        _render_mobile_review_actions(model)
        if model.action_options
        else ""
    )
    facts = "".join(
        f"<div><dt>{label}</dt><dd>{html(value or '—')}</dd></div>"
        for label, value in (
            ("原因", model.reason),
            ("范围", model.scope),
            ("处理期限", model.deadline),
        )
        if value
    )
    return f"""
    <main class="mobile-review-card">
      <p class="eyebrow">人工复核</p><h1>{html(model.review_title)}</h1>
      {render_state(model.state)}
      <dl class="detail-grid single">{facts}</dl>
      {operation_block}
      {action_block}
    </main>
    """


def _render_mobile_review_actions(model: MobileReviewReadModel) -> str:
    forms = []
    for action, label in model.action_options:
        price = (
            '<label>目标价格<input name="target_price" inputmode="decimal" required placeholder="不得低于基础成本"></label>'
            if action == "adjusted"
            else ""
        )
        forms.append(
            f"""
            <form method="post" action="/mobile/review/{html(model.review_task_id)}/resolve" class="mobile-action-form">
              <input type="hidden" name="action" value="{html(action)}">
              {price}<label>说明（可选）<input name="note" maxlength="500"></label>
              <button type="submit">{html(label)}</button>
            </form>
            """
        )
    return '<section><h2>选择处理方式</h2><div class="mobile-action-list">' + "".join(forms) + "</div></section>"


def _render_mobile_operation_confirmations(model: MobileReviewReadModel) -> str:
    if not model.operation_confirmations:
        return ""
    cards = []
    for operation in model.operation_confirmations:
        forms = []
        for outcome, label, class_name in (
            ("TARGET_NOT_APPLIED", operation.not_applied_label, "secondary"),
            ("TARGET_APPLIED", operation.applied_label, ""),
        ):
            forms.append(
                f"""
                <form method="post" action="/mobile/review/{html(model.review_task_id)}/resolve-operation">
                  <input type="hidden" name="operation_id" value="{html(operation.operation_id)}">
                  <input type="hidden" name="outcome" value="{html(outcome)}">
                  <button type="submit" class="{class_name}">{html(label)}</button>
                </form>
                """
            )
        cards.append(
            f"""
            <article class="mobile-operation-card">
              <h3>{html(operation.title)}</h3>
              <p>{html(operation.detail)}</p>
              <div class="mobile-operation-actions">{"".join(forms)}</div>
            </article>
            """
        )
    return (
        '<section><h2>确认平台实际状态</h2>'
        '<div class="mobile-operation-list">'
        + "".join(cards)
        + "</div></section>"
    )


def render_table(model: TableReadModel) -> str:
    if not model.columns:
        return render_state(model.state)
    head = "".join(f'<th scope="col">{html(item)}</th>' for item in model.columns)
    body_rows: list[str] = []
    for index, row in enumerate(model.rows):
        url = model.row_urls[index] if index < len(model.row_urls) else ""
        cells = []
        for column_index, value in enumerate(row):
            content = html(value)
            if column_index == 0 and url:
                content = f'<a href="{html(url)}">{content}</a>'
            cells.append(f"<td>{content}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    body = "".join(body_rows)
    if not body:
        body = f'<tr><td colspan="{max(1, len(model.columns))}">{html(model.state.detail or model.state.title)}</td></tr>'
    return f"""
    <div class="table-state">{render_state(model.state, compact=True)}</div>
    <div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>
    {_render_table_pagination(model)}
    """


def _render_table_pagination(model: TableReadModel) -> str:
    if not (model.has_previous or model.has_next):
        return ""
    previous = (
        f'<a href="{html(model.previous_url)}">上一页</a>'
        if model.has_previous
        else "<span>上一页</span>"
    )
    following = (
        f'<a href="{html(model.next_url)}">下一页</a>'
        if model.has_next
        else "<span>下一页</span>"
    )
    return f'<nav class="pagination" aria-label="分页">{previous}<b>第 {model.page} 页</b>{following}</nav>'


def render_state(model: StateReadModel, *, compact: bool = False) -> str:
    tag = "div"
    detail = f"<p>{html(model.detail)}</p>" if model.detail and not compact else ""
    return f'<{tag} class="state-banner state-{html(model.state.value)}"><strong>{html(model.title)}</strong>{detail}</{tag}>'


def render_notification_drawer(model: NotificationDrawerReadModel) -> str:
    items = "".join(
        f'<a href="{html(item.url)}"><strong>{html(item.title)}</strong><span>{html(item.detail)}</span></a>'
        for item in model.items
    ) or '<p class="empty-copy">当前没有需要处理的通知。</p>'
    return f"""
    <details class="notification-drawer">
      <summary aria-label="通知">通知 <b>{model.total}</b></summary>
      <div class="notification-popover">{items}<a class="history-link" href="{html(model.history_url)}">查看通知历史</a></div>
    </details>
    """
