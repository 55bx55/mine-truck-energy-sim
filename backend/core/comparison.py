"""效率 MAP 与恒定效率假设的能耗对比。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .battery import battery_efficiency
from .cycles import DriveCycle
from .simulation import SimulationResult
from .vehicle import VehicleParams, calc_dynamics


@dataclass
class EfficiencyComparison:
    """同一工况下，两种电机效率建模方法的能耗结果。"""

    cycle_name: str
    eta_constant: float
    E_map_kWh: float
    E_constant_kWh: float
    delta_kWh: float
    delta_pct: float

    def to_dict(self) -> dict:
        return {
            "cycle_name": self.cycle_name,
            "eta_constant": self.eta_constant,
            "E_map_kWh": round(self.E_map_kWh, 3),
            "E_constant_kWh": round(self.E_constant_kWh, 3),
            "delta_kWh": round(self.delta_kWh, 3),
            "delta_pct": round(self.delta_pct, 2),
        }


def simulate_constant_efficiency(
    cycle: DriveCycle,
    param: VehicleParams,
    eta_constant: float = 0.90,
) -> float:
    """按恒定电机效率计算净电池能耗，返回 Wh。

    动力学、传动效率和 C-rate 电池效率与效率 MAP 仿真保持一致，
    只替换电机效率的取得方式。
    """
    if not 0.0 < eta_constant <= 1.0:
        raise ValueError("恒定电机效率必须位于 (0, 1] 区间")

    _, _, p_wheel, _ = calc_dynamics(
        cycle.v, cycle.a, cycle.grade, cycle.payload, param
    )
    is_drive = p_wheel >= 0
    p_motor_mech = np.where(
        is_drive,
        p_wheel / param.eta_T,
        p_wheel * param.eta_T,
    )

    p_mech_to_batt = p_motor_mech / eta_constant
    eta_batt_dis = battery_efficiency(p_mech_to_batt, "discharge", param)
    p_batt_drive = p_mech_to_batt / eta_batt_dis

    p_regen = p_motor_mech * eta_constant
    eta_batt_chg = battery_efficiency(p_regen, "charge", param)
    p_batt_regen = p_regen * eta_batt_chg
    p_batt = np.where(is_drive, p_batt_drive, p_batt_regen)

    return float(np.trapezoid(p_batt, dx=1.0) / 3600.0)


def compare_efficiency_models(
    cycle: DriveCycle,
    map_result: SimulationResult,
    param: VehicleParams,
    eta_constant: float = 0.90,
) -> EfficiencyComparison:
    """汇总效率 MAP 与恒定效率模型的能耗差异。"""
    e_constant_kwh = simulate_constant_efficiency(cycle, param, eta_constant) / 1000.0
    e_map_kwh = map_result.E_total / 1000.0
    delta_kwh = e_constant_kwh - e_map_kwh
    delta_pct = delta_kwh / e_map_kwh * 100.0 if e_map_kwh else float("nan")
    return EfficiencyComparison(
        cycle_name=cycle.name,
        eta_constant=eta_constant,
        E_map_kWh=e_map_kwh,
        E_constant_kWh=e_constant_kwh,
        delta_kWh=delta_kwh,
        delta_pct=delta_pct,
    )
