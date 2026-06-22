const state = {
  latest: null,
};

const fmtPct = (value, digits = 1) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return `${Number(value).toFixed(digits)}%`;
};

const fmtNav = (value) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(4);
};

const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#039;",
}[char]));

const byId = (id) => document.getElementById(id);
const sleeveLabels = {
  core: "核心仓位",
  mainline: "主线仓位",
  thematic: "主题仓位",
  defensive: "防御仓位",
  mainline_watch: "主线备选",
  watch: "观察备选",
  candidate: "方向备选",
};
const sleeveShortLabels = {
  core: "核心",
  mainline: "主线",
  thematic: "主题",
  defensive: "防御",
};
const actionClass = {
  new: "positive",
  increase: "positive",
  decrease: "negative",
  exit: "negative",
};
const signClass = (value) => {
  const number = Number(value || 0);
  if (number > 0) return "positive";
  if (number < 0) return "negative";
  return "";
};

const instrumentCode = (row) => row.display_code || row.code || "--";
const instrumentBadge = (row) => row.is_synthetic
  ? `<span class="synthetic-badge">内部</span>`
  : "";

function allocationGate(row) {
  if (row.etf_gate_grade) {
    return {
      text: `${row.etf_gate_grade} / ${fmtPct(Number(row.etf_execution_ratio || 0) * 100, 0)}`,
      className: `gate-label grade-${String(row.etf_gate_grade).toLowerCase()}`,
      title: "主线/主题ETF门禁结果",
    };
  }
  if (row.sleeve === "core") {
    return {
      text: "核心底仓",
      className: "gate-label gate-neutral",
      title: "核心宽基ETF底仓，不走主线/主题门禁",
    };
  }
  if (row.sleeve === "defensive") {
    return {
      text: "防御承接",
      className: "gate-label gate-neutral",
      title: "承接未落地主线/主题仓位，不走主线/主题门禁",
    };
  }
  return {
    text: "待门禁",
    className: "gate-label gate-muted",
    title: "当前目标缺少门禁记录",
  };
}

function showToast(message) {
  const el = byId("toast");
  el.textContent = message;
  el.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    el.hidden = true;
  }, 3200);
}

