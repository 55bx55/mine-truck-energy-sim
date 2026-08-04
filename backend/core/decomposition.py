"""能量损耗分解分析（移植自 MATLAB energy_decomposition07.m）

追踪每个工况中电池能量的去向，分解为：
  - 电机铜损（I²R，与转矩平方相关）
  - 电机铁损（涡流，高速显著）
  - 电机低速附加损耗（反电动势不足）
  - 电机位置偏离损耗（工作点偏离最优效率区）
  - 传动系统损耗
  - 滚动阻力损耗 / 空气阻力损耗
  - 坡度势能变化（上坡消耗，下坡回收，有符号）

电机内部损耗采用"串联级联法"：从机械端反向逐级加回损耗，
各损耗因子的解析式与效率 MAP 生成器（map_generator.py）保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .cycles import DriveCycle
from .efficiency import MotorEfficiencyMap
from .simulation import SimulationResult
from .vehicle import VehicleParams

# 效率 MAP 生成时的损耗系数（与 map_generator.py / generate_motor_map01.m 一致）
_LOSS_PARAMS = {
    "eta_peak": 0.935,
    "n_opt": 3200.0,
    "T_opt": 1200.0,
    "sigma_n_high": 4000.0,
    "sigma_n_low": 3000.0,
    "sigma_T": 1800.0,
    "alpha_cu": 0.04,
    "T_rated": 1500.0,
    "n_crit": 3800.0,
    "n_ultra_low": 250.0,
}


@dataclass
class EnergyBreakdown:
    """单个工况的能量分解结果（kWh）"""

    cycle_name: str
    E_batt_total: float      # 净电池能耗
    E_loss_cu: float         # 电机铜损
    E_loss_fe: float         # 电机铁损
    E_loss_low: float        # 电机低速附加损耗
    E_loss_base: float       # 电机位置偏离损耗
    E_loss_trans: float      # 传动系统损耗
    E_loss_roll: float       # 滚动阻力损耗
    E_loss_air: float        # 空气阻力损耗
    E_grade_change: float    # 坡度势能变化（±）
    E_other: float           # 其他（收支差）

    def to_dict(self) -> dict:
        return {
            "cycle_name": self.cycle_name,
            "E_batt_total": round(self.E_batt_total, 3),
            "电机铜损": round(self.E_loss_cu, 3),
            "电机铁损": round(self.E_loss_fe, 3),
            "低速附加损耗": round(self.E_loss_low, 3),
            "位置偏离损耗": round(self.E_loss_base, 3),
            "传动系统损耗": round(self.E_loss_trans, 3),
            "滚动阻力损耗": round(self.E_loss_roll, 3),
            "空气阻力损耗": round(self.E_loss_air, 3),
            "坡度势能变化": round(self.E_grade_change, 3),
            "其他": round(self.E_other, 3),
        }


def decompose_cycle(
    cycle: DriveCycle,
    res: SimulationResult,
    param: VehicleParams,
) -> EnergyBreakdown:
    """对单个工况做能量分解（向量化）"""
    P = _LOSS_PARAMS
    N = cycle.duration

    v_ms = cycle.v / 3.6
    alpha = np.arctan(cycle.grade / 100.0)
    m_total = param.m_vehicle + cycle.payload

    # ---- (A) 行驶阻力做功 ----
    F_roll = m_total * param.g * param.f_r * np.cos(alpha)
    F_air = 0.5 * param.rho * param.Cd * param.A * v_ms**2
    F_grade = m_total * param.g * np.sin(alpha)

    dE_roll = F_roll * v_ms          # J
    dE_air = F_air * v_ms            # J
    dE_grade = F_grade * v_ms        # J（有符号）

    E_loss_roll = np.sum(np.abs(dE_roll))
    E_loss_air = np.sum(np.abs(dE_air))
    E_grade_change = np.sum(dE_grade)

    # ---- (B) 传动系统损耗 ----
    P_w = res.P_wheel
    P_w_pos = P_w >= 0
    dE_trans = np.where(
        P_w_pos,
        np.abs(P_w) * (1.0 / param.eta_T - 1.0),
        np.abs(P_w) * (1.0 - param.eta_T),
    )
    E_loss_trans = np.sum(dE_trans)

    # ---- (C) 电机损耗分解（串联级联法，仅驱动工况）----
    n_i = res.n_motor
    T_i = res.T_motor
    eta_q = res.eta_inst

    P_motor_mech = np.where(P_w_pos, P_w / param.eta_T, P_w * param.eta_T)
    P_mech_out = np.where(P_w_pos, P_motor_mech, np.abs(P_motor_mech))

    # 损耗因子（与 MAP 生成公式一致）
    dn = n_i - P["n_opt"]
    f_n = np.where(dn >= 0, np.exp(-dn**2 / (2 * P["sigma_n_high"]**2)),
                   np.exp(-dn**2 / (2 * P["sigma_n_low"]**2)))
    f_T = np.exp(-((T_i - P["T_opt"]) ** 2) / (2 * P["sigma_T"] ** 2))
    eta_base = P["eta_peak"] * f_n * f_T

    delta_cu = np.minimum(P["alpha_cu"] * (np.abs(T_i) / P["T_rated"]) ** 2, 0.20)
    delta_fe = np.minimum(0.07 * np.maximum(0.0, (n_i - P["n_crit"]) / 2000.0), 0.10)
    delta_low = 0.08 * np.maximum(0.0, 1.0 - n_i / P["n_ultra_low"])

    # 串联级联：从机械端反向逐级加回损耗
    # 损耗因子上限远小于 1（δ_cu≤0.2, δ_fe≤0.1, δ_low≤0.08），除法安全，
    # 与 MATLAB 分支条件 delta < 0.999 等价。
    act = (P_w_pos) & (P_mech_out > 0)

    P_after_low = np.where(act, P_mech_out / (1.0 - delta_low), 0.0)
    dE_low = np.where(act, P_after_low - P_mech_out, 0.0)

    P_after_fe = np.where(act, P_after_low / (1.0 - delta_fe), 0.0)
    dE_fe = np.where(act, P_after_fe - P_after_low, 0.0)

    P_after_cu = np.where(act, P_after_fe / (1.0 - delta_cu), 0.0)
    dE_cu = np.where(act, P_after_cu - P_after_fe, 0.0)

    # MATLAB 兜底：eta_base ≤ 0.01 时该步损耗记 0
    eta_base_safe = np.where(eta_base > 0.01, eta_base, 1.0)
    P_elec_calc = np.where(act, P_after_cu / eta_base_safe, 0.0)
    dE_base = np.where(act, P_elec_calc - P_after_cu, 0.0)

    # 时间步长 1 s，dE 即 J
    E_loss_cu = np.sum(np.where(act, dE_cu, 0.0))
    E_loss_fe = np.sum(np.where(act, dE_fe, 0.0))
    E_loss_low = np.sum(np.where(act, dE_low, 0.0))
    E_loss_base = np.sum(np.where(act, dE_base, 0.0))

    # ---- 汇总（J → kWh）----
    E_batt_total = res.E_total / 1000.0
    L = lambda x: x / 3600.0 / 1000.0  # noqa: E731

    sums = L(E_loss_cu) + L(E_loss_fe) + L(E_loss_low) + L(E_loss_base) \
        + L(E_loss_trans) + L(E_loss_roll) + L(E_loss_air)
    E_other = max(0.0, abs(E_batt_total) - sums)

    return EnergyBreakdown(
        cycle_name=cycle.name,
        E_batt_total=E_batt_total,
        E_loss_cu=L(E_loss_cu),
        E_loss_fe=L(E_loss_fe),
        E_loss_low=L(E_loss_low),
        E_loss_base=L(E_loss_base),
        E_loss_trans=L(E_loss_trans),
        E_loss_roll=L(E_loss_roll),
        E_loss_air=L(E_loss_air),
        E_grade_change=L(E_grade_change),
        E_other=E_other,
    )
