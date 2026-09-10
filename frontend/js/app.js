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
  params: {},           // 当前车辆参数
  presets: [],          // 车型预设
  currentCycle: 0,      // 当前选中工况（0-2 预设，3 自定义）
  results: null,        // 3 预设工况仿真结果
  customResult: null,   // 自定义工况仿真结果
  customCycleData: null, // 自定义工况原始序列
  decompose: null,
  sensitivity: null,
  compare: null,
  efficiencyComparison: null,
};

const charts = {};
const CYCLE_NAMES = ["重载上坡", "平路满载", "长下坡回收"];
const CYCLE_COLORS = ["#dc2626", "#16a34a", "#2563eb"];
const PRESET_COLORS = ["#0f766e", "#2563eb", "#d97706"];

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

function linspace(a, b, n) {
  if (n <= 0) return [];
  if (n === 1) return [b];
  return Array.from({ length: n }, (_, i) => a + (b - a) * i / (n - 1));
}

/* 当前选中的仿真结果 */
function getCurrentResult() {
  return state.currentCycle < 3 ? state.results[state.currentCycle] : state.customResult;
}
function getCurrentCycleName() {
  return state.currentCycle < 3 ? CYCLE_NAMES[state.currentCycle] : (state.customCycleData?.name || "自定义工况");
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
    state.presets = meta.vehicle_presets || [];
    state.params = { ...meta.vehicle_params };

    $("api-dot").classList.add("ok");
    $("api-status").textContent = "引擎就绪 · 效率MAP峰值 " + meta.motor_map.peak_eta_pct + "%";

    renderParamPanel();
    renderPresetSelect();
    renderCustomPanel();
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
  { key: "soc_init", label: "初始 SOC", step: 0.05, min: 0.3, max: 1.0 },
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
    $("preset-select").value = "";
    renderParamPanel();
    await refreshAll();
  });

  $("btn-run").addEventListener("click", () => refreshAll());
}

function renderPresetSelect() {
  const sel = $("preset-select");
  sel.innerHTML = '<option value="">手动配置</option>' +
    state.presets.map((p) => `<option value="${p.key}">${p.name}</option>`).join("");
  sel.addEventListener("change", () => {
    const preset = state.presets.find((p) => p.key === sel.value);
    if (!preset) return;
    Object.assign(state.params, preset.params);
    renderParamPanel();
  });
}

function collectParams() {
  const out = {};
  for (const f of PARAM_FIELDS) out[f.key] = state.params[f.key];
  return out;
}

/* ---------------- 自定义工况 ---------------- */
const CUSTOM_FIELDS = [
  { key: "v_max", label: "最高速 (km/h)", step: 1, min: 5, max: 40, def: 20 },
  { key: "t_acc", label: "加速时间 (s)", step: 5, min: 5, max: 120, def: 40 },
  { key: "t_cruise", label: "匀速时间 (s)", step: 5, min: 0, max: 300, def: 120 },
  { key: "t_dec", label: "减速时间 (s)", step: 5, min: 5, max: 120, def: 40 },
  { key: "grade", label: "坡度 (%)", step: 0.5, min: -15, max: 15, def: 6 },
  { key: "payload", label: "载重 (kg)", step: 5000, min: 0, max: 120000, def: 60000 },
];

function renderCustomPanel() {
  const box = $("custom-group");
  box.innerHTML = CUSTOM_FIELDS.map((f) => `
    <div class="param-row">
      <label for="c-${f.key}">${f.label}</label>
      <input id="c-${f.key}" type="number" step="${f.step}" min="${f.min}" max="${f.max}" value="${f.def}">
    </div>`).join("");

  $("btn-custom").addEventListener("click", async () => {
    const v = {};
    CUSTOM_FIELDS.forEach((f) => {
      const el = $(`c-${f.key}`);
      v[f.key] = parseFloat(el.value);
      if (Number.isNaN(v[f.key])) v[f.key] = f.def;
    });

    const tAcc = Math.max(1, Math.round(v.t_acc));
    const tCrs = Math.max(0, Math.round(v.t_cruise));
    const tDec = Math.max(1, Math.round(v.t_dec));
    const speed = [
      ...linspace(0, v.v_max, tAcc),
      ...Array(tCrs).fill(v.v_max),
      ...linspace(v.v_max, 0, tDec),
    ];
    const grade = Array(speed.length).fill(v.grade);
    const payload = Array(speed.length).fill(v.payload);

    state.customCycleData = { v: speed, grade, payload, name: `自定义(${v.v_max}km/h,${v.grade}%)` };
    $("btn-custom").disabled = true;
    $("btn-custom").textContent = "仿真中…";
    try {
      const res = await api("/api/simulate", {
        params: collectParams(),
        custom_cycle: state.customCycleData,
      });
      state.customResult = res.results[0];
      state.currentCycle = 3;
      renderCycleSwitch();
      await refreshTimeseriesOnly();
      $("api-status").textContent = "自定义工况完成 · " + state.customResult.E_total_kWh + " kWh";
    } catch (e) {
      console.error(e);
      $("api-status").textContent = "自定义工况失败: " + e.message;
    } finally {
      $("btn-custom").disabled = false;
      $("btn-custom").textContent = "生成并仿真";
    }
  });
}