function setButtonLoading(button, loading) {
  button.disabled = loading;
  button.dataset.label ||= button.textContent;
  button.textContent = loading ? "处理中" : button.dataset.label;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function renderMetrics(data) {
  const run = data.run || {};
  byId("navValue").textContent = fmtNav(run.nav);
  byId("riskBudget").textContent = fmtPct(run.risk_budget_ratio);
  byId("cashRatio").textContent = fmtPct(run.cash_ratio);
  byId("basisDate").textContent = run.basis_date || "--";
  byId("marketRegime").textContent = run.market_regime || "暂无市场状态";
}

function renderNavChart(points) {
  const el = byId("navChart");
  if (!points || points.length === 0) {
    el.innerHTML = `<div class="empty-chart">暂无净值点</div>`;
    return;
  }
  const width = 900;
  const height = 260;
  const pad = 34;
  const navs = points.map((p) => Number(p.nav));
  const min = Math.min(...navs);
  const max = Math.max(...navs);
  const span = Math.max(max - min, 0.01);
  const coords = points.map((p, index) => {
    const x = points.length === 1 ? width / 2 : pad + (index * (width - pad * 2)) / (points.length - 1);
    const y = height - pad - ((Number(p.nav) - min) * (height - pad * 2)) / span;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const last = points[points.length - 1];
  el.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#d9dee7" />
      <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}" stroke="#d9dee7" />
      <polyline points="${coords.join(" ")}" fill="none" stroke="#0f766e" stroke-width="3" vector-effect="non-scaling-stroke" />
      <circle cx="${coords[coords.length - 1].split(",")[0]}" cy="${coords[coords.length - 1].split(",")[1]}" r="4" fill="#0f766e" />
      <text x="${pad}" y="22" fill="#667085">最高 ${fmtNav(max)} / 最低 ${fmtNav(min)}</text>
      <text x="${width - pad - 160}" y="22" fill="#17202a">${last.basis_date}  ${fmtNav(last.nav)}</text>
    </svg>
  `;
}

function renderAllocations(rows) {
  byId("allocationCount").textContent = `${rows.length} 个目标`;
  const tbody = byId("allocationRows");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8">暂无目标仓位</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map((row) => {
    const drift = Number(row.drift_ratio || 0);
    const pct = Number(row.pct_chg || 0);
    const driftClass = signClass(drift);
    const pctClass = signClass(pct);
    const gate = allocationGate(row);
    const rowClasses = [
      "allocation-row",
      `allocation-${row.sleeve || "unknown"}`,
      row.is_synthetic ? "synthetic-row" : "",
    ].filter(Boolean).join(" ");
    return `
      <tr class="${rowClasses}">
        <td>${escapeHtml(instrumentCode(row))}${instrumentBadge(row)}</td>
        <td>${escapeHtml(row.name || row.code)}</td>
        <td>${sleeveLabels[row.sleeve] || row.sleeve || "--"}</td>
        <td class="theme-cell">${escapeHtml(row.theme || "")}<br><span>${escapeHtml(row.stage || "")}</span></td>
        <td>${fmtPct(row.target_weight_ratio, 2)}</td>
        <td class="${driftClass}">${fmtPct(drift, 2)}</td>
        <td class="${pctClass}">${row.pct_chg === null ? "--" : fmtPct(pct, 2)}</td>
        <td><span class="${gate.className}" title="${escapeHtml(gate.title)}">${escapeHtml(gate.text)}</span></td>
      </tr>
    `;
  }).join("");
}

function renderRebalanceHistory(history) {
  const tbody = byId("rebalanceRows");
  if (!history || !history.length) {
    byId("rebalanceCount").textContent = "暂无历史";
    tbody.innerHTML = `<tr><td colspan="8">暂无有效调仓历史</td></tr>`;
    return;
  }

  const rows = history.flatMap((entry) => (entry.changes || []).map((change) => ({
    ...change,
    basis_date: entry.basis_date,
    previous_basis_date: entry.previous_basis_date,
    active_drift_ratio: entry.active_drift_ratio,
  })));
  byId("rebalanceCount").textContent = `${history.length} 次 / ${rows.length} 条变化`;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8">最近运行无仓位变化</td></tr>`;
    return;
  }

  tbody.innerHTML = rows.map((row) => {
    const drift = Number(row.drift_ratio || 0);
    const driftClass = signClass(drift);
    const action = row.action || "";
    return `
      <tr>
        <td>${escapeHtml(row.basis_date || "--")}<br><span>${escapeHtml(row.previous_basis_date || "--")}</span></td>
        <td class="${actionClass[action] || ""}">${escapeHtml(row.action_label || action || "--")}</td>
        <td>${escapeHtml(instrumentCode(row))}${instrumentBadge(row)}</td>
        <td>${escapeHtml(row.name || row.code)}</td>
        <td>${sleeveLabels[row.sleeve] || row.sleeve || "--"}</td>
        <td>${fmtPct(row.previous_weight_ratio, 2)}</td>
        <td>${fmtPct(row.target_weight_ratio, 2)}</td>
        <td class="${driftClass}">${fmtPct(drift, 2)}</td>
      </tr>
    `;
  }).join("");
}

function renderSleeveSummary(summary) {
  const entries = ["core", "mainline", "thematic", "defensive"];
  const compact = window.innerWidth <= 560;
  const minColumn = compact ? 52 : 78;
  const columns = entries
    .map((key) => `minmax(${minColumn}px, ${Math.max(Number(summary?.[key] || 0), 1)}fr)`)
    .join(" ");
  const el = byId("sleeveSummary");
  el.style.gridTemplateColumns = columns;
  el.innerHTML = entries.map((key) => `
    <div class="sleeve-item sleeve-${key}" title="${sleeveLabels[key]} ${fmtPct(summary?.[key] || 0, 1)}">
      <span>${compact ? sleeveShortLabels[key] : sleeveLabels[key]}</span>
      <strong>${fmtPct(summary?.[key] || 0, 1)}</strong>
    </div>
  `).join("");
}

function renderEtfGate(summary, rows) {
  const reviewed = Number(summary.reviewed_count || 0);
  const selected = Number(summary.selected_count || 0);
  const discounted = Number(summary.discounted_selected_count || 0);
  const rejected = Number(summary.rejected_count || 0);
  byId("gateCount").textContent = reviewed ? `${reviewed} 个候选` : "暂无候选";
  const grades = summary.by_grade || {};
  byId("gateSummary").innerHTML = ["A", "B", "C", "D"].map((grade) => `
    <div class="gate-card grade-${grade.toLowerCase()}">
      <span>${grade}</span>
      <strong>${Number(grades[grade] || 0)}</strong>
    </div>
  `).join("") + `
    <div class="gate-card">
      <span>入选</span>
      <strong>${selected}</strong>
    </div>
    <div class="gate-card">
      <span>折扣</span>
      <strong>${discounted}</strong>
    </div>
    <div class="gate-card">
      <span>拒绝</span>
      <strong>${rejected}</strong>
    </div>
  `;

  const tbody = byId("gateRows");
  if (!rows || !rows.length) {
    tbody.innerHTML = `<tr><td colspan="7">暂无门禁记录</td></tr>`;
    return;
  }
  const ordered = [...rows].sort((a, b) => (
    Number(Boolean(b.selected)) - Number(Boolean(a.selected))
    || Number(a.direction_rank || 999) - Number(b.direction_rank || 999)
    || Number(b.score || 0) - Number(a.score || 0)
  ));
  tbody.innerHTML = ordered.map((row) => {
    const reasonText = [...(row.reasons || []), ...(row.reject_reasons || [])]
      .slice(0, 3)
      .join("；");
    const selectedMark = row.selected ? "已入选" : "备选";
    return `
      <tr class="${row.selected ? "selected-row" : ""}">
        <td>${escapeHtml(instrumentCode(row))}${instrumentBadge(row)}</td>
        <td>${escapeHtml(row.name || row.code)}</td>
        <td>${sleeveLabels[row.sleeve] || row.sleeve || "--"}</td>
        <td><span class="grade-pill grade-${String(row.grade || "").toLowerCase()}">${escapeHtml(row.grade || "--")}</span></td>
        <td>${Number(row.score || 0).toFixed(1)}</td>
        <td>${fmtPct(Number(row.execution_ratio || 0) * 100, 0)} ${selectedMark}</td>
        <td class="reason-cell">${escapeHtml(reasonText || "--")}</td>
      </tr>
    `;
  }).join("");
}

function renderSources(rows) {
  byId("statusText").textContent = rows.length ? "已连接上游" : "暂无快照";
  byId("sourceStatus").innerHTML = rows.map((row) => `
    <div class="status-item">
      <strong>${row.source} ${row.ok ? "正常" : "异常"}</strong>
      <p>基准日：${row.basis_date || "--"}<br>获取时间：${row.fetched_at || "--"}<br>${row.error || row.content_hash || ""}</p>
    </div>
  `).join("");
}

function render(data) {
  state.latest = data;
  renderMetrics(data);
  renderNavChart(data.nav_curve || []);
  renderSleeveSummary(data.sleeve_summary || {});
  renderEtfGate(data.etf_gate_summary || {}, data.etf_gate || []);
  renderAllocations(data.allocations || []);
  renderRebalanceHistory(data.rebalance_history || []);
  renderSources(data.source_status || []);
}

async function loadState() {
  const data = await fetchJson("/api/index");
  render(data);
}

async function runRefresh() {
  const button = byId("refreshBtn");
  setButtonLoading(button, true);
  try {
    const data = await fetchJson("/api/run/daily", { method: "POST" });
    render({ ...state.latest, ...data });
    await loadState();
    showToast("影子仓位已更新");
  } catch (error) {
    showToast(`刷新失败：${error.message}`);
  } finally {
    setButtonLoading(button, false);
  }
}

byId("refreshBtn").addEventListener("click", runRefresh);
window.addEventListener("resize", () => {
  if (state.latest) renderSleeveSummary(state.latest.sleeve_summary || {});
});

loadState().catch((error) => showToast(`加载失败：${error.message}`));
