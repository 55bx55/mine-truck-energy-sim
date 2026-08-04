"""效率-动力学耦合能耗仿真引擎（移植自 MATLAB main_simulation04.m）

对给定工况逐秒求解：

    车速/坡度/载重
        → 纵向动力学平衡 → 轮边需求功率 P_wheel + 电机工作点 (n, T)
        → 电机效率 MAP 查表 → 电机输入电功率
        → C-rate 相关电池效率 → 电池端功率 P_batt
        → 梯形积分累积电池能耗 E_cum / E_total

同时输出驱动段平均效率（算术平均与功率加权两种口径）。
全程向量化，一次工况仿真在毫秒级完成。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .battery import battery_efficiency
from .cycles import DriveCycle
from .efficiency import MotorEfficiencyMap
from .vehicle import VehicleParams, calc_dynamics


@dataclass
class SimulationResult:
    """单个工况的仿真结果（与 MATLAB results 结构体字段一致）"""

    cycle_name: str
    n_motor: np.ndarray      # 电机转速 (r/min)
    T_motor: np.ndarray      # 电机转矩 (N·m)
    eta_inst: np.ndarray     # 电机瞬时效率
    eta_batt_inst: np.ndarray  # 电池瞬时效率
    P_wheel: np.ndarray      # 轮边功率 (W)
    P_batt: np.ndarray       # 电池端功率 (W)
    E_cum: np.ndarray        # 累积能耗 (Wh)
    E_total: float           # 工况总能耗 (Wh)
    E_drive_kWh: float       # 驱动能耗 (kWh)
    E_regen_kWh: float       # 回收能量 (kWh)
    eta_avg_drive_arith: float     # 驱动段平均效率（算术平均）
    eta_avg_drive_weighted: float  # 驱动段平均效率（功率加权）

    def to_dict(self) -> dict:
        def _f(x):
            return None if (x is None or (isinstance(x, float) and x != x)) else float(x)

        return {
            "cycle_name": self.cycle_name,
            "n_motor": self.n_motor.tolist(),
            "T_motor": self.T_motor.tolist(),
            "eta_inst": self.eta_inst.tolist(),
            "eta_batt_inst": self.eta_batt_inst.tolist(),
            "P_wheel": self.P_wheel.tolist(),
            "P_batt": self.P_batt.tolist(),
            "E_cum": self.E_cum.tolist(),
            "E_total": float(self.E_total),
            "E_total_kWh": round(float(self.E_total) / 1000.0, 3),
            "E_drive_kWh": round(self.E_drive_kWh, 3),
            "E_regen_kWh": round(self.E_regen_kWh, 3),
            "eta_avg_drive_arith": _f(self.eta_avg_drive_arith),
            "eta_avg_drive_weighted": _f(self.eta_avg_drive_weighted),
        }


def simulate_cycle(
    cycle: DriveCycle,
    eff_map: MotorEfficiencyMap,
    param: VehicleParams,
) -> SimulationResult:
    """对单个工况执行能耗仿真（向量化实现）"""
    N = cycle.duration

    # ---- 1. 动力学计算（向量化）----
    n_motor, T_motor, P_wheel, _ = calc_dynamics(
        cycle.v, cycle.a, cycle.grade, cycle.payload, param
    )

    # ---- 2. 电机效率查表（向量化）----
    eta_inst = eff_map.lookup(n_motor, T_motor)

    # ---- 3. 电池端功率（含 C-rate 相关电池效率）----
    P_wheel_pos = P_wheel >= 0
    P_batt = np.zeros(N)

    P_motor_mech = np.where(P_wheel_pos, P_wheel / param.eta_T, P_wheel * param.eta_T)

    # 驱动工况：电机消耗电功率 = 机械功率 / 电机效率 / 电池效率
    P_mech_to_batt = P_motor_mech / eta_inst
    eta_batt_dis = battery_efficiency(P_mech_to_batt, "discharge", param)
    P_batt_drive = P_mech_to_batt / eta_batt_dis

    # 制动工况：回收功率 = 机械功率 × 电机效率 × 电池效率
    P_regen = P_motor_mech * eta_inst
    eta_batt_chg = battery_efficiency(P_regen, "charge", param)
    P_batt_regen = P_regen * eta_batt_chg

    P_batt = np.where(P_wheel_pos, P_batt_drive, P_batt_regen)
    eta_batt_inst = np.where(P_wheel_pos, eta_batt_dis, eta_batt_chg)

    # ---- 4. 梯形积分累积能耗 (Wh) ----
    E_total_Wh = float(np.sum((P_batt[1:] + P_batt[:-1]) / 2.0 * 1.0 / 3600.0))
    E_cum = np.concatenate(
        [[0.0], np.cumsum((P_batt[1:] + P_batt[:-1]) / 2.0 / 3600.0)]
    )

    # ---- 5. 驱动段平均效率（两种口径）----
    idx_drive = P_wheel > 0
    if idx_drive.any():
        eta_arith = float(np.mean(eta_inst[idx_drive]))
        eta_wtd = float(np.sum(eta_inst[idx_drive] * P_wheel[idx_drive]) / np.sum(P_wheel[idx_drive]))
    else:
        eta_arith = float("nan")
        eta_wtd = float("nan")

    # ---- 6. 驱动/回收能量拆分（kWh）----
    E_drive = float(np.sum(P_batt[P_batt > 0]) / 3600.0 / 1000.0)
    E_regen = float(np.sum(np.abs(P_batt[P_batt < 0])) / 3600.0 / 1000.0)

    return SimulationResult(
        cycle_name=cycle.name,
        n_motor=n_motor,
        T_motor=T_motor,
        eta_inst=eta_inst,
        eta_batt_inst=eta_batt_inst,
        P_wheel=P_wheel,
        P_batt=P_batt,
        E_cum=E_cum,
        E_total=E_total_Wh,
        E_drive_kWh=E_drive,
        E_regen_kWh=E_regen,
        eta_avg_drive_arith=eta_arith,
        eta_avg_drive_weighted=eta_wtd,
    )


def simulate_all(
    cycles: list[DriveCycle],
    eff_map: MotorEfficiencyMap,
    param: VehicleParams,
) -> list[SimulationResult]:
    """批量仿真多个工况"""
    return [simulate_cycle(c, eff_map, param) for c in cycles]
