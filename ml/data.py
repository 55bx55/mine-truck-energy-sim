"""工况采样与特征工程：为数据驱动代理模型生成训练/评测数据。

设计要点
--------
1. 以效率 MAP 机理模型为"教师"(teacher)，随机采样工况后逐条仿真得到净能耗标签；
2. 特征全部可由工况序列 + 整车参数直接算出，不引入机理模型的效率查表结果，避免标签泄漏；
3. 建模目标是"代理模型"(surrogate)：用数据驱动方式逼近机理模型的输出，
   服务于需要海量工况评估的调度/优化场景；
4. 额外构造一个分布外(OOD)测试集，用于分析代理模型的泛化边界。
"""

from __future__ import annotations

import numpy as np

from backend.core.cycles import DriveCycle, smoothdata_gaussian
from backend.core.efficiency import MotorEfficiencyMap
from backend.core.simulation import simulate_cycle
from backend.core.vehicle import VehicleParams


# ----------------------------------------------------------------------
# 恒效率 90% 对照模型（论文第 4 章基线）
# ----------------------------------------------------------------------
class ConstantEfficiencyMap:
    """把所有工作点的电机效率强制为常数的假 MAP，用于复现论文的恒定效率基线"""

    def __init__(self, eta: float = 0.90):
        self.eta_const = float(eta)

    def lookup(self, n_motor, T_motor):
        n = np.asarray(n_motor, dtype=np.float64)
        scalar = n.ndim == 0
        n_flat = np.atleast_1d(n)
        out = np.full_like(n_flat, self.eta_const, dtype=np.float64)
        return out[0] if scalar else out


# ----------------------------------------------------------------------
# 工况采样
# ----------------------------------------------------------------------
REGIMES = ("uphill", "flat", "downhill")
REGIME_CN = {"uphill": "重载上坡", "flat": "平路", "downhill": "长下坡"}


def sample_cycle(rng: np.random.Generator, idx: int, regime: str | None = None,
                 payload_range: tuple[float, float] = (0.0, 60000.0),
                 eta_T_range: tuple[float, float] = (0.88, 0.97),
                 grade_range: tuple[float, float] | None = None,
                 v_range: tuple[float, float] = (5.0, 30.0),
                 duration_range: tuple[int, int] = (120, 400)) -> tuple[DriveCycle, VehicleParams, dict]:
    """随机采样一条梯形速度工况 + 整车参数，返回 (工况, 参数, 元信息)"""
    regime = regime or str(rng.choice(REGIMES))

    # ---- 时间结构：加速 / 匀速 / 减速 ----
    duration = int(rng.integers(duration_range[0], duration_range[1] + 1))
    t1 = max(8, int(duration * rng.uniform(0.12, 0.25)))
    t3 = max(8, int(duration * rng.uniform(0.12, 0.25)))
    t2 = duration - t1 - t3
    if t2 < 10:
        t2 = 10
        duration = t1 + t2 + t3

    v_cruise = float(rng.uniform(*v_range))
    v = np.concatenate([
        np.linspace(0.0, v_cruise, t1),
        np.full(t2, v_cruise),
        np.linspace(v_cruise, 0.0, t3),
    ])

    # ---- 坡度：按工况类型取基准值，叠加深浅不一的缓变 ----
    if grade_range is not None:
        g0 = float(rng.uniform(*grade_range))
    elif regime == "uphill":
        g0 = float(rng.uniform(3.0, 10.0))
    elif regime == "flat":
        g0 = float(rng.uniform(-1.0, 1.0))
    else:
        g0 = float(rng.uniform(-10.0, -3.0))
    wave = rng.uniform(0.0, 1.5) * np.sin(2.0 * np.pi * rng.uniform(0.5, 2.0) * np.arange(len(v)) / len(v))
    grade = np.clip(g0 + wave, -12.0, 12.0)

    payload = float(rng.uniform(*payload_range))
    payload_arr = np.full(len(v), payload)

    t = np.arange(len(v), dtype=np.float64)
    a = np.concatenate([[0.0], np.diff(v) / 3.6])
    a = smoothdata_gaussian(a, window=3)
    cycle = DriveCycle(name=f"c{idx:05d}", t=t, v=v, a=a, grade=grade, payload=payload_arr)

    # ---- 整车参数摄动 ----
    p = VehicleParams()
    preset = VehicleParams.PRESETS[int(rng.integers(0, len(VehicleParams.PRESETS)))]
    p.m_vehicle = float(preset["params"]["m_vehicle"])
    p.batt_capacity_kWh = float(preset["params"]["batt_capacity_kWh"])
    p.i_total = float(preset["params"]["i_total"])
    p.eta_T = float(rng.uniform(*eta_T_range))
    p.f_r = float(rng.uniform(0.015, 0.030))

    meta = {
        "regime": regime,
        "v_cruise": v_cruise,
        "grade_base": g0,
        "duration": len(v),
        "payload": payload,
    }
    return cycle, p, meta