/* ---------------- 工况切换 ---------------- */
function renderCycleSwitch() {
  const box = $("cycle-switch");
  const labels = [...CYCLE_NAMES];
  if (state.customResult) labels.push(state.customCycleData.name);
  box.innerHTML = labels.map((name, i) => `
    <button data-i="${i}" class="${i === state.currentCycle ? "active" : ""}">${name}</button>`).join("");
  box.addEventListener("click", async (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;
    state.currentCycle = parseInt(btn.dataset.i, 10);
    [...box.children].forEach((b) => b.classList.toggle("active", b === btn));
    await refreshTimeseriesOnly();
  });
}

/* ---------------- 图表初始化 ---------------- */
function initCharts() {
  charts.map = echarts.init($("chart-map"));
  charts.energy = echarts.init($("chart-energy"));
  charts.decomp = echarts.init($("chart-decomp"));
  charts.efficiencyCompare = echarts.init($("chart-efficiency-compare"));
  charts.ts = echarts.init($("chart-timeseries"));
  charts.sens = echarts.init($("chart-sens"));
  charts.compare = echarts.init($("chart-compare"));
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c.resize()));
}

/* ---------------- 数据刷新 ---------------- */
async function refreshAll() {
  $("btn-run").disabled = true;
  try {
    const body = { params: collectParams() };
    const [sim, sens, decomp, cmp, effCmp] = await Promise.all([
      api("/api/simulate", { ...body, cycle_ids: [0, 1, 2] }),
      api("/api/sensitivity", body),
      api("/api/decompose", {
        ...body,
        cycle_id: Math.min(state.currentCycle, 2),
      }),
      api("/api/compare", { cycle_ids: [0, 1, 2] }),
      api("/api/compare-efficiency", { ...body, cycle_ids: [0, 1, 2], eta_constant: 0.90 }),
    ]);
    state.results = sim.results;
    state.sensitivity = sens;
    state.decompose = decomp;
    state.compare = cmp;
    state.efficiencyComparison = effCmp;

    // 自定义工况存在则一并重算
    if (state.customCycleData) {
      const cr = await api("/api/simulate", { params: body.params, custom_cycle: state.customCycleData });
      state.customResult = cr.results[0];
    }

    renderCards();
    renderMap();
    renderEnergy();
    renderDecomp();
    renderEfficiencyComparison();
    renderTimeseries();
    renderSensitivity();
    renderCompare();
  } catch (e) {
    console.error(e);
    $("api-status").textContent = "仿真失败: " + e.message;
  } finally {
    $("btn-run").disabled = false;
  }
}

/* ---------------- 效率 MAP 与恒定效率对比 ---------------- */
function renderEfficiencyComparison() {
  const d = state.efficiencyComparison;
  if (!d) return;
  const rows = d.results;
  const etaPct = (d.eta_constant * 100).toFixed(0);
  charts.efficiencyCompare.setOption({
    tooltip: {
      trigger: "axis", axisPointer: { type: "shadow" },
      formatter: (ps) => {
        const row = rows[ps[0].dataIndex];
        return `${row.cycle_name}<br>` +
          ps.map((p) => `${p.seriesName}: ${p.value.toFixed(3)} kWh`).join("<br>") +
          `<br>恒定效率相对偏差: ${row.delta_pct >= 0 ? "+" : ""}${row.delta_pct.toFixed(2)}%`;
      },
    },
    legend: { top: 0, data: ["效率 MAP", `恒定效率 ${etaPct}%`] },
    grid: { left: 70, right: 30, top: 46, bottom: 50, containLabel: true },
    xAxis: { type: "category", data: rows.map((r) => r.cycle_name) },
    yAxis: { type: "value", name: "净能耗 (kWh)", nameLocation: "middle", nameGap: 48 },
    series: [
      {
        name: "效率 MAP", type: "bar", data: rows.map((r) => r.E_map_kWh),
        itemStyle: { color: "#0f766e" }, barGap: "12%",
      },
      {
        name: `恒定效率 ${etaPct}%`, type: "bar", data: rows.map((r) => r.E_constant_kWh),
        itemStyle: { color: "#d97706" },
        label: {
          show: true, position: "top", fontSize: 10,
          formatter: (p) => {
            const delta = rows[p.dataIndex].delta_pct;
            return `${delta >= 0 ? "+" : ""}${delta.toFixed(1)}%`;
          },
        },
      },
    ],
  }, true);
}

