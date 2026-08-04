# 电动矿卡能耗预测与仿真分析系统

**Efficiency-Map Based Energy Consumption Simulation for Electric Mining Trucks**

一个面向 60 吨级电动矿卡的能耗预测与仿真分析平台：基于电机效率 MAP 查表与纵向动力学耦合模型，对典型矿山工况逐秒求解能耗，并提供效率 Map 可视化、能量损耗分解、参数敏感性分析与交互式仿真。

算法核心完整移植自毕业设计的 MATLAB 仿真程序，并经过**逐点一致性对账**（浮点精度内一致，见 [验证](#验证)）。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009688)
![ECharts](https://img.shields.io/badge/ECharts-5-AA344D)
![tests](https://img.shields.io/badge/tests-29%20passed-brightgreen)

---

![系统界面](docs/screenshot.jpg)

---

## 特性

- **效率-动力学耦合仿真引擎**：纵向动力学平衡 → 电机效率 MAP 查表（含高速弱磁/低速/过载五类边界处理）→ C-rate 相关电池效率 → 梯形积分累积能耗，全程向量化，一次工况仿真毫秒级完成
- **SOC 状态跟踪**：电池端能量积分得到 SOC 时程曲线，支持能量回收工况下 SOC 回升的刻画
- **交互式参数面板**：修改整备质量、传动比、阻力系数等 10 项车辆参数 + 初始 SOC，一键重算并联动刷新全部图表；内置 60/90/110 吨级车型预设
- **自定义工况编辑器**：梯形速度曲线（加速-匀速-减速）+ 坡度 + 载重参数化生成任意工况，一键仿真并接入全部联动视图
- **多车型对比**：同工况下 60/90/110 吨级车型的净能耗与回收能量横向对比
- **五类可视化**：效率 MAP 热力图叠加工作点轨迹、三工况能耗构成对比、效率/功率/能耗/SOC 时程曲线、能量损耗分解环形图、参数敏感性 Tornado 图
- **能量损耗分解**：将电池能量去向分解为电机铜损、铁损、低速损耗、位置偏离损耗、传动损耗与行驶阻力损耗（串联级联法）
- **参数敏感性分析**：对载重、滚动阻力、传动效率、主减速比做 ±20% 单因素摄动，量化各参数对能耗的影响权重
- **数据自包含**：效率 MAP 与工况数据均由程序生成，无需 MATLAB 及原始数据文件，clone 即用

## 架构

```
┌──────────────────────────────────────────────────────┐
│  Frontend (纯静态, ECharts 5)                        │
│  参数面板 · 效率Map热力图 · 能耗对比 · 时程 · Tornado  │
└────────────────────────┬─────────────────────────────┘
                         │ REST API (JSON)
┌────────────────────────▼─────────────────────────────┐
│  FastAPI 后端                                        │
│  /api/simulate  /api/sensitivity  /api/decompose     │
│  /api/motor-map /api/cycles /api/meta                │
└────────────────────────┬─────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────┐
│  仿真引擎 core/                                      │
│  vehicle.py     纵向动力学（滚动/空气/坡度/加速阻力） │
│  efficiency.py  电机效率MAP查表（5类边界处理）        │
│  battery.py     C-rate相关电池充放电效率              │
│  simulation.py  逐秒能耗积分仿真（向量化）            │
│  map_generator.py  乘法损耗模型效率曲面生成           │
│  decomposition.py  能量损耗分解（串联级联）           │
│  sensitivity.py  单因素摄动敏感性分析                │
└──────────────────────────────────────────────────────┘
```

## 算法模型

### 纵向动力学

$$F_{total} = \underbrace{m g f_r \cos\alpha}_{滚动阻力} + \underbrace{\tfrac{1}{2}\rho C_d A v^2}_{空气阻力} + \underbrace{m g \sin\alpha}_{坡度阻力} + \underbrace{\delta m a}_{加速阻力}$$

轮边需求功率 $P_{wheel} = F_{total} \cdot v$，电机转速 $n = \dfrac{v \cdot i_{total} \cdot 60}{2\pi r_w}$。

### 电机效率 MAP（乘法损耗模型）

$$\eta = \eta_{peak} \cdot f_n(n) \cdot f_T(T) \cdot (1-\delta_{cu}) \cdot (1-\delta_{fe}) \cdot (1-\delta_{low})$$

其中 $f_n, f_T$ 为转速/转矩方向效率因子（不对称高斯），$\delta_{cu}$ 为铜损（与转矩平方成正比）、$\delta_{fe}$ 为铁损（高速显著）、$\delta_{low}$ 为低速附加损耗。模型峰值效率因子 93.5%（网格离散采样后实际峰值 91.4%），高效区（≥90%）平台约 2000–4000 r/min × 800–1600 N·m。

### 电池效率（C-rate 模型）

$$\eta_{dis} = 0.96 - 0.02 \cdot \min(C_{rate}, 3), \qquad \eta_{chg} = 0.96 - 0.03 \cdot \min(C_{rate}, 2)$$

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 生成数据（首次运行；已提交的 data/ 可直接使用）
python scripts/prepare_data.py

# 3. 启动服务
uvicorn backend.app:app --reload

# 4. 浏览器打开
#    http://127.0.0.1:8000
```

运行测试：

```bash
python -m pytest tests/ -v
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/meta` | 默认车辆参数、车型预设、工况列表、MAP 峰值 |
| GET | `/api/motor-map` | 效率 MAP 网格数据（可降采样） |
| GET | `/api/cycles` | 三工况时间序列 |
| POST | `/api/simulate` | 运行能耗仿真，body: `{params?, cycle_ids?, custom_cycle?}` |
| POST | `/api/sensitivity` | 参数敏感性分析 |
| POST | `/api/decompose` | 能量损耗分解（支持 cycle_id 或 custom_cycle） |
| POST | `/api/compare` | 多车型同工况能耗对比 |
| GET | `/docs` | 交互式 API 文档（FastAPI 自动生成） |

自定义工况仿真示例：

```bash
curl -X POST http://127.0.0.1:8000/api/simulate \
  -H "Content-Type: application/json" \
  -d '{"custom_cycle": {"v": [0, 5, 10, 10, 5, 0], "grade": [6, 6, 6, 6, 6, 6], "payload": [60000, 60000, 60000, 60000, 60000, 60000]}}'
```

## 验证

Python 移植版与 MATLAB 原始程序（毕设 `main_simulation04.m` 等）逐项对账，**全部在浮点精度内一致**：

| 项目 | 工况 | MATLAB 基准 | Python 移植 | 最大误差 |
|------|------|------------|------------|---------|
| 总能耗 E_total | 重载上坡 | 27361.5452 Wh | 27361.5452 Wh | < 1e-6 |
| 总能耗 E_total | 平路满载 | 7469.4328 Wh | 7469.4328 Wh | < 1e-6 |
| 总能耗 E_total | 长下坡回收 | -4238.7208 Wh | -4238.7208 Wh | < 1e-6 |
| 效率 MAP 曲面 | 全域 100×120 | — | — | 4.4e-16 |
| 逐点序列 n/T/η/P | 三工况 | — | — | ≤ 2.9e-10 |

对账脚本：`scripts/reconcile_matlab.py`（需原始 `.mat` 数据，仅作开发验证用）。

### 典型仿真结果（默认参数，60 吨级）

| 工况 | 净能耗 | 驱动能耗 | 回收能量 | 驱动段平均效率（功率加权） | SOC 变化 |
|------|--------|---------|---------|--------------------------|---------|
| 重载上坡（8%坡，满载60t） | 27.36 kWh | 27.36 kWh | 0 | 69.7% | -9.12 pp |
| 平路满载（20km/h） | 7.47 kWh | 7.47 kWh | 0 | 81.9% | -2.49 pp |
| 长下坡回收（-7%坡，空载） | -4.24 kWh | 0 | 4.24 kWh | — | +1.41 pp |

### 多车型对比（同工况）

| 车型 | 重载上坡 | 平路满载 | 长下坡回收 |
|------|---------|---------|-----------|
| 60 吨级（整备 30t / 300 kWh） | 27.36 kWh | 7.47 kWh | -4.24 kWh |
| 90 吨级（整备 42t / 420 kWh） | 33.14 kWh | 8.43 kWh | -6.19 kWh |
| 110 吨级（整备 52t / 520 kWh） | 36.30 kWh | 9.34 kWh | -7.82 kWh |

## 项目结构

```
mine-truck-energy-sim/
├── backend/
│   ├── app.py                # FastAPI 入口与路由
│   └── core/                 # 仿真引擎核心算法
│       ├── vehicle.py        # 车辆动力学 + 车型预设
│       ├── efficiency.py     # 效率MAP查表
│       ├── battery.py        # 电池效率模型
│       ├── simulation.py     # 能耗仿真引擎（含 SOC 跟踪）
│       ├── map_generator.py  # 效率曲面生成
│       ├── decomposition.py  # 能量分解
│       ├── sensitivity.py    # 敏感性分析
│       └── cycles.py         # 典型矿山工况 + 自定义工况构造
├── frontend/                 # 纯静态前端（ECharts 5）
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── docs/                     # 截图等文档素材
├── data/                     # 程序生成的数据（已提交）
├── tests/                    # 单元测试与 API 集成测试（29 项）
├── scripts/                  # 数据准备 / 对账 / 冒烟测试
├── requirements.txt
└── LICENSE
```

## 背景

本项目源于本科毕业设计《电动矿卡能耗预测》的工程化重构：将 MATLAB 仿真程序完整移植为 Python 工程，补齐数据自包含、可视化与交互能力，并建立与 MATLAB 基准的一致性对账体系。适用于新能源矿用车整车能耗仿真、动力系统匹配与运营节能分析等场景。

## License

[MIT](LICENSE)
