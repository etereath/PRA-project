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
    return f"""
    <section class="hero compact-hero">
      <div><p class="eyebrow">业务管理</p><h1>任务、复核与自动化</h1><p>先创建任务，确认无误后再授权平台执行。</p></div>
    </section>
    <nav class="section-tabs" aria-label="业务管理分页"><a class="active" href="#tasks">创建任务</a><a href="#reviews">人工复核</a><a href="#automation">自动化方案</a></nav>
    {task_controls}
    <section class="panel"><header class="panel-header"><div><h2>人工库存调整</h2><p>输入本次增加或减少的数量，并记录来源和原因</p></div></header><div class="form-shell">{render_state(model.inventory_state)}{inventory_error}{receipt}{inventory_form}</div></section>
    <section class="panel" id="tasks"><header class="panel-header"><div><h2>当前任务</h2><p>选择待执行任务并再次确认后，才会发送到平台</p></div></header>{execution_controls}{render_table(model.pending_tasks)}</section>
    <section class="panel" id="reviews"><header class="panel-header"><div><h2>人工复核</h2><p>查看原因并选择处理结果</p></div></header>{review_controls}{render_table(model.pending_reviews)}</section>
    <section class="panel" id="automation"><header class="panel-header"><div><h2>自动化方案</h2><p>设置定时扫描、日结、任务生成和库存预警</p></div></header>{automation_controls}{render_table(model.automation_runs)}</section>
    """


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
        scope_label = "全部商品默认值" if policy.scope_type == "DEFAULT" else policy.scope_key
        policy_cards.append(
            f"""
            <form method="post" action="/management/automation/inventory-alert" class="alert-policy-form">
              <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
              <input type="hidden" name="scope_type" value="{html(policy.scope_type)}">
              <input type="hidden" name="scope_key" value="{html(policy.scope_key)}">
              <input type="hidden" name="expected_version" value="{policy.version}">
              <strong>{html(scope_label)}</strong>
              <label class="toggle-label"><input name="enabled" type="checkbox" value="true"{checked}>启用预警</label>
              <label>库存阈值<input name="threshold_qty" type="number" min="0" max="9999" value="{policy.threshold_qty}" required></label>
              <label>重复提醒（分钟）<input name="repeat_interval_minutes" type="number" min="30" max="1440" value="{policy.repeat_interval_minutes}" required></label>
              <button type="submit">保存预警</button>
            </form>
            """
        )
    sku_options = "".join(
        f'<option value="{html(sku)}">{html(label)}</option>'
        for sku, label, _, _ in model.inventory_options
    )
    if sku_options:
        default_policy = next(
            (
                item
                for item in model.inventory_alert_options
                if item.scope_type == "DEFAULT"
            ),
            None,
        )
        threshold = default_policy.threshold_qty if default_policy else 0
        repeat = default_policy.repeat_interval_minutes if default_policy else 60
        policy_cards.append(
            f"""
            <form method="post" action="/management/automation/inventory-alert" class="alert-policy-form">
              <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
              <input type="hidden" name="scope_type" value="SKU">
              <input type="hidden" name="expected_version" value="0">
              <strong>新增商品覆盖</strong>
              <label>商品<select name="scope_key">{sku_options}</select></label>
              <label class="toggle-label"><input name="enabled" type="checkbox" value="true" checked>启用预警</label>
              <label>库存阈值<input name="threshold_qty" type="number" min="0" max="9999" value="{threshold}" required></label>
              <label>重复提醒（分钟）<input name="repeat_interval_minutes" type="number" min="30" max="1440" value="{repeat}" required></label>
              <button type="submit">添加覆盖</button>
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
        + '</div><h3>数据库库存预警</h3><div class="alert-policy-grid">'
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
        + html(f"已创建 {len(receipt)} 个任务；如需执行，请在下方单独确认。")
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
        rows = "".join(
            "<tr>"
            f'<td><input type="checkbox" name="excluded_item_keys" value="{html(item.item_key)}" {"checked" if item.excluded else ""} aria-label="排除 {html(item.variety)} {html(item.grade)}"></td>'
            f"<td>{html(item.variety)} · {html(item.grade)}</td>"
            f"<td>{html(item.platform_name)}</td>"
            f"<td>{html(item.current_status or '不可用')} / {html(str(item.current_price) if item.current_price is not None else '—')}</td>"
            f"<td>{html(str(item.target_price) if item.target_price is not None else _manual_action_label(item.action_type))}</td>"
            f"<td>{html('；'.join(item.blockers) or '可创建')}</td>"
            "</tr>"
            for item in preview.items
        )
        hidden_scope = "".join(
            f'<input type="hidden" name="{name}" value="{html(value)}">'
            for name, values in (
                ("varieties", preview.request.varieties),
                ("grades", preview.request.grades),
                ("platforms", preview.request.platforms),
            )
            for value in values
        )
        hidden_values = (
            f'<input type="hidden" name="action" value="{html(preview.request.action)}">'
            f'<input type="hidden" name="price_value" value="{html(str(preview.request.price_value or ""))}">'
            f'<input type="hidden" name="target_inventory" value="{html(str(preview.request.target_inventory if preview.request.target_inventory is not None else ""))}">'
            f'<input type="hidden" name="idempotency_key" value="{html(preview.request.idempotency_key)}">'
        )
        create_button = (
            f"""
            <form method="post" action="/management/tasks/create" class="inline-actions">
              <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
              <input type="hidden" name="preview_token" value="{html(preview_token)}">
              <input type="hidden" name="preview_digest" value="{html(preview.preview_digest)}">
              <button type="submit">创建 {len(preview.included_items)} 个任务</button>
            </form>
            """
            if preview.creatable
            else '<p class="form-hint">部分项目未通过检查，暂时不能创建任务。</p>'
        )
        preview_html = f"""
        <div class="form-shell"><h3>任务预览</h3>
          <form method="post" action="/management/tasks/preview">
            <input type="hidden" name="csrf_token" value="{html(csrf_token)}">{hidden_scope}{hidden_values}
            <div class="table-scroll"><table><thead><tr><th>排除</th><th>商品</th><th>平台</th><th>当前状态</th><th>目标</th><th>检查结果</th></tr></thead><tbody>{rows}</tbody></table></div>
            <button class="secondary" type="submit">重新预览</button>
          </form>{create_button}
        </div>
        """

    return f"""
    <section class="panel"><header class="panel-header"><div><h2>创建任务</h2><p>按品种、等级和平台多选；先预览，再创建</p></div><button type="button" data-dialog-open="manual-task-dialog">打开创建窗口</button></header>
      {receipt_html}{error_html}{preview_html}
    </section>
    <dialog id="manual-task-dialog" class="modal-dialog">
      <form method="post" action="/management/tasks/preview" data-manual-task-form>
        <input type="hidden" name="csrf_token" value="{html(csrf_token)}">
        <input type="hidden" name="idempotency_key" value="{html(idempotency_key)}">
        <header class="dialog-header"><div><p class="eyebrow">创建任务</p><h2>选择任务范围</h2></div><button type="button" class="icon-button" data-dialog-close aria-label="关闭">×</button></header>
        <fieldset><legend>品种（可多选）</legend><div class="chip-row">{checks('varieties', options.varieties)}</div></fieldset>
        <fieldset><legend>等级（可多选）</legend><div class="chip-row">{checks('grades', options.grades)}</div></fieldset>
        <fieldset><legend>平台（可多选）</legend><div class="chip-row">{platform_choices}</div></fieldset>
        <label>任务类型<select name="action" data-task-action><option value="SET_PRICE">调整价格到</option><option value="CHANGE_PRICE">加/降价</option><option value="SET_OFFLINE">下架</option><option value="SET_ONLINE">上架</option></select></label>
        <label data-price-field><span data-price-label>目标价格</span><input name="price_value" inputmode="decimal" placeholder="请输入目标价格"></label>
        <label data-inventory-field hidden>平台目标库存<input name="target_inventory" type="number" min="0" step="1"></label>
        <footer class="dialog-actions"><button type="button" class="secondary" data-dialog-close>取消</button><button type="submit"{' disabled' if not platform_ready else ''}>预览任务</button></footer>
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
    pagination = ""
    if model.has_previous or model.has_next:
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
        pagination = f'<nav class="pagination" aria-label="分页">{previous}<b>第 {model.page} 页</b>{following}</nav>'
    return f"""
    <div class="table-state">{render_state(model.state, compact=True)}</div>
    <div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>
    {pagination}
    """


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
