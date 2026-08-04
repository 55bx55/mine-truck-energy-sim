/* ============================================================
   电动矿卡能耗预测与仿真分析系统 · 前端逻辑
   ECharts 5 + 原生 fetch，无构建步骤
   ============================================================ */

"use strict";

const API = ""; // 同源部署

const state = {
  meta: null,
  motorMap: null,
  cycles: null,
  params: {},          // 当前车辆参数
  currentCycle: 0,     // 时程曲线选中的工况
  results: null,       // 仿真结果
  decompose: null,
  sensitivity: null,
};

const charts = {};
const CYCLE_COLORS = ["#dc2626", "#16a34a", "#2563eb"];

/* ---------------- 工具 ---------------- */
const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const opt = body === undefined
    ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(API + path, opt);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

function fmt(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return Number(v).toFixed(digits);
}

/* ---------------- 初始化 ---------------- */
async function init() {
  try {
    const [meta, motorMap, cyclesData] = await Promise.all([
      api("/api/meta"),
      api("/api/motor-map"),
      api("/api/cycles"),
    ]);
    state.meta = meta;
    state.motorMap = motorMap;
    state.cycles = cyclesData.cycles;
    state.params = { ...meta.vehicle_params };

    $("api-dot").classList.add("ok");
    $("api-status").textContent = "引擎就绪 · 效率MAP峰值 " + meta.motor_map.peak_eta_pct + "%";

    renderParamPanel();
    renderCycleSwitch();
    initCharts();
    await refreshAll();
  } catch (e) {
    $("api-dot").classList.add("err");
    $("api-status").textContent = "引擎加载失败: " + e.message;
    console.error(e);
  }
}

/* ---------------- 参数面板 ---------------- */
const PARAM_FIELDS = [
  { key: "m_vehicle", label: "整备质量 (kg)", step: 1000, min: 10000, max: 80000 },
  { key: "f_r", label: "滚动阻力系数", step: 0.001, min: 0.005, max: 0.05 },
  { key: "Cd", label: "空气阻力系数", step: 0.05, min: 0.3, max: 1.5 },
  { key: "A", label: "迎风面积 (m²)", step: 0.5, min: 4, max: 25 },
  { key: "i_total", label: "主减速比", step: 1, min: 10, max: 100 },
  { key: "eta_T", label: "传动效率", step: 0.005, min: 0.8, max: 1.0 },
  { key: "eta_dis", label: "放电基准效率", step: 0.005, min: 0.85, max: 1.0 },
  { key: "eta_chg", label: "充电基准效率", step: 0.005, min: 0.85, max: 1.0 },
  { key: "batt_capacity_kWh", label: "电池容量 (kWh)", step: 10, min: 100, max: 800 },
  { key: "delta", label: "旋转质量系数", step: 0.01, min: 1.0, max: 1.3 },
];

function renderParamPanel() {
  const box = $("param-group");
  box.innerHTML = PARAM_FIELDS.map((f) => `
    <div class="param-row">
      <label for="p-${f.key}">${f.label}</label>
      <input id="p-${f.key}" type="number" step="${f.step}" min="${f.min}" max="${f.max}"
             value="${state.params[f.key]}">
    </div>`).join("");

  box.addEventListener("input", (e) => {
    const input = e.target;
    const key = input.id.slice(2);
    const v = parseFloat(input.value);
    if (!Number.isNaN(v)) state.params[key] = v;
  });

  $("btn-reset").addEventListener("click", async () => {
    state.params = { ...state.meta.vehicle_params };
    renderParamPanel();
    await refreshAll();
  });

  $("btn-run").addEventListener("click", () => refreshAll());
}

function collectParams() {
  const out = {};
  for (const f of PARAM_FIELDS) out[f.key] = state.params[f.key];
  return out;
}

/* ---------------- 工况切换 ---------------- */
function renderCycleSwitch() {
  const box = $("cycle-switch");
  box.innerHTML = state.cycles.map((c, i) => `
    <button data-i="${i}" class="${i === state.currentCycle ? "active" : ""}">${c.name}</button>`).join("");
  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.currentCycle = parseInt(btn.dataset.i, 10);
    [...box.children].forEach((b) => b.classList.toggle("active", b === btn));
    await refreshTimeseries();
  });
}

/* ---------------- 图表初始化 ---------------- */
function initCharts() {
  charts.map = echarts.init($("chart-map"));
  charts.energy = echarts.init($("chart-energy"));
  charts.decomp = echarts.init($("chart-decomp"));
  charts.ts = echarts.init($("chart-timeseries"));
  charts.sens = echarts.init($("chart-sens"));
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));
}

