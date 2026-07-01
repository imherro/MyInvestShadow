const state = {
  latest: null,
  allocationSort: {
    key: null,
    direction: "asc",
  },
};

const collator = new Intl.Collator("zh-CN", { numeric: true, sensitivity: "base" });

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
  defensive_quality: "收益防御",
  cash_like: "现金防御",
  beta_core: "β核心仓",
  alpha_active: "α主动仓",
  defensive_factor: "防御因子仓",
  liquidity: "流动性仓",
  mainline_watch: "主线备选",
  watch: "观察备选",
  candidate: "方向备选",
};
const sleeveShortLabels = {
  core: "核心",
  mainline: "主线",
  thematic: "主题",
  defensive: "防御",
  defensive_quality: "收益",
  cash_like: "现金",
  beta_core: "β核心",
  alpha_active: "α主动",
  defensive_factor: "防御因子",
  liquidity: "流动性",
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
const xueqiuUrl = (code) => {
  const match = String(code || "").match(/^(\d{6})\.(SH|SZ|BJ)$/i);
  if (!match) return null;
  return `https://xueqiu.com/S/${match[2].toUpperCase()}${match[1]}`;
};
const instrumentCodeLink = (row) => {
  const label = instrumentCode(row);
  const url = xueqiuUrl(row.code);
  const text = escapeHtml(label);
  if (!url) return text;
  return `<a class="code-link" href="${url}" target="_blank" rel="noopener noreferrer">${text}</a>`;
};
const instrumentBadge = (row) => row.is_synthetic
  ? `<span class="synthetic-badge">内部</span>`
  : "";
const gateFactor = (row) => {
  const components = row.etf_gate_components || row.components || {};
  const factor = row.gate_weight_factor ?? components.gate_weight_factor;
  const number = Number(factor);
  return Number.isFinite(number) ? number : null;
};

function allocationGate(row) {
  if (row.etf_gate_grade) {
    const factor = gateFactor(row);
    return {
      text: factor === null
        ? `${row.etf_gate_grade} / ${fmtPct(Number(row.etf_execution_ratio || 0) * 100, 0)}`
        : `${row.etf_gate_grade} / x${factor.toFixed(2)}`,
      className: `gate-label grade-${String(row.etf_gate_grade).toLowerCase()}`,
      title: "主线/主题ETF门禁结果，x 为参与仓位分配的等级系数",
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

const chartColor = (key) => ({
  shadow: "#0f766e",
  "510300.SH": "#2563eb",
  "510500.SH": "#b7791f",
}[key] || "#667085");

const shortDate = (value) => {
  const text = String(value || "");
  return text.length >= 10 ? text.slice(5) : text;
};

function lineCoords(values, dates, min, span, width, height, padX, padTop, padBottom) {
  return values
    .map((point) => {
      const index = dates.indexOf(point.basis_date);
      const value = Number(point.value);
      if (index < 0 || !Number.isFinite(value)) return null;
      const x = dates.length === 1
        ? width / 2
        : padX + (index * (width - padX * 2)) / (dates.length - 1);
      const y = height - padBottom - ((value - min) * (height - padTop - padBottom)) / span;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .filter(Boolean);
}

function renderNavChart(points, benchmarks = []) {
  const el = byId("navChart");
  if (!points || points.length === 0) {
    el.innerHTML = `<div class="empty-chart">暂无净值点</div>`;
    return;
  }
  const width = 900;
  const height = 300;
  const padX = 46;
  const padTop = 30;
  const padBottom = 52;
  const dates = points.map((p) => p.basis_date);
  const shadowValues = points.map((p) => ({ basis_date: p.basis_date, value: Number(p.nav) }));
  const benchmarkSeries = (benchmarks || []).map((series) => ({
    ...series,
    values: (series.points || [])
      .filter((p) => p.normalized !== null && p.normalized !== undefined)
      .map((p) => ({ basis_date: p.basis_date, value: Number(p.normalized), close: p.close })),
  }));
  const values = [
    ...shadowValues.map((p) => p.value),
    ...benchmarkSeries.flatMap((series) => series.values.map((p) => p.value)),
  ].filter((value) => Number.isFinite(value));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 0.01);
  const coords = lineCoords(shadowValues, dates, min, span, width, height, padX, padTop, padBottom);
  const last = points[points.length - 1];
  const tickIndexes = [...new Set([
    0,
    Math.floor((points.length - 1) / 2),
    points.length - 1,
  ])];
  const ticks = tickIndexes.map((index) => {
    const x = points.length === 1
      ? width / 2
      : padX + (index * (width - padX * 2)) / (points.length - 1);
    return `
      <line x1="${x.toFixed(1)}" y1="${height - padBottom}" x2="${x.toFixed(1)}" y2="${height - padBottom + 5}" stroke="#98a2b3" />
      <text x="${x.toFixed(1)}" y="${height - 20}" text-anchor="middle" fill="#667085">${shortDate(points[index].basis_date)}</text>
    `;
  }).join("");
  const benchmarkLines = benchmarkSeries.map((series) => {
    const seriesCoords = lineCoords(series.values, dates, min, span, width, height, padX, padTop, padBottom);
    if (!seriesCoords.length) return "";
    const lastPoint = [...(series.points || [])].reverse().find((p) => p.close !== null && p.close !== undefined);
    const label = `${series.code} ${lastPoint ? Number(lastPoint.close).toFixed(3) : "--"}`;
    return `
      <polyline points="${seriesCoords.join(" ")}" fill="none" stroke="${chartColor(series.code)}" stroke-width="2.2" vector-effect="non-scaling-stroke" />
      <text x="${width - padX - 190}" y="${series.code === "510300.SH" ? 42 : 62}" fill="${chartColor(series.code)}">${label}</text>
    `;
  }).join("");
  el.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
      <line x1="${padX}" y1="${height - padBottom}" x2="${width - padX}" y2="${height - padBottom}" stroke="#d9dee7" />
      <line x1="${padX}" y1="${padTop}" x2="${padX}" y2="${height - padBottom}" stroke="#d9dee7" />
      ${ticks}
      ${benchmarkLines}
      <polyline points="${coords.join(" ")}" fill="none" stroke="${chartColor("shadow")}" stroke-width="3" vector-effect="non-scaling-stroke" />
      <circle cx="${coords[coords.length - 1].split(",")[0]}" cy="${coords[coords.length - 1].split(",")[1]}" r="4" fill="#0f766e" />
      <text x="${padX}" y="22" fill="#667085">归一化对比：影子净值 / ETF收盘价</text>
      <text x="${width - padX - 190}" y="22" fill="${chartColor("shadow")}">影子 ${last.basis_date} ${fmtNav(last.nav)}</text>
    </svg>
  `;
}

const numericAllocationSorts = new Set(["target", "drift", "pct"]);
const gradeRank = {
  A: 1,
  B: 2,
  C: 3,
  D: 4,
};

const allocationSortValue = (row, key) => {
  if (key === "code") return instrumentCode(row);
  if (key === "name") return row.name || row.code || "";
  if (key === "sleeve") return sleeveLabels[row.sleeve] || row.sleeve || "";
  if (key === "theme") return `${row.theme || ""} ${row.stage || ""}`;
  if (key === "target") return Number(row.target_weight_ratio);
  if (key === "drift") return Number(row.drift_ratio);
  if (key === "pct") return row.pct_chg === null ? null : Number(row.pct_chg);
  if (key === "gate") {
    const gate = allocationGate(row);
    const grade = String(row.etf_gate_grade || "").toUpperCase();
    return `${gradeRank[grade] || 9} ${gate.text}`;
  }
  return "";
};

function compareAllocationValues(a, b) {
  if (typeof a === "number" && typeof b === "number") return a - b;
  return collator.compare(String(a), String(b));
}

function compareMissingValues(a, b) {
  const aMissing = a === null || a === undefined || Number.isNaN(a);
  const bMissing = b === null || b === undefined || Number.isNaN(b);
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;
  return 0;
}

function sortedAllocations(rows) {
  const { key, direction } = state.allocationSort;
  if (!key) return rows;
  const multiplier = direction === "desc" ? -1 : 1;
  return rows
    .map((row, index) => ({ row, index }))
    .sort((left, right) => {
      const leftValue = allocationSortValue(left.row, key);
      const rightValue = allocationSortValue(right.row, key);
      const missingComparison = compareMissingValues(leftValue, rightValue);
      if (missingComparison !== 0) return missingComparison;
      const comparison = compareAllocationValues(leftValue, rightValue);
      if (comparison !== 0) return comparison * multiplier;
      return left.index - right.index;
    })
    .map((item) => item.row);
}

function updateAllocationSortHeaders() {
  document.querySelectorAll("[data-allocation-sort]").forEach((button) => {
    const key = button.dataset.allocationSort;
    const active = key === state.allocationSort.key;
    const indicator = button.querySelector(".sort-indicator");
    button.classList.toggle("is-sorted", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.title = active
      ? `当前${state.allocationSort.direction === "asc" ? "升序" : "降序"}，点击切换`
      : "点击排序";
    if (indicator) {
      indicator.textContent = active
        ? (state.allocationSort.direction === "asc" ? "↑" : "↓")
        : "↕";
    }
  });
}

function setAllocationSort(key) {
  if (state.allocationSort.key === key) {
    state.allocationSort.direction = state.allocationSort.direction === "asc" ? "desc" : "asc";
  } else {
    state.allocationSort.key = key;
    state.allocationSort.direction = numericAllocationSorts.has(key) ? "desc" : "asc";
  }
  renderAllocations(state.latest?.allocations || []);
}

function renderAllocations(rows) {
  byId("allocationCount").textContent = `${rows.length} 个目标`;
  const tbody = byId("allocationRows");
  updateAllocationSortHeaders();
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8">暂无目标仓位</td></tr>`;
    return;
  }
  tbody.innerHTML = sortedAllocations(rows).map((row) => {
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
        <td>${instrumentCodeLink(row)}${instrumentBadge(row)}</td>
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
        <td>${instrumentCodeLink(row)}${instrumentBadge(row)}</td>
        <td>${escapeHtml(row.name || row.code)}</td>
        <td>${sleeveLabels[row.sleeve] || row.sleeve || "--"}</td>
        <td>${fmtPct(row.previous_weight_ratio, 2)}</td>
        <td>${fmtPct(row.target_weight_ratio, 2)}</td>
        <td class="${driftClass}">${fmtPct(drift, 2)}</td>
      </tr>
    `;
  }).join("");
}

function renderSleeveBlocks(elementId, entries) {
  const el = byId(elementId);
  if (!el) return;
  const rows = (entries || []).filter((row) => Number(row.weight_ratio || 0) >= 0);
  if (!rows.length) {
    el.innerHTML = `<div class="sleeve-item">暂无结构</div>`;
    return;
  }
  const compact = window.innerWidth <= 560;
  const minColumn = compact ? 52 : 78;
  el.style.gridTemplateColumns = rows
    .map((row) => `minmax(${minColumn}px, ${Math.max(Number(row.weight_ratio || 0), 1)}fr)`)
    .join(" ");
  el.innerHTML = rows.map((row) => {
    const key = row.key || row.raw_key || "unknown";
    const marketText = row.market_weight_ratio !== undefined
      ? ` / 市场 ${fmtPct(row.market_weight_ratio, 1)}`
      : "";
    const extra = row.unallocated_ratio > 0
      ? `<small>未落地 ${fmtPct(row.unallocated_ratio, 1)}</small>`
      : row.temporary_parking_ratio > 0
        ? `<small>临时承接 ${fmtPct(row.temporary_parking_ratio, 1)}</small>`
        : "";
    return `
      <div class="sleeve-item sleeve-${key}" title="${escapeHtml(row.label || sleeveLabels[key] || key)} ${fmtPct(row.weight_ratio, 1)}${marketText}">
        <span>${compact ? sleeveShortLabels[key] || row.label || key : row.label || sleeveLabels[key] || key}</span>
        <strong>${fmtPct(row.weight_ratio, 1)}</strong>
        ${extra}
      </div>
    `;
  }).join("");
}

function legacyExecutableRows(summary, defensiveLayers = []) {
  const baseEntries = [
    { key: "core", label: sleeveLabels.core, weight_ratio: Number(summary?.core || 0) },
    { key: "mainline", label: sleeveLabels.mainline, weight_ratio: Number(summary?.mainline || 0) },
    { key: "thematic", label: sleeveLabels.thematic, weight_ratio: Number(summary?.thematic || 0) },
  ];
  const defensiveEntries = (defensiveLayers || [])
    .filter((row) => Number(row.weight_ratio || 0) > 0)
    .map((row) => ({
      key: row.key,
      label: row.label,
      weight_ratio: Number(row.weight_ratio || 0),
    }));
  return defensiveEntries.length
    ? [...baseEntries, ...defensiveEntries]
    : [...baseEntries, { key: "defensive", label: sleeveLabels.defensive, weight_ratio: Number(summary?.defensive || 0) }];
}

function renderSleeveSummary(data) {
  const marketRows = data.market_sleeve_allocation || [];
  const executable = data.shadow_executable_allocation || {};
  const executableRows = executable.rows || legacyExecutableRows(
    data.sleeve_summary || {},
    data.defensive_layers || [],
  );
  renderSleeveBlocks("marketSleeveSummary", marketRows);
  renderSleeveBlocks("sleeveSummary", executableRows);

  const notice = byId("executionNotice");
  if (!notice) return;
  const unallocated = Number(executable.unallocated_alpha_ratio || 0);
  if (unallocated > 0) {
    const parking = executable.temporary_parking_sleeve === "liquidity" ? "流动性仓" : executable.temporary_parking_sleeve || "--";
    notice.innerHTML = `α主动预算未完全落地：未落地 ${fmtPct(unallocated, 2)}，原因：${escapeHtml(executable.unallocated_reason || "门禁后未使用")}，临时承接：${escapeHtml(parking)}。`;
  } else {
    notice.innerHTML = "影子落地结构与市场建议结构无未落地 α 主动预算。";
  }
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
    tbody.innerHTML = `<tr><td colspan="8">暂无门禁记录</td></tr>`;
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
    const factor = gateFactor(row);
    return `
      <tr class="${row.selected ? "selected-row" : ""}">
        <td>${instrumentCodeLink(row)}${instrumentBadge(row)}</td>
        <td>${escapeHtml(row.name || row.code)}</td>
        <td>${sleeveLabels[row.sleeve] || row.sleeve || "--"}</td>
        <td><span class="grade-pill grade-${String(row.grade || "").toLowerCase()}">${escapeHtml(row.grade || "--")}</span></td>
        <td>${Number(row.score || 0).toFixed(1)}</td>
        <td>${factor === null ? "--" : `x${factor.toFixed(2)}`}</td>
        <td>${fmtPct(Number(row.execution_ratio || 0) * 100, 0)} ${selectedMark}</td>
        <td class="reason-cell">${escapeHtml(reasonText || "--")}</td>
      </tr>
    `;
  }).join("");
}

function renderMarketConstraints(constraints) {
  const el = byId("marketConstraints");
  const caps = constraints?.risk_caps || [];
  const keyConstraints = constraints?.key_constraints || [];
  byId("constraintCount").textContent = caps.length ? `${caps.length} 个风险上限` : "暂无风险上限";
  if (!constraints || !Object.keys(constraints).length) {
    el.innerHTML = `<div class="status-item">暂无市场约束</div>`;
    return;
  }
  const scoreLine = [
    `仓位分 ${constraints.market_position_score ?? "--"}`,
    `机会分 ${constraints.market_opportunity_score ?? "--"}`,
    `拥挤惩罚 ${constraints.crowding_penalty ?? "--"}`,
  ].join(" / ");
  const capHtml = caps.slice(0, 5).map((row) => `
    <li>
      <strong>${escapeHtml(row.reason || "--")}</strong>
      <span>${escapeHtml(row.message || row.severity || "")}</span>
    </li>
  `).join("");
  const constraintHtml = keyConstraints.slice(0, 3).map((item) => `
    <li>${escapeHtml(item)}</li>
  `).join("");
  el.innerHTML = `
    <div class="constraint-card">
      <span>市场状态</span>
      <strong>${escapeHtml(constraints.allocation_state || "--")}</strong>
      <p>${escapeHtml(scoreLine)}<br>官方仓位：${escapeHtml(constraints.equity_position_range || "--")}</p>
    </div>
    <div class="constraint-card">
      <span>风险上限</span>
      <ul>${capHtml || "<li>无</li>"}</ul>
    </div>
    <div class="constraint-card">
      <span>关键约束</span>
      <ul>${constraintHtml || "<li>无</li>"}</ul>
    </div>
  `;
}

function renderAllocationPolicy(policy) {
  const el = byId("policyStatus");
  if (!policy || !Object.keys(policy).length) {
    el.innerHTML = "";
    return;
  }
  const sourceLabels = {
    "market.sleeve_allocation": "市场建议结构",
    "market.sleeve_mix": "市场仓位结构",
    "market.equity_position_range": "市场仓位区间",
    "shadow_fallback_score_bands": "影子备用规则",
  };
  const source = sourceLabels[policy.position_source] || policy.position_source || "--";
  const sleeveSource = sourceLabels[policy.sleeve_source] || policy.sleeve_source || "--";
  const status = policy.range_violation ? "越界" : "区间内";
  const statusClass = policy.range_violation ? "negative" : "positive";
  el.innerHTML = `
    <div class="status-item">
      <strong>仓位政策 ${escapeHtml(source)}</strong>
      <p>
        官方区间：${escapeHtml(policy.equity_position_range || "--")}<br>
        主动仓位：${fmtPct(policy.target_active_weight_ratio, 2)}，
        <span class="${statusClass}">${status}</span><br>
        仓位层来源：${escapeHtml(sleeveSource)}${policy.fallback_used ? " / fallback" : ""}
      </p>
    </div>
  `;
}

function renderSources(rows, optionalPolicy = {}) {
  byId("statusText").textContent = rows.length ? "已连接上游" : "暂无快照";
  const optionalStatus = Object.keys(optionalPolicy || {}).length ? `
    <div class="status-item">
      <strong>可选研究上游</strong>
      <p>
        ETF研究：${optionalPolicy.etf_used ? "已参与" : "未参与"}，基准日：${optionalPolicy.etf_basis_date || "--"}<br>
        个股深研：${optionalPolicy.stock_used ? "已参与" : "未参与"}，基准日：${optionalPolicy.stock_basis_date || "--"}<br>
        要求基准日：${optionalPolicy.required_basis_date || "--"}
      </p>
    </div>
  ` : "";
  byId("sourceStatus").innerHTML = optionalStatus + rows.map((row) => `
    <div class="status-item">
      <strong>${row.source} ${row.ok ? "正常" : "异常"}</strong>
      <p>基准日：${row.basis_date || "--"}<br>获取时间：${row.fetched_at || "--"}<br>${row.error || row.content_hash || ""}</p>
    </div>
  `).join("");
}

function renderApiDirectory(catalog) {
  const el = byId("apiDirectory");
  if (!el) return;
  if (!catalog || !Object.keys(catalog).length) {
    byId("apiTotal").textContent = "暂无接口目录";
    el.innerHTML = `<div class="status-item">暂无接口说明</div>`;
    return;
  }
  byId("apiTotal").textContent = `${Number(catalog.total_endpoints || 0)} 个公开接口`;
  const recommended = (catalog.recommended_entrypoints || []).map((row) => `
    <li>
      <a href="${escapeHtml(row.path)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.path)}</a>
      <span>${escapeHtml(row.reason || "")}</span>
    </li>
  `).join("");
  const groups = (catalog.groups || []).map((group) => `
    <li>
      <strong>${escapeHtml(group.name || group.key)}</strong>
      <span>${Number((group.endpoints || []).length)} 个接口</span>
    </li>
  `).join("");
  const safety = (catalog.safety || []).slice(0, 5).map((item) => `
    <li>${escapeHtml(item)}</li>
  `).join("");
  el.innerHTML = `
    <div class="api-card">
      <span>推荐入口</span>
      <ul class="api-list">${recommended || "<li>无</li>"}</ul>
    </div>
    <div class="api-card">
      <span>接口分组</span>
      <ul class="api-list">${groups || "<li>无</li>"}</ul>
    </div>
    <div class="api-card">
      <span>安全边界</span>
      <ul class="api-list">${safety || "<li>无</li>"}</ul>
    </div>
  `;
}

function render(data) {
  state.latest = data;
  renderMetrics(data);
  renderNavChart(data.nav_curve || [], data.benchmark_curve || []);
  renderSleeveSummary(data);
  renderMarketConstraints(data.market_constraints || {});
  renderEtfGate(data.etf_gate_summary || {}, data.etf_gate || []);
  renderAllocations(data.allocations || []);
  renderRebalanceHistory(data.rebalance_history || []);
  renderAllocationPolicy(data.allocation_policy || {});
  renderSources(data.source_status || [], data.optional_source_policy || {});
}

async function loadState() {
  const [data, catalog] = await Promise.all([fetchJson("/api/index"), fetchJson("/api")]);
  render(data);
  renderApiDirectory(catalog);
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
document.querySelectorAll("[data-allocation-sort]").forEach((button) => {
  button.addEventListener("click", () => setAllocationSort(button.dataset.allocationSort));
});
window.addEventListener("resize", () => {
  if (state.latest) renderSleeveSummary(state.latest);
});

loadState().catch((error) => showToast(`加载失败：${error.message}`));