/* 仅刷新当前选中工况相关图（时程 / MAP / 分解），用于工况切换与自定义工况 */
async function refreshTimeseriesOnly() {
  const isCustom = state.currentCycle === 3;
  const body = { params: collectParams() };
  const decomp = await api("/api/decompose", isCustom
    ? { ...body, custom_cycle: state.customCycleData }
    : { ...body, cycle_id: state.currentCycle });
  state.decompose = decomp;
  renderCards();
  renderMap();
  renderDecomp();
  renderTimeseries();
}

/* ---------------- 概览卡片 ---------------- */
function renderCards() {
  const cards = state.results.map((r, i) => {
    const pos = r.E_total_kWh >= 0;
    const eta = r.eta_avg_drive_weighted;
    return `
      <div class="card ${i === 0 ? "highlight" : ""}">
        <div class="c-name">${CYCLE_NAMES[i]} · 净能耗</div>
        <div class="c-val ${pos ? "pos" : "neg"}">${pos ? "" : "−"}${Math.abs(r.E_total_kWh).toFixed(2)} <small style="font-size:13px">kWh</small></div>
        <div class="c-sub">平均效率：${eta === null ? "—" : (eta * 100).toFixed(1) + "%"}
          <br>驱动 ${r.E_drive_kWh.toFixed(2)} / 回收 ${r.E_regen_kWh.toFixed(2)} kWh
          <br>SOC ${r.soc_delta_pct >= 0 ? "+" : ""}${r.soc_delta_pct.toFixed(2)} pp</div>
      </div>`;
  });
  if (state.customResult) {
    const r = state.customResult;
    const pos = r.E_total_kWh >= 0;
    cards.push(`
      <div class="card highlight">
        <div class="c-name">${state.customCycleData.name} · 净能耗</div>
        <div class="c-val ${pos ? "pos" : "neg"}">${pos ? "" : "−"}${Math.abs(r.E_total_kWh).toFixed(2)} <small style="font-size:13px">kWh</small></div>
        <div class="c-sub">平均效率：${r.eta_avg_drive_weighted === null ? "—" : (r.eta_avg_drive_weighted * 100).toFixed(1) + "%"}
          <br>驱动 ${r.E_drive_kWh.toFixed(2)} / 回收 ${r.E_regen_kWh.toFixed(2)} kWh
          <br>SOC ${r.soc_delta_pct >= 0 ? "+" : ""}${r.soc_delta_pct.toFixed(2)} pp</div>
      </div>`);
  }
  $("cards").innerHTML = cards.join("");
}

