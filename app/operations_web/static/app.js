"use strict";

for (const form of document.querySelectorAll("[data-inventory-form]")) {
  const product = form.querySelector("[data-inventory-product]");
  const delta = form.querySelector("input[name='inventory_delta']");
  const version = form.querySelector("[data-inventory-version]");
  const before = form.querySelector("[data-inventory-before]");
  const after = form.querySelector("[data-inventory-after]");

  const refresh = () => {
    const selected = product.options[product.selectedIndex];
    const current = Number.parseInt(selected.dataset.qty || "0", 10);
    const change = Number.parseInt(delta.value || "0", 10);
    version.value = selected.dataset.version || "";
    before.textContent = `${current} 扎`;
    after.textContent = delta.value ? `${current + change} 扎` : "填写调整值后显示";
    after.classList.toggle("invalid-value", current + change < 0);
  };

  product.addEventListener("change", refresh);
  delta.addEventListener("input", refresh);
  refresh();
}

for (const opener of document.querySelectorAll("[data-dialog-open]")) {
  opener.addEventListener("click", () => {
    const dialog = document.getElementById(opener.dataset.dialogOpen || "");
    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
  });
}

for (const closer of document.querySelectorAll("[data-dialog-close]")) {
  closer.addEventListener("click", () => closer.closest("dialog")?.close());
}

for (const dialog of document.querySelectorAll("dialog[data-auto-open]")) {
  if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
}

const manualTaskPreviewDialog = document.getElementById("manual-task-confirm-dialog");
const manualTaskFinalDialog = document.getElementById("manual-task-final-dialog");
const manualTaskCreateForm = document.getElementById("manual-task-create-form");
const manualTaskRows = Array.from(document.querySelectorAll("[data-manual-task-row]"));
const manualTaskFinalOpeners = Array.from(
  document.querySelectorAll("[data-manual-task-final-open]"),
);
const manualTaskFinalCount = document.querySelector("[data-manual-task-final-count]");

const manualTaskNumber = (value) => {
  const text = String(value || "").trim();
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
};

const refreshManualTaskPreview = () => {
  let included = 0;
  let blocked = manualTaskCreateForm?.dataset.previewErrors === "1";

  for (const row of manualTaskRows) {
    const include = row.querySelector("[data-manual-task-include]");
    const status = row.querySelector("[data-manual-task-item-status]");
    const selected = Boolean(include?.checked);
    row.classList.toggle("is-excluded", !selected);
    if (!selected) {
      if (status) status.textContent = "不执行";
      continue;
    }
    included += 1;

    if (row.dataset.structuralBlocked === "1") {
      if (status) {
        row.dataset.structuralMessage ||= status.textContent || "需先处理商品资料。";
        status.textContent = row.dataset.structuralMessage;
      }
      blocked = true;
      continue;
    }

    const action = row.dataset.action || "";
    const currentPrice = manualTaskNumber(row.dataset.currentPrice);
    const baseCost = manualTaskNumber(row.dataset.baseCost) ?? 0;
    const priceInput = row.querySelector("[data-manual-task-price]");
    const inventoryInput = row.querySelector("[data-manual-task-inventory]");
    const priceValue = manualTaskNumber(priceInput?.value);
    let issue = "";

    if (action === "SET_PRICE" || action === "SET_ONLINE") {
      if (priceValue === null) issue = "请填写有效的目标价格。";
      else if (priceValue <= 0) issue = "目标价格必须大于 0。";
      else if (priceValue < baseCost) issue = "目标价格不能低于商品基础成本。";
      else if (action === "SET_PRICE" && priceValue === currentPrice) {
        issue = "目标价格与当前价格相同，请修改后再执行。";
      }
    } else if (action === "CHANGE_PRICE") {
      if (priceValue === null) issue = "请填写有效的加/降价金额。";
      else if (priceValue === 0) issue = "加/降价金额不能为 0。";
      else if (currentPrice !== null && currentPrice + priceValue <= 0) {
        issue = "调整后的目标价格必须大于 0。";
      } else if (currentPrice !== null && currentPrice + priceValue < baseCost) {
        issue = "调整后的目标价格不能低于商品基础成本。";
      }
    }

    if (!issue && action === "SET_ONLINE") {
      const inventoryText = String(inventoryInput?.value || "").trim();
      const inventoryValue = Number(inventoryText);
      if (
        !inventoryText ||
        !Number.isInteger(inventoryValue) ||
        inventoryValue < 0
      ) {
        issue = "平台库存必须是非负整数。";
      }
    }

    if (status) status.textContent = issue ? `需处理：${issue}` : "可执行";
    if (issue) blocked = true;
  }

  if (included === 0) blocked = true;
  for (const opener of manualTaskFinalOpeners) opener.disabled = blocked;
  if (manualTaskFinalCount) manualTaskFinalCount.textContent = String(included);
};

