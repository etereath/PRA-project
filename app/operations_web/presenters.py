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
    ) or '<li class="empty-copy">当前交易日还没有运行事件。</li>'
    return f"""
    <section class="hero compact-hero">
      <div>
        <p class="eyebrow">当前 PRA 交易日</p>
        <h1>{html(model.platform_trade_date)}</h1>
        <p>{html(model.phase_label)} · 截至 {html(model.observed_at)}</p>
      </div>
      <span class="status-pill">{html(model.trade_day_status)}</span>
    </section>
    {render_state(model.state)}
    <section class="metric-grid">{metrics}</section>
    <section class="panel">
      <header class="panel-header"><div><h2>品种销售与库存</h2><p>今日已售、成交均价、销售额与数据库真实库存</p></div><a href="/database/sales-analysis">查看销售分析</a></header>
      {render_table(model.products)}
    </section>
    <div class="two-column">
      <section class="panel"><header class="panel-header"><div><h2>待处理</h2><p>复核、异常和系统影响</p></div></header>{todo}</section>
      <section class="panel"><header class="panel-header"><div><h2>今日时间轴</h2><p>最近运行与执行结果</p></div></header><ol class="timeline">{timeline}</ol></section>
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
      <div><p class="eyebrow">只读数据中心</p><h1>数据库</h1><p>{html(model.section_title)} · 一次只展示一个数据集</p></div>
      <span class="status-pill">交易日 {html(model.trade_date or "不可用")}</span>
    </section>
    <nav class="section-tabs" aria-label="数据库分页">{tabs}</nav>
    {notice}
    <section class="panel">
      <header class="panel-header"><div><h2>{html(model.section_title)}</h2><p>默认 25 条，后端窄查询分页</p></div></header>
      {filters}
      <nav class="chip-row" aria-label="数据集">{datasets}</nav>
      {render_table(model.table)}
    </section>
    """


def render_management(model: ManagementReadModel, *, csrf_token: str) -> str:
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
    return f"""
    <section class="hero compact-hero">
      <div><p class="eyebrow">业务管理</p><h1>当前业务状态</h1><p>当前版本只读展示正式任务、人工复核和自动化运行；创建、授权与处置入口将在后续上线。</p></div>
    </section>
    <section class="panel"><header class="panel-header"><div><h2>人工库存调整</h2><p>只输入有符号调整值；平台库存不会覆盖真实库存</p></div></header><div class="form-shell">{render_state(model.inventory_state)}{receipt}{inventory_form}</div></section>
    <section class="panel"><header class="panel-header"><div><h2>当前任务</h2><p>创建任务不等于真实平台执行授权</p></div></header>{render_table(model.pending_tasks)}</section>
    <section class="panel"><header class="panel-header"><div><h2>人工复核</h2><p>只显示正式复核事实</p></div></header>{render_table(model.pending_reviews)}</section>
    <section class="panel"><header class="panel-header"><div><h2>自动化运行</h2><p>后台生命周期独立于 Web</p></div></header>{render_table(model.automation_runs)}</section>
    """


def render_system(model: SystemReadModel) -> str:
    cards = "".join(
        f"""
        <article class="component-card state-{html(item.state.state.value)}">
          <header><h2>{html(item.name)}</h2><span>{html(item.state.title)}</span></header>
          <p>{html(item.state.detail)}</p><small>检查于 {html(item.checked_at)}</small>
        </article>
        """
        for item in model.components
    )
    return f"""
    <section class="hero compact-hero"><div><p class="eyebrow">当前状态</p><h1>系统</h1><p>只展示组件当前状态；历史业务事实留在数据库。</p></div></section>
    {render_state(model.overall)}
    <section class="component-grid">{cards}</section>
    <p class="notice">本页不会启动 Worker、创建队列目录、迁移 Runtime DB 或修改平台。</p>
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
        f'<section class="panel"><header class="panel-header"><h2>相关事实</h2></header>{related}</section>'
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
    actions = "".join(f"<li>{html(item)}</li>" for item in model.allowed_actions)
    action_block = (
        f'<section><h2>可选处理</h2><ul class="action-list">{actions}</ul></section>'
        if actions
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
