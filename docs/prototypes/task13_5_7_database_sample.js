(() => {
  const businessDatasets = [
    {
      name: "销售与订单",
      category: "sales",
      description: "销量、成交金额、均价与下单时段",
      status: "可用 · 截至 09:42",
      tone: "success",
      columns: ["品种 / 等级", "今日已售", "成交均价", "销售额", "当前库存", "观察时间", "质量"],
      rows: [
        [{ title: "艾莎 · A级", sub: "示例经营记录" }, "38 扎", "¥12.00", "¥456", "72 扎", "09:42", { status: "完整", tone: "success" }],
        [{ title: "艾莎 · B级", sub: "示例经营记录" }, "41 扎", "¥9.00", "¥369", "96 扎", "09:42", { status: "完整", tone: "success" }],
        [{ title: "卡罗拉 · A级", sub: "示例经营记录" }, "27 扎", "¥10.00", "¥270", "54 扎", "09:42", { status: "库存偏低", tone: "warning" }],
        [{ title: "蜜桃雪山 · B级", sub: "示例经营记录" }, "22 扎", "¥7.00", "¥154", "63 扎", "09:42", { status: "完整", tone: "success" }],
        [{ title: "其他 · C级", sub: "示例经营记录" }, "14 扎", "¥8.00", "¥112", "33 扎", "09:42", { status: "库存偏低", tone: "warning" }],
      ],
    },
    {
      name: "商品与库存",
      category: "inventory",
      description: "当前库存、今日已售、最近调整及库存状态",
      status: "新鲜 · 2 分钟前",
      tone: "success",
      columns: ["品种 / 等级", "当前库存", "今日已售", "最近调整", "调整来源", "更新时间", "状态"],
      rows: [
        [{ title: "艾莎 · A级", sub: "当前可售" }, "72 扎", "38 扎", "+8 扎", "人工盘点", "09:40", { status: "正常", tone: "success" }],
        [{ title: "艾莎 · B级", sub: "当前可售" }, "96 扎", "41 扎", "+8 扎", "人工盘点", "09:40", { status: "正常", tone: "success" }],
        [{ title: "卡罗拉 · A级", sub: "当前可售" }, "54 扎", "27 扎", "-6 扎", "盘点修正", "09:20", { status: "偏低", tone: "warning" }],
        [{ title: "其他 · C级", sub: "当前可售" }, "33 扎", "14 扎", "0 扎", "无调整", "09:40", { status: "偏低", tone: "warning" }],
      ],
    },
    {
      name: "平台价格",
      category: "price",
      description: "已映射商品的当前售价、安全底价和观察时间",
      status: "可用 · 8 分钟前",
      tone: "success",
      columns: ["平台商品", "等级", "当前售价", "最低安全价", "平台", "观察时间", "状态"],
      rows: [
        [{ title: "艾莎 60cm", sub: "20枝/扎" }, "A级", "¥12.00", "¥7.50", "蚂蚁花团", "09:34", { status: "正常", tone: "success" }],
        [{ title: "艾莎 60cm", sub: "20枝/扎" }, "B级", "¥9.00", "¥6.00", "蚂蚁花团", "09:34", { status: "正常", tone: "success" }],
        [{ title: "卡罗拉 55cm", sub: "20枝/扎" }, "A级", "¥10.00", "¥6.50", "蚂蚁花团", "09:34", { status: "正常", tone: "success" }],
      ],
    },
    {
      name: "交易日结算",
      category: "settlement",
      description: "交易日状态、结算质量、版本和处理结果",
      status: "开放快照 · 进行中",
      tone: "info",
      columns: ["PRA 交易日", "状态", "已售数量", "成交金额", "数据质量", "结算版本", "结果"],
      rows: [
        ["2026-08-08", { status: "开放中", tone: "info" }, "142 扎", "¥1,361", "正常", "待生成", { status: "暂定", tone: "warning" }],
        ["2026-08-07", { status: "已结束", tone: "success" }, "156 扎", "¥1,498", "完整", "v3", { status: "最终", tone: "success" }],
      ],
    },
    {
      name: "品种销售结构",
      category: "sales",
      description: "按品种、等级和销售时段汇总销售贡献",
      status: "可用 · 截至 09:42",
      tone: "success",
      columns: ["品种", "A级销量", "B级销量", "C级销量", "销售额", "高峰时段", "销售占比"],
      rows: [
        ["艾莎", "38 扎", "41 扎", "—", "¥825", "08:00–09:00", "60.6%"],
        ["卡罗拉", "27 扎", "—", "—", "¥270", "07:00–08:00", "19.8%"],
        ["蜜桃雪山", "—", "22 扎", "—", "¥154", "08:00–09:00", "11.3%"],
        ["其他", "—", "—", "14 扎", "¥112", "07:00–08:00", "8.3%"],
      ],
    },
    {
      name: "库存调整流水",
      category: "inventory",
      description: "库存调整前后数量、原因、来源和记录时间",
      status: "完整记录 · 可追溯",
      tone: "success",
      columns: ["商品", "调整前", "调整后", "变动", "调整来源", "记录时间", "结果"],
      rows: [
        ["卡罗拉 · A级", "60 扎", "54 扎", "-6 扎", "销售后盘点", "09:20", { status: "已记录", tone: "success" }],
        ["艾莎 · B级", "88 扎", "96 扎", "+8 扎", "人工盘点", "08:15", { status: "已记录", tone: "success" }],
        ["蜜桃雪山 · B级", "70 扎", "63 扎", "-7 扎", "销售后盘点", "07:50", { status: "已记录", tone: "success" }],
      ],
    },
    {
      name: "商品映射",
      category: "price",
      description: "平台商品与内部品种、等级和映射版本关系",
      status: "1 项需要确认",
      tone: "warning",
      columns: ["平台商品", "平台", "内部品种", "等级", "映射版本", "状态", "处理"],
      rows: [
        ["艾莎 60cm A", "蚂蚁花团", "艾莎", "A级", "v4", { status: "已映射", tone: "success" }, { action: "打开详情" }],
        ["艾莎 60cm B", "蚂蚁花团", "艾莎", "B级", "v4", { status: "已映射", tone: "success" }, { action: "打开详情" }],
        ["平台新品示例", "蚂蚁花团", "待确认", "—", "v4", { status: "需确认", tone: "warning" }, { action: "打开详情" }],
      ],
    },
    {
      name: "历史经营快照",
      category: "settlement",
      description: "按 PRA 交易日保存的版本化只读经营快照",
      status: "30 个交易日可用",
      tone: "success",
      columns: ["PRA 交易日", "状态", "销售额", "已售数量", "期末库存", "快照版本", "质量"],
      rows: [
        ["2026-08-07", "已结束", "¥1,498", "156 扎", "301 扎", "v3", { status: "完整", tone: "success" }],
        ["2026-08-06", "已结束", "¥1,286", "131 扎", "337 扎", "v2", { status: "完整", tone: "success" }],
        ["2026-08-05", "已结束", "¥1,542", "164 扎", "288 扎", "v2", { status: "完整", tone: "success" }],
      ],
    },
  ];

  const systemDatasets = [
    {
      name: "项目运行概览",
      category: "all",
      description: "任务、复核、自动化、异常与执行记录",
      status: "运行正常",
      tone: "success",
      columns: ["数据对象", "类别", "当前状态", "来源", "最近更新", "关联事实", "查看"],
      rows: [
        [{ title: "销售数据例行读取", sub: "运营读取" }, "自动化运行", { status: "进行中", tone: "info" }, "计划运行", "09:40", "3 个子步骤", { action: "打开详情" }],
        [{ title: "库存偏低确认", sub: "卡罗拉 · A级" }, "复核", { status: "等待人工", tone: "warning" }, "经营规则", "09:34", "1 项待处理", { action: "打开详情" }],
        [{ title: "平台商品只读观察", sub: "示例运行记录" }, "执行记录", { status: "已完成", tone: "success" }, "自动化", "09:18", "18 条观察", { action: "打开详情" }],
        [{ title: "消息发送记录", sub: "运营摘要" }, "通知", { status: "已送达", tone: "success" }, "消息中心", "09:12", "1 条消息", { action: "打开详情" }],
      ],
    },
    {
      name: "任务",
      category: "task",
      description: "人工与系统创建的正式业务任务及当前状态",
      status: "2 项待执行",
      tone: "warning",
      columns: ["任务", "任务类型", "范围", "优先级", "创建时间", "当前状态", "查看"],
      rows: [
        ["卡罗拉 A级改价", "调整价格到", "1 个商品", "普通", "09:36", { status: "待执行", tone: "warning" }, { action: "打开详情" }],
        ["库存偏低下架", "立即下架", "1 个商品", "优先", "09:34", { status: "待复核", tone: "warning" }, { action: "打开详情" }],
      ],
    },
    {
      name: "复核",
      category: "review",
      description: "需要人工确认影响、证据和处理方式的事项",
      status: "3 项等待人工",
      tone: "warning",
      columns: ["复核事项", "业务影响", "等待时间", "建议动作", "通知状态", "当前状态", "处理"],
      rows: [
        ["卡罗拉 A级库存偏低", "影响当前销售", "8 分钟", "确认库存", "已送达", { status: "等待人工", tone: "warning" }, { action: "查看并处理" }],
        ["商品映射确认", "影响销售分析", "17 分钟", "确认映射", "已送达", { status: "等待人工", tone: "warning" }, { action: "查看并处理" }],
      ],
    },
    {
      name: "自动化",
      category: "automation",
      description: "运行计划、适用范围、启停状态和最近结果",
      status: "3 项运行中",
      tone: "success",
      columns: ["自动化方案", "运行频率", "业务范围", "最近结果", "最近运行", "当前状态", "管理"],
      rows: [
        ["上架中商品小扫描", "每 10 分钟", "上架中商品", "成功", "09:40", { status: "运行中", tone: "success" }, { action: "管理方案" }],
        ["完整市场扫描", "每 1 小时", "商品与订单", "成功", "09:00", { status: "运行中", tone: "success" }, { action: "管理方案" }],
        ["交易日销售结算", "每日 20:00", "当前交易日", "成功", "昨日 20:05", { status: "运行中", tone: "success" }, { action: "管理方案" }],
      ],
    },
    {
      name: "异常",
      category: "incident",
      description: "当前异常、影响范围、复核状态和处理进度",
      status: "1 项需要关注",
      tone: "warning",
      columns: ["异常", "等级", "影响对象", "首次发现", "复核状态", "处理进度", "查看"],
      rows: [
        ["卡罗拉 A级库存偏低", "S2", "1 个商品", "09:34", "等待人工", { status: "处理中", tone: "warning" }, { action: "打开详情" }],
      ],
    },
    {
      name: "执行记录",
      category: "execution",
      description: "任务执行、只读观察和结果导入的可追溯记录",
      status: "最近运行成功",
      tone: "success",
      columns: ["执行事项", "执行方式", "范围", "开始时间", "完成时间", "结果", "查看"],
      rows: [
        ["平台商品只读观察", "自动化", "18 个商品", "09:16", "09:18", { status: "成功", tone: "success" }, { action: "打开详情" }],
        ["历史订单读取", "人工发起", "2026-07-10", "08:42", "08:46", { status: "成功", tone: "success" }, { action: "打开详情" }],
      ],
    },
  ];

  const collections = { business: businessDatasets, system: systemDatasets };
  const states = {
    business: { index: 0, filter: "all", query: "" },
    system: { index: 0, filter: "all", query: "" },
  };

  const cellText = (cell) => {
    if (typeof cell === "string") return cell;
    return [cell.title, cell.sub, cell.status, cell.action].filter(Boolean).join(" ");
  };

  const renderCell = (cell) => {
    const td = document.createElement("td");
    if (typeof cell === "string") {
      td.textContent = cell;
      return td;
    }
    if (cell.title) {
      const wrapper = document.createElement("span");
      const title = document.createElement("strong");
      const sub = document.createElement("small");
      wrapper.className = "table-title";
      title.textContent = cell.title;
      sub.textContent = cell.sub || "";
      wrapper.append(title, sub);
      td.append(wrapper);
      return td;
    }
    if (cell.status) {
      const status = document.createElement("span");
      status.className = `status ${cell.tone || "success"}`;
      status.textContent = cell.status;
      td.append(status);
      return td;
    }
    if (cell.action) {
      const button = document.createElement("button");
      button.className = "link-button";
      button.type = "button";
      button.textContent = cell.action;
      td.append(button);
    }
    return td;
  };

  const visibleDatasets = (kind) => {
    const { filter } = states[kind];
    return filter === "all"
      ? collections[kind]
      : collections[kind].filter((dataset) => dataset.category === filter);
  };

  const renderTable = (kind, dataset) => {
    const table = document.querySelector(`[data-dataset-table="${kind}"]`);
    const thead = table.querySelector("thead");
    const tbody = table.querySelector("tbody");
    const query = states[kind].query.trim().toLocaleLowerCase("zh-CN");
    const rows = dataset.rows.filter((row) => !query || row.some((cell) => cellText(cell).toLocaleLowerCase("zh-CN").includes(query)));

    const headerRow = document.createElement("tr");
    dataset.columns.forEach((column) => {
      const th = document.createElement("th");
      th.textContent = column;
      headerRow.append(th);
    });
    thead.replaceChildren(headerRow);
    tbody.replaceChildren();

    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.className = "empty-cell";
      td.colSpan = dataset.columns.length;
      td.textContent = "当前数据集中没有符合搜索条件的记录。";
      tr.append(td);
      tbody.append(tr);
      return;
    }

    rows.forEach((row) => {
      const tr = document.createElement("tr");
      row.forEach((cell) => tr.append(renderCell(cell)));
      tbody.append(tr);
    });
  };

  const renderDataset = (kind) => {
    const datasets = visibleDatasets(kind);
    const state = states[kind];
    state.index = Math.min(state.index, Math.max(datasets.length - 1, 0));
    const dataset = datasets[state.index];
    if (!dataset) return;

    document.querySelector(`[data-dataset-name="${kind}"]`).textContent = dataset.name;
    document.querySelector(`[data-dataset-position="${kind}"]`).textContent = `${state.index + 1} / ${datasets.length}`;
    document.querySelector(`[data-dataset-title="${kind}"]`).textContent = dataset.name;
    document.querySelector(`[data-dataset-description="${kind}"]`).textContent = dataset.description;
    const status = document.querySelector(`[data-dataset-status="${kind}"]`);
    status.className = `status ${dataset.tone}`;
    status.textContent = dataset.status;
    document.querySelectorAll(`[data-dataset-filter="${kind}"]`).forEach((button) => {
      button.classList.toggle("active", button.dataset.filter === state.filter);
    });
    renderTable(kind, dataset);
  };

  Object.keys(collections).forEach((kind) => {
    document.querySelector(`[data-dataset-prev="${kind}"]`).addEventListener("click", () => {
      const datasets = visibleDatasets(kind);
      states[kind].index = (states[kind].index - 1 + datasets.length) % datasets.length;
      renderDataset(kind);
    });
    document.querySelector(`[data-dataset-next="${kind}"]`).addEventListener("click", () => {
      const datasets = visibleDatasets(kind);
      states[kind].index = (states[kind].index + 1) % datasets.length;
      renderDataset(kind);
    });
    document.querySelectorAll(`[data-dataset-filter="${kind}"]`).forEach((button) => {
      button.addEventListener("click", () => {
        states[kind].filter = button.dataset.filter;
        states[kind].index = 0;
        renderDataset(kind);
      });
    });
    const search = document.querySelector(`[data-dataset-search="${kind}"]`);
    if (search) {
      search.addEventListener("input", () => {
        states[kind].query = search.value;
        renderDataset(kind);
      });
    }
    renderDataset(kind);
  });
})();