for (const row of manualTaskRows) {
  row.querySelector("[data-manual-task-include]")?.addEventListener(
    "change",
    refreshManualTaskPreview,
  );
  row.querySelector("[data-manual-task-price]")?.addEventListener(
    "input",
    refreshManualTaskPreview,
  );
  row.querySelector("[data-manual-task-inventory]")?.addEventListener(
    "input",
    refreshManualTaskPreview,
  );
}

refreshManualTaskPreview();

for (const opener of manualTaskFinalOpeners) {
  opener.addEventListener("click", () => {
    if (!manualTaskCreateForm?.reportValidity()) return;
    manualTaskPreviewDialog?.close();
    if (manualTaskFinalDialog && typeof manualTaskFinalDialog.showModal === "function") {
      manualTaskFinalDialog.showModal();
    }
  });
}

for (const returnButton of document.querySelectorAll("[data-manual-task-preview-return]")) {
  returnButton.addEventListener("click", () => {
    manualTaskFinalDialog?.close();
    if (manualTaskPreviewDialog && typeof manualTaskPreviewDialog.showModal === "function") {
      manualTaskPreviewDialog.showModal();
    }
  });
}

for (const submitter of document.querySelectorAll("[data-manual-task-final-submit]")) {
  submitter.addEventListener("click", () => {
    if (!manualTaskCreateForm?.reportValidity()) {
      manualTaskFinalDialog?.close();
      if (manualTaskPreviewDialog && typeof manualTaskPreviewDialog.showModal === "function") {
        manualTaskPreviewDialog.showModal();
      }
      return;
    }
    submitter.disabled = true;
    manualTaskCreateForm.requestSubmit();
  });
}

const queueCancelForm = document.getElementById("queue-cancel-form");
const queueCancelDialog = document.getElementById("queue-cancel-dialog");
const queueCancelOpen = document.querySelector("[data-queue-cancel-open]");
const queueCancelAll = document.querySelector("[data-queue-cancel-all]");
const queueCancelItems = Array.from(document.querySelectorAll("[data-queue-cancel-item]"));
const queueCancelCount = document.querySelector("[data-queue-cancel-count]");
const queueCancelSubmit = document.querySelector("[data-queue-cancel-submit]");

const refreshQueueCancellation = () => {
  const selected = queueCancelItems.filter((item) => item.checked).length;
  if (queueCancelCount) queueCancelCount.textContent = String(selected);
  if (queueCancelOpen) queueCancelOpen.disabled = selected === 0;
  if (queueCancelAll) {
    queueCancelAll.checked = selected > 0 && selected === queueCancelItems.length;
    queueCancelAll.indeterminate = selected > 0 && selected < queueCancelItems.length;
  }
};

queueCancelAll?.addEventListener("change", () => {
  for (const item of queueCancelItems) item.checked = queueCancelAll.checked;
  refreshQueueCancellation();
});

for (const item of queueCancelItems) item.addEventListener("change", refreshQueueCancellation);

queueCancelOpen?.addEventListener("click", () => {
  refreshQueueCancellation();
  if (!queueCancelItems.some((item) => item.checked)) return;
  if (queueCancelDialog && typeof queueCancelDialog.showModal === "function") {
    queueCancelDialog.showModal();
  }
});

queueCancelForm?.addEventListener("submit", () => {
  if (queueCancelSubmit) queueCancelSubmit.disabled = true;
});

refreshQueueCancellation();