/* ---------------- 数据刷新 ---------------- */
async function refreshAll() {
  $("btn-run").disabled = true;
  try {
    const body = { params: collectParams() };
    const [sim, sens, decomp] = await Promise.all([
      api("/api/simulate", { ...body, cycle_ids: [0, 1, 2] }),
      api("/api/sensitivity", body),
      api("/api/decompose", { ...body, cycle_id: state.currentCycle }),
    ]);
    state.results = sim.results;
    state.sensitivity = sens;
    state.decompose = decomp;

    renderCards();
    renderMap();
    renderEnergy();
    renderDecomp();
    renderTimeseries();
    renderSensitivity();
  } catch (e) {
    console.error(e);
    $("api-status").textContent = "仿真失败: " + e.message;
  } finally {
    $("btn-run").disabled = false;
  }
}

async function refreshTimeseries() {
  const decomp = await api("/api/decompose", {
    params: collectParams(), cycle_id: state.currentCycle,
  });
  state.decompose = decomp;
  renderDecomp();
  renderTimeseries();
}

/* ---------------- 概览卡片 ---------------- */
function renderCards() {
  const names = ["重载上坡", "平路满载", "长下坡回收"];
  $("cards").innerHTML = state.results.map((r, i) => {
    const eta = r.eta_avg_drive_weighted;
    const pos = r.E_total_kWh >= 0;
    return `
      <div class="card ${i === 0 ? "highlight" : ""}">
        <div class="c-name">${names[i]} · 净能耗</div>
        <div class="c-val ${pos ? "pos" : "neg"}">${pos ? "" : "−"}${Math.abs(r.E_total_kWh).toFixed(2)} <small style="font-size:13px">kWh</small></div>
        <div class="c-sub">驱动段平均效率（功率加权）：${eta === null ? "—" : (eta * 100).toFixed(1) + "%"}
          <br>驱动 ${r.E_drive_kWh.toFixed(2)} kWh · 回收 ${r.E_regen_kWh.toFixed(2)} kWh</div>
      </div>`;
  }).join("");
}

/* ---------------- 效率 MAP + 工作点 ---------------- */
function renderMap() {
  const mm = state.motorMap;
  const res = state.results[state.currentCycle];

  const heatData = [];
  for (let j = 0; j < mm.T.length; j++) {
    for (let i = 0; i < mm.n.length; i++) {
      heatData.push([mm.n[i], mm.T[j], mm.eta[j][i]]);
    }
  }

  const scatter = res.n_motor.map((n, k) => [n, Math.abs(res.T_motor[k])]);

  const extCurve = buildExtCurve(mm);

  charts.map.setOption({
    tooltip: {
      formatter: (p) => {
        if (p.seriesType === "heatmap") {
          return `转速 ${p.value[0]} r/min<br>转矩 ${p.value[1]} N·m<br>效率 ${p.value[2].toFixed(1)}%`;
        }
        if (p.seriesType === "scatter") {
          return `工作点<br>转速 ${p.value[0].toFixed(0)} r/min<br>转矩 ${p.value[1].toFixed(0)} N·m`;
        }
        return "";
      },
    },
    grid: { left: 70, right: 20, top: 30, bottom: 50 },
    xAxis: { type: "value", name: "转速 n (r/min)", min: 0, max: mm.n[mm.n.length - 1] },
    yAxis: { type: "value", name: "转矩 T (N·m)", min: 0, max: mm.T[mm.T.length - 1] },
    visualMap: {
      min: 60, max: 95, calculable: true,
      orient: "horizontal", left: "center", bottom: 0,
      inRange: { color: ["#1a237e", "#2563eb", "#0f766e", "#65a30d", "#facc15", "#ef4444"] },
      text: ["95%", "60%"],
    },
    series: [
      {
        name: "效率", type: "heatmap",
        data: heatData,
        progressive: 2000,
      },
      {
        name: "外特性", type: "line",
        data: extCurve, symbol: "none",
        lineStyle: { type: "dashed", width: 2, color: "#1e293b" },
        z: 5,
      },
      {
        name: "工作点", type: "scatter",
        data: scatter,
        symbolSize: 3, itemStyle: { color: "#111827", opacity: 0.75 },
        z: 6,
      },
    ],
  }, true);
}

function buildExtCurve(mm) {
  const nBase = 2000, TBase = 3000, PkW = TBase * nBase / 9550;
  const pts = [];
  for (const n of mm.n) {
    const T = n <= nBase ? TBase : Math.min(PkW * 9550 / n, mm.T[mm.T.length - 1]);
    if (T <= mm.T[mm.T.length - 1]) pts.push([n, T]);
  }
  return pts;
}

/* ---------------- 三工况能耗对比 ---------------- */
function renderEnergy() {
  const names = ["重载上坡", "平路满载", "长下坡回收"];
  charts.energy.setOption({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { top: 0, data: ["驱动能耗", "回收能量"] },
    grid: { left: 60, right: 20, top: 40, bottom: 40 },
    xAxis: { type: "category", data: names },
    yAxis: { type: "value", name: "能量 (kWh)" },
    series: [
      {
        name: "驱动能耗", type: "bar", stack: "e",
        data: state.results.map((r) => r.E_drive_kWh),
        itemStyle: { color: "#dc2626" }, barWidth: 46,
      },
      {
        name: "回收能量", type: "bar", stack: "e",
        data: state.results.map((r) => r.E_regen_kWh),
        itemStyle: { color: "#16a34a" },
      },
    ],
  }, true);
}

