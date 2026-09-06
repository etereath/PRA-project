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

for (const form of document.querySelectorAll("[data-manual-task-form]")) {
  const action = form.querySelector("[data-task-action]");
  const price = form.querySelector("[data-price-field]");
  const priceLabel = form.querySelector("[data-price-label]");
  const priceInput = price.querySelector("input");
  const inventory = form.querySelector("[data-inventory-field]");
  const refresh = () => {
    const value = action.value;
    price.hidden = value === "SET_OFFLINE";
    inventory.hidden = value !== "SET_ONLINE";
    priceInput.required = value !== "SET_OFFLINE";
    inventory.querySelector("input").required = value === "SET_ONLINE";
    const priceCopy = {
      SET_PRICE: ["目标价格", "请输入目标价格"],
      CHANGE_PRICE: ["加/降价金额", "正数加价，负数降价"],
      SET_ONLINE: ["上架价格", "请输入上架价格"],
    }[value];
    if (priceCopy) {
      priceLabel.textContent = priceCopy[0];
      priceInput.placeholder = priceCopy[1];
    }
  };
  action.addEventListener("change", refresh);
  refresh();
}
