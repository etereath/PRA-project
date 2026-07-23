"""Pure HTML style and interaction assets for the Web UI."""

from __future__ import annotations

def common_styles() -> str:
    return """
  <style>
    /* business-inputs-layout-v2 */
    :root {
      --bg: #f2ecdf;
      --panel: rgba(255,255,255,0.92);
      --ink: #1f2a30;
      --muted: #5f6d73;
      --accent: #b05833;
      --accent-soft: #ecd7cb;
      --success: #285844;
      --success-bg: #dceddf;
      --error: #8a2f2f;
      --error-bg: #f6dddd;
      --info-bg: #ece6da;
      --line: rgba(31,42,48,0.12);
      --shadow: 0 20px 60px rgba(91, 67, 49, 0.13);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: Georgia, "Times New Roman", "Noto Serif SC", serif;
      background:
        radial-gradient(circle at top left, rgba(176,88,51,0.14), transparent 28%),
        radial-gradient(circle at 88% 10%, rgba(40,88,68,0.12), transparent 22%),
        linear-gradient(180deg, #faf5eb 0%, var(--bg) 100%);
      min-height: 100vh;
    }
    .shell {
      width: min(1120px, calc(100% - 32px));
      margin: 28px auto 44px;
    }
    .wide-shell {
      width: min(1380px, calc(100% - 24px));
    }
    .hero {
      padding: 28px 30px 24px;
      border: 1px solid var(--line);
      border-radius: 28px;
      background: linear-gradient(135deg, rgba(255,255,255,0.86), rgba(255,248,243,0.94));
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
    }
    .hero::after {
      content: "";
      position: absolute;
      inset: auto -90px -90px auto;
      width: 260px;
      height: 260px;
      background: radial-gradient(circle, rgba(176,88,51,0.12), transparent 72%);
    }
    h1 {
      margin: 0 0 10px;
      font-size: clamp(34px, 5vw, 58px);
      line-height: 0.94;
      letter-spacing: -0.03em;
    }
    h2 {
      margin: 0 0 16px;
      font-size: 24px;
    }
    .lede {
      max-width: 820px;
      margin: 0;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.55;
    }
    .nav-strip {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 18px 0;
    }
    .nav-link {
      text-decoration: none;
      color: var(--ink);
      padding: 12px 16px;
      border-radius: 999px;
      background: rgba(255,255,255,0.7);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
    }
    .nav-link.active {
      background: var(--accent);
      color: white;
      border-color: transparent;
    }
    .business-input-tab-panel {
      padding: 16px 18px;
    }
    .business-input-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }
    .business-input-tab {
      min-width: 150px;
      text-align: center;
      text-decoration: none;
      color: var(--ink);
      padding: 12px 18px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: rgba(255,255,255,0.82);
      box-shadow: 0 10px 28px rgba(91, 67, 49, 0.09);
      font-weight: 700;
    }
    .business-input-tab.active {
      background: var(--accent);
      color: white;
      border-color: transparent;
    }
    .layout {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
      margin-top: 18px;
    }
    .panel {
      padding: 22px;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(6px);
      margin-top: 18px;
    }
    .grid {
      display: grid;
      gap: 14px;
    }
    .two-col {
      grid-template-columns: 1fr 1.6fr;
      align-items: end;
    }
    .inventory-form {
      display: grid;
      gap: 16px;
      margin-top: 24px;
    }
    .inventory-row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-items: stretch;
      min-height: 118px;
    }
    .inventory-row .field {
      align-content: start;
    }
    .inventory-row .align-with-primary-control {
      padding-top: 0;
    }
    .inventory-submit-field {
      align-content: start;
    }
    .submit-label-spacer {
      min-height: 34px;
    }
    .inventory-submit-control {
      min-height: 50px;
      display: flex;
      align-items: center;
    }
    .field {
      display: grid;
      gap: 8px;
    }
    .field label, .checkbox {
      font-size: 13px;
      color: var(--muted);
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .product-input-panel .field label,
    .product-edit-form .field label {
      font-size: 16px;
      font-weight: 800;
      color: var(--ink);
      letter-spacing: 0;
      text-transform: none;
    }
    .product-input-panel .help,
    .product-edit-form .help {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
      margin: 0;
    }
    .product-input-panel .help-placeholder {
      min-height: 18px;
      visibility: hidden;
    }
    .field-title-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 34px;
    }
    .product-input-panel .field > label,
    .product-edit-form .field > label {
      display: flex;
      align-items: center;
      min-height: 34px;
    }
    .mini-button {
      padding: 7px 12px;
      font-size: 13px;
      font-weight: 700;
      background: var(--accent-soft);
      color: var(--ink);
      border: 1px solid rgba(176,88,51,0.2);
      box-shadow: none;
      white-space: nowrap;
    }
    input[type="text"], input[type="password"], input[type="number"], input[type="datetime-local"], select, textarea {
      width: 100%;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 16px;
      font: inherit;
      color: var(--ink);
      background: rgba(255,255,255,0.95);
    }
    input[type="text"], input[type="password"], input[type="number"], input[type="datetime-local"], select {
      min-height: 50px;
    }
    .modal-card {
      width: min(520px, calc(100% - 32px));
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 22px;
      color: var(--ink);
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    .modal-card::backdrop {
      background: rgba(31,42,48,0.28);
      backdrop-filter: blur(2px);
    }
    .feedback-dialog h3 {
      margin: 0;
      font-size: 22px;
    }
    .feedback-dialog p {
      margin: 0;
      line-height: 1.65;
      color: var(--ink);
    }
    textarea {
      resize: vertical;
      min-height: 120px;
    }
    .checkbox {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .actions {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 8px;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 13px 18px;
      font: inherit;
      cursor: pointer;
      transition: transform 140ms ease, opacity 140ms ease;
    }
    button:hover { transform: translateY(-1px); }
    .primary {
      background: var(--accent);
      color: white;
    }
    .secondary {
      background: var(--accent-soft);
      color: var(--ink);
    }
    .sticky-actions {
      position: sticky;
      bottom: 10px;
      padding-top: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0), rgba(255,255,255,0.92) 35%);
    }
    .confirm-box {
      margin-top: 16px;
      padding: 16px;
      border-radius: 18px;
      background: rgba(236, 215, 203, 0.5);
      border: 1px solid rgba(176,88,51,0.18);
    }
    .banner {
      margin-top: 18px;
      padding: 14px 16px;
      border-radius: 16px;
      font-size: 15px;
      border: 1px solid transparent;
    }
    .banner.success {
      background: var(--success-bg);
      color: var(--success);
    }
    .banner.error {
      background: var(--error-bg);
      color: var(--error);
    }
    .banner.info {
      background: var(--info-bg);
      color: var(--ink);
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 12px;
    }
    .metric {
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255,248,243,0.86);
      border: 1px solid rgba(176,88,51,0.12);
    }
    .metric .label {
      display: block;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .metric strong {
      display: block;
      margin-top: 6px;
      font-size: 28px;
      line-height: 1;
    }
    .metric-link {
      display: block;
      color: inherit;
      text-decoration: none;
      transition: transform 140ms ease, box-shadow 140ms ease;
    }
    .metric-link:hover {
      transform: translateY(-1px);
      box-shadow: 0 12px 30px rgba(91, 67, 49, 0.12);
    }
    .dashboard-metric {
      min-height: 122px;
    }
    .metric-warn {
      background: rgba(176,88,51,0.14);
      border-color: rgba(176,88,51,0.24);
    }
    .metric-urgent {
      background: rgba(211,113,35,0.18);
      border-color: rgba(211,113,35,0.3);
    }
    .metric-error {
      background: var(--error-bg);
      border-color: rgba(138,47,47,0.2);
    }
    .metric-muted {
      background: rgba(95,109,115,0.14);
      border-color: rgba(95,109,115,0.2);
    }
    .metric-note {
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .metric-links {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 10px;
      font-size: 13px;
    }
    .subtle {
      color: var(--muted);
      margin: 8px 0 0;
      font-size: 14px;
      word-break: break-all;
    }
    .table-wrap {
      overflow-x: auto;
      margin-top: 16px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 640px;
    }
    th, td {
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 14px;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.04em;
      white-space: nowrap;
    }
    .editor-table {
      min-width: 980px;
    }
    .editor-table th:first-child,
    .editor-table td:first-child {
      position: sticky;
      left: 0;
      background: #f8f1e7;
      z-index: 1;
    }
    .row-index {
      color: var(--muted);
      width: 44px;
      white-space: nowrap;
    }
    .cell-input {
      min-width: 140px;
      padding: 10px 12px;
      border-radius: 12px;
    }
    .cell-input.invalid {
      border-color: rgba(138,47,47,0.5);
      background: rgba(246,221,221,0.5);
    }
    .cell-issue {
      margin-top: 6px;
      color: var(--error);
      font-size: 12px;
      line-height: 1.35;
    }
    .issue-list {
      margin: 16px 0 0;
      padding-left: 20px;
      color: var(--error);
      line-height: 1.5;
    }
    .aside-list {
      display: grid;
      gap: 12px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.5;
    }
    .aside-list code {
      display: block;
      margin-top: 4px;
      padding: 10px 12px;
      border-radius: 12px;
      background: rgba(29,42,49,0.05);
      color: var(--ink);
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 13px;
      word-break: break-all;
    }
    pre {
      margin: 0;
      padding: 12px 14px;
      border-radius: 14px;
      background: rgba(29,42,49,0.05);
      color: var(--ink);
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 12px;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .toolbar-panel {
      padding: 14px 18px;
    }
    .toolbar-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .utility-link {
      box-shadow: none;
      background: rgba(255,248,243,0.92);
    }
    .notice-panel {
      border-style: dashed;
      background: rgba(236, 230, 218, 0.85);
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 88px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      line-height: 1.2;
      background: rgba(31,42,48,0.08);
      color: var(--ink);
    }
    .status-success {
      background: var(--success-bg);
      color: var(--success);
    }
    .status-info {
      background: var(--info-bg);
      color: var(--ink);
    }
    .status-error {
      background: var(--error-bg);
      color: var(--error);
    }
    .status-muted {
      background: rgba(95,109,115,0.16);
      color: var(--muted);
    }
    .status-warn {
      background: rgba(176,88,51,0.14);
      color: var(--accent);
    }
    @media (max-width: 960px) {
      .layout, .two-col, .inventory-row { grid-template-columns: 1fr; }
      .inventory-row { min-height: auto; }
      .inventory-row .align-with-primary-control { padding-top: 0; }
      .submit-label-spacer { display: none; }
      .shell, .wide-shell {
        width: min(100% - 18px, 1380px);
        margin-top: 18px;
      }
      .hero, .panel {
        padding: 18px;
        border-radius: 20px;
      }
      .nav-link {
        flex: 1 1 auto;
        text-align: center;
      }
    }
  </style>
  <script>
    document.addEventListener("DOMContentLoaded", () => {
      const cookie = document.cookie.split("; ").find((item) => item.startsWith("pra_runtime_csrf="));
      if (!cookie) return;
      const token = decodeURIComponent(cookie.substring("pra_runtime_csrf=".length));
      document.querySelectorAll("form[method='post']").forEach((form) => {
        const action = form.getAttribute("action") || window.location.pathname;
        if (action === "/runtime/login" || action.startsWith("/mobile/review/")) return;
        let input = form.querySelector("input[name='csrf_token']");
        if (!input) {
          input = document.createElement("input");
          input.type = "hidden";
          input.name = "csrf_token";
          form.appendChild(input);
        }
        input.value = token;
      });
    });
  </script>
"""