# ----------------------------------------------------------------------
# 特征工程
# ----------------------------------------------------------------------
FEATURE_NAMES = [
    "duration_s", "distance_km", "v_mean", "v_max", "v_std",
    "grade_mean", "grade_max", "grade_min", "grade_pos_frac", "grade_neg_frac",
    "payload_mean", "payload_max", "m_total_mean",
    "a_abs_mean", "a_pos_sum", "a_neg_sum",
    "delta_h_m", "drive_time_frac",
    "P_wheel_avg_drive_kw", "P_wheel_max_kw", "P_wheel_avg_brake_kw",
    "P_wheel_energy_drive_kwh", "P_wheel_energy_brake_kwh",
    "eta_T", "f_r", "i_total",
]


def extract_features(cycle: DriveCycle, p: VehicleParams) -> np.ndarray:
    """从工况 + 整车参数提取特征（不含效率查表结果，避免标签泄漏）"""
    from backend.core.vehicle import calc_dynamics

    v, a, grade, payload = cycle.v, cycle.a, cycle.grade, cycle.payload
    n = len(v)

    _, _, P_wheel, _ = calc_dynamics(v, a, grade, payload, p)
    P_kw = P_wheel / 1000.0

    drive = P_wheel > 0
    brake = P_wheel < 0

    v_ms = v / 3.6
    alpha = np.arctan(grade / 100.0)
    delta_h = float(np.trapezoid(v_ms * np.sin(alpha), cycle.t))

    m_total = p.m_vehicle + payload

    feats = [
        float(n),
        float(cycle.distance_km),
        float(v.mean()), float(v.max()), float(v.std()),
        float(grade.mean()), float(grade.max()), float(grade.min()),
        float(np.mean(grade > 0.5)), float(np.mean(grade < -0.5)),
        float(payload.mean()), float(payload.max()), float(m_total.mean()),
        float(np.abs(a).mean()), float(a[a > 0].sum()), float(-a[a < 0].sum()),
        delta_h,
        float(drive.mean()),
        float(P_kw[drive].mean()) if drive.any() else 0.0,
        float(P_kw.max()),
        float(np.abs(P_kw[brake]).mean()) if brake.any() else 0.0,
        float(P_kw[drive].sum() / 3600.0) if drive.any() else 0.0,
        float(np.abs(P_kw[brake]).sum() / 3600.0) if brake.any() else 0.0,
        float(p.eta_T), float(p.f_r), float(p.i_total),
    ]
    return np.asarray(feats, dtype=np.float64)


# ----------------------------------------------------------------------
# 数据集构建
# ----------------------------------------------------------------------
def build_dataset(n_samples: int, eff_map: MotorEfficiencyMap, seed: int = 42,
                  ood: bool = False) -> dict:
    """生成数据集。

    ood=True 时构造分布外样本：满载下坡、空载上坡、极端传动效率，
    用于分析代理模型在训练分布之外的误差膨胀。
    """
    rng = np.random.default_rng(seed + (10_000 if ood else 0))

    X, y, y_const, meta = [], [], [], []
    const_map = ConstantEfficiencyMap(0.90)

    for i in range(n_samples):
        if ood:
            # 刻意超出训练分布的外推压力测试：坡度、载重、车速、传动效率、时长全部越界
            regime = REGIMES[i % 3]
            if regime == "uphill":
                payload_range = (0.0, 12000.0)
                grade_range = (12.0, 18.0)       # 训练集上坡上限 10%
            elif regime == "downhill":
                payload_range = (75000.0, 95000.0)  # 训练集载重上限 60 t
                grade_range = (-18.0, -12.0)
            else:
                payload_range = (15000.0, 25000.0)
                grade_range = (2.5, 3.5)
            cycle, p, m = sample_cycle(
                rng, i, regime=regime,
                payload_range=payload_range,
                eta_T_range=(0.80, 0.99),        # 训练集 0.88~0.97
                grade_range=grade_range,
                v_range=(32.0, 45.0),            # 训练集 5~30 km/h
                duration_range=(420, 700),       # 训练集 120~400 s
            )
        else:
            cycle, p, m = sample_cycle(rng, i)

        res_map = simulate_cycle(cycle, eff_map, p)     # 教师：效率 MAP 机理模型
        res_const = simulate_cycle(cycle, const_map, p)  # 基线：恒定效率 90%

        X.append(extract_features(cycle, p))
        y.append(res_map.E_total / 1000.0)               # 目标：净能耗 (kWh)
        y_const.append(res_const.E_total / 1000.0)
        meta.append(m)

    return {
        "X": np.vstack(X),
        "y": np.asarray(y, dtype=np.float64),
        "y_const": np.asarray(y_const, dtype=np.float64),
        "meta": meta,
    }
