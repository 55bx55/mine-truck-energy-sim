"""参数敏感性分析（移植自 MATLAB sensitivity_analysis06.m）

对关键参数做单因素摄动（±10%、±20%），考察对工况总能耗的影响：

  参数集合：
    - 载重质量 m_payload（通过修改工况载重序列实现）
    - 滚动阻力系数 f_r
    - 传动效率 eta_T
    - 主减速比 i_total

输出每个参数在 ±20% 摄动下的能耗变化范围（Tornado 图数据）
以及敏感度指标：平均每 1% 参数变化引起的能耗变化百分比。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .cycles import DriveCycle
from .efficiency import MotorEfficiencyMap
from .simulation import simulate_cycle
from .vehicle import VehicleParams


@dataclass
class SensitivityResult:
    """单参数敏感性分析结果"""

    param_key: str
    display_name: str
    base_value: float
    perturbations: list      # 摄动比例列表
    E_values_kWh: list       # 各摄动下的总能耗 (kWh)
    delta_pct: list          # 能耗变化率 (%)
    sensitivity: float       # 每 1% 参数变化 → 能耗变化 %

    def to_dict(self) -> dict:
        return {
            "param_key": self.param_key,
            "display_name": self.display_name,
            "base_value": round(self.base_value, 4),
            "perturbations": self.perturbations,
            "E_values_kWh": [round(v, 3) for v in self.E_values_kWh],
            "delta_pct": [round(v, 3) for v in self.delta_pct],
            "sensitivity": round(self.sensitivity, 4),
        }


# 待分析参数：key / 显示名 / 基准值（与 MATLAB 一致）
PARAMS_TO_TEST = [
    ("m_payload", "载重质量", 60000.0),
    ("f_r", "滚动阻力系数", 0.020),
    ("eta_T", "传动效率", 0.95),
    ("i_total", "主减速比", 45.0),
]

PERTURBATIONS = [-0.20, -0.10, 0.0, 0.10, 0.20]


def run_sensitivity(
    base_cycle: DriveCycle,
    eff_map: MotorEfficiencyMap,
    param: VehicleParams,
) -> list[SensitivityResult]:
    """运行敏感性分析，返回各参数结果列表"""
    results = []

    for key, display_name, base_val in PARAMS_TO_TEST:
        E_values = []
        for pert in PERTURBATIONS:
            new_val = base_val * (1.0 + pert)

            if key == "m_payload":
                # 通过修改工况载重序列实现
                cycle_mod = DriveCycle(
                    name=base_cycle.name,
                    t=base_cycle.t.copy(),
                    v=base_cycle.v.copy(),
                    a=base_cycle.a.copy(),
                    grade=base_cycle.grade.copy(),
                    payload=base_cycle.payload * (1.0 + pert),
                )
                param_mod = param
            else:
                cycle_mod = base_cycle
                param_mod = VehicleParams.from_dict(param.to_dict())
                setattr(param_mod, key, new_val)

            res = simulate_cycle(cycle_mod, eff_map, param_mod)
            E_values.append(res.E_total / 1000.0)  # kWh

        E_values = np.array(E_values)
        E_base = E_values[PERTURBATIONS.index(0.0)]
        delta_pct = (E_values - E_base) / E_base * 100.0

        non_zero = np.abs(np.array(PERTURBATIONS)) > 1e-9
        sensitivity = float(np.mean(np.abs(delta_pct[non_zero])) / 20.0)

        results.append(SensitivityResult(
            param_key=key,
            display_name=display_name,
            base_value=base_val,
            perturbations=list(PERTURBATIONS),
            E_values_kWh=E_values.tolist(),
            delta_pct=delta_pct.tolist(),
            sensitivity=sensitivity,
        ))

    return results


def tornado_data(results: list[SensitivityResult]) -> dict:
    """构造 Tornado 图数据：按 ±20% 影响范围降序排列"""
    rows = []
    for r in results:
        d = r.delta_pct
        rows.append({
            "display_name": r.display_name,
            "param_key": r.param_key,
            "base": r.E_values_kWh[2],
            "minus20": d[0],   # -20% 摄动能耗变化率
            "plus20": d[4],    # +20% 摄动能耗变化率
            "range": abs(d[4] - d[0]),
            "sensitivity": r.sensitivity,
        })
    rows.sort(key=lambda x: x["range"], reverse=True)
    return {"rows": rows}
