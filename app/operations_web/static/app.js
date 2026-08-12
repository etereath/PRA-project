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