/* ---------------- 效率 MAP + 工作点 ---------------- */
function renderMap() {
  const mm = state.motorMap;
  const res = getCurrentResult();
  if (!res) return;

  // 后端为减小响应体做了降采样；坐标轴使用整齐刻度，避免把长小数
  // （例如 6823.529）直接渲染在图表边缘而造成文字重叠。
  const nAxisMax = Math.ceil(mm.n[mm.n.length - 1] / 1000) * 1000;
  const tAxisMax = Math.ceil(mm.T[mm.T.length - 1] / 500) * 500;

  const heatData = [];
  for (let j = 0; j < mm.T.length; j++) {
    for (let i = 0; i < mm.n.length; i++) {
      heatData.push([mm.n[i], mm.T[j], mm.eta[j][i]]);
    }
  }

  const scatter = res.n_motor.map((n, k) => [n, Math.abs(res.T_motor[k])]);
  const extCurve = buildExtCurve(mm);

  charts.map.setOption({
    title: { text: getCurrentCycleName() + " · 工作点", left: 8, top: 2, textStyle: { fontSize: 13, color: "#334155" } },
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
    grid: { left: 82, right: 28, top: 52, bottom: 78 },
    xAxis: {
      type: "value", name: "转速 n (r/min)", nameLocation: "middle", nameGap: 36,
      min: 0, max: nAxisMax, interval: 1000,
      axisLabel: { formatter: (value) => value.toLocaleString() },
    },
    yAxis: {
      type: "value", name: "转矩 T (N·m)", nameLocation: "middle", nameGap: 58,
      min: 0, max: tAxisMax, interval: 500,
      axisLabel: { formatter: (value) => value.toLocaleString() },
    },
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
  charts.energy.setOption({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { top: 0, data: ["驱动能耗", "回收能量"] },
    grid: { left: 64, right: 30, top: 42, bottom: 48, containLabel: true },
    xAxis: { type: "category", data: CYCLE_NAMES },
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
  if (!d) return;
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
    title: { text: getCurrentCycleName(), left: 8, top: 2, textStyle: { fontSize: 13, color: "#334155" } },
    tooltip: {
      trigger: "item",
      formatter: (p) => `${p.name}<br>${p.value} kWh（${p.percent.toFixed(1)}%）`,
    },
    legend: { type: "scroll", orient: "vertical", right: 8, top: "middle", textStyle: { fontSize: 12 } },
    series: [{
      name: "能量去向", type: "pie",
      radius: ["36%", "64%"], center: ["34%", "52%"],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: "#fff", borderWidth: 2 },
      label: { show: false },
      emphasis: { label: { show: true, fontSize: 13, fontWeight: "bold" } },
      data: items.map(([name, value]) => ({ name, value })),
      color: ["#0f766e", "#dc2626", "#d97706", "#7c3aed", "#2563eb", "#0891b2", "#65a30d", "#64748b", "#94a3b8"],
    }],
  }, true);
}

/* ---------------- 时程曲线（含 SOC） ---------------- */
function renderTimeseries() {
  const r = getCurrentResult();
  if (!r) return;
  const t = r.n_motor.map((_, i) => i);
  const socPct = r.soc.map((x) => (x * 100).toFixed(2));

  charts.ts.setOption({
    title: { text: getCurrentCycleName(), left: 8, top: 2, textStyle: { fontSize: 13, color: "#334155" } },
    tooltip: { trigger: "axis" },
    legend: { top: 0, data: ["电机效率", "轮边功率", "累积能耗", "SOC"] },
    grid: { left: 72, right: 168, top: 46, bottom: 54, containLabel: false },
    xAxis: { type: "category", data: t, name: "时间 (s)", nameLocation: "middle", nameGap: 34 },
    yAxis: [
      {
        type: "value", name: "效率 (%)", min: 50, max: 100, position: "left",
        nameLocation: "middle", nameGap: 46,
      },
      {
        type: "value", name: "功率 (kW)", position: "right",
        nameLocation: "middle", nameGap: 42,
      },
      {
        type: "value", name: "能耗 (kWh)", position: "right", offset: 58,
        nameLocation: "middle", nameGap: 42, splitLine: { show: false },
      },
      {
        type: "value", name: "SOC (%)", position: "right", offset: 116, min: 0, max: 100,
        nameLocation: "middle", nameGap: 42, splitLine: { show: false },
      },
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
      {
        name: "SOC", type: "line",
        data: socPct,
        yAxisIndex: 3, symbol: "none",
        lineStyle: { width: 2, color: "#7c3aed", type: "dashed" },
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
    grid: { left: 134, right: 82, top: 44, bottom: 38, containLabel: false },
    xAxis: { type: "value", name: "能耗变化率 (%)", nameLocation: "middle", nameGap: 32 },
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

/* ---------------- 多车型对比 ---------------- */
function renderCompare() {
  const d = state.compare;
  if (!d) return;

  charts.compare.setOption({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: { top: 0, data: d.presets },
    grid: { left: 64, right: 34, top: 42, bottom: 48, containLabel: true },
    xAxis: { type: "category", data: d.cycle_names },
    yAxis: { type: "value", name: "净能耗 (kWh)", nameLocation: "middle", nameGap: 48 },
    series: d.rows.map((row, i) => ({
      name: row.name,
      type: "bar",
      data: row.cells.map((c) => c.E_total_kWh),
      itemStyle: { color: PRESET_COLORS[i % PRESET_COLORS.length] },
      barGap: "10%",
      label: { show: true, position: "top", fontSize: 10, formatter: (p) => p.value.toFixed(1) },
    })),
  }, true);
}

/* ---------------- 启动 ---------------- */
if (typeof echarts === "undefined") {
  $("api-status").textContent = "ECharts 加载失败，请检查网络（CDN）";
  $("api-dot").classList.add("err");
} else {
  init();
}