/* ---------------- 能量分解 ---------------- */
function renderDecomp() {
  const d = state.decompose;
  const items = [
    ["坡度势能变化", d["坡度势能变化"]],
    ["电机铜损", d["电机铜损"]],
    ["电机铁损", d["电机铁损"]],
    ["低速附加损耗", d["低速附加损耗"]],
    ["位置偏离损耗", d["位置偏离损耗"]],
    ["传动系统损耗", d["传动系统损耗"]],
    ["滚动阻力损耗", d["滚动阻力损耗"]],
    ["空气阻力损耗", d["空气阻力损耗"]],
    ["其他", d["其他"]],
  ].filter(([, v]) => v > 0.005);

  charts.decomp.setOption({
    tooltip: {
      trigger: "item",
      formatter: (p) => `${p.name}<br>${p.value} kWh（${p.percent.toFixed(1)}%）`,
    },
    legend: { type: "scroll", orient: "vertical", right: 0, top: "middle", textStyle: { fontSize: 12 } },
    series: [{
      name: "能量去向", type: "pie",
      radius: ["38%", "68%"], center: ["38%", "52%"],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: "#fff", borderWidth: 2 },
      label: { show: false },
      emphasis: { label: { show: true, fontSize: 13, fontWeight: "bold" } },
      data: items.map(([name, value]) => ({ name, value })),
      color: ["#0f766e", "#dc2626", "#d97706", "#7c3aed", "#2563eb", "#0891b2", "#65a30d", "#64748b", "#94a3b8"],
    }],
  }, true);
}

/* ---------------- 时程曲线 ---------------- */
function renderTimeseries() {
  const r = state.results[state.currentCycle];
  const t = r.n_motor.map((_, i) => i);

  charts.ts.setOption({
    tooltip: { trigger: "axis" },
    legend: { top: 0, data: ["电机效率", "轮边功率", "累积能耗"] },
    grid: { left: 60, right: 90, top: 40, bottom: 40 },
    xAxis: { type: "category", data: t, name: "时间 (s)" },
    yAxis: [
      { type: "value", name: "效率 (%)", min: 50, max: 100, position: "left" },
      { type: "value", name: "功率 (kW)", position: "right" },
      { type: "value", name: "能耗 (kWh)", position: "right", offset: 55, splitLine: { show: false } },
    ],
    series: [
      {
        name: "电机效率", type: "line",
        data: r.eta_inst.map((x) => (x * 100).toFixed(2)),
        yAxisIndex: 0, symbol: "none", lineStyle: { width: 1.6, color: "#2563eb" },
      },
      {
        name: "轮边功率", type: "line",
        data: r.P_wheel.map((x) => (x / 1000).toFixed(2)),
        yAxisIndex: 1, symbol: "none", lineStyle: { width: 1.4, color: "#dc2626" },
        areaStyle: { opacity: 0.08 },
      },
      {
        name: "累积能耗", type: "line",
        data: r.E_cum.map((x) => (x / 1000).toFixed(3)),
        yAxisIndex: 2, symbol: "none", lineStyle: { width: 2.4, color: "#111827" },
      },
    ],
  }, true);
}

/* ---------------- 敏感性 Tornado ---------------- */
function renderSensitivity() {
  const rows = state.sensitivity.tornado.rows;

  charts.sens.setOption({
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => {
        let s = ps[0].name + "<br>";
        ps.forEach((p) => { s += `${p.seriesName}: ${p.value}%<br>`; });
        return s;
      },
    },
    legend: { top: 0, data: ["−20% 摄动", "+20% 摄动"] },
    grid: { left: 130, right: 60, top: 40, bottom: 30 },
    xAxis: { type: "value", name: "能耗变化率 (%)" },
    yAxis: { type: "category", data: rows.map((r) => r.display_name), inverse: true },
    series: [
      {
        name: "−20% 摄动", type: "bar", stack: "t",
        data: rows.map((r) => r.minus20),
        itemStyle: { color: "#2563eb" }, barWidth: 20,
        label: { show: true, position: "left", formatter: (p) => p.value.toFixed(1) + "%", fontSize: 11, color: "#2563eb" },
      },
      {
        name: "+20% 摄动", type: "bar", stack: "t",
        data: rows.map((r) => r.plus20),
        itemStyle: { color: "#dc2626" },
        label: { show: true, position: "right", formatter: (p) => "+" + p.value.toFixed(1) + "%", fontSize: 11, color: "#dc2626" },
      },
    ],
  }, true);
}

/* ---------------- 启动 ---------------- */
if (typeof echarts === "undefined") {
  $("api-status").textContent = "ECharts 加载失败，请检查网络（CDN）";
  $("api-dot").classList.add("err");
} else {
  init();
}
