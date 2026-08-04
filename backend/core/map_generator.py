"""电机效率 MAP 生成器（移植自 MATLAB generate_motor_map01.m）

基于课题组实验室实测电机 Map 图的特征分布，构造物理损耗框架的效率曲面：

    eta = eta_peak * f_n(n) * f_T(T) * (1-δ_cu) * (1-δ_fe) * (1-δ_low)

  - f_n     : 转速方向效率因子（不对称高斯，低速侧衰减快）
  - f_T     : 转矩方向效率因子（宽平台高斯）
  - δ_cu    : 铜损（I²R，与转矩平方成正比），限幅 20%
  - δ_fe    : 铁损/弱磁损耗（高速涡流），限幅 10%
  - δ_low   : 极低转速附加损耗（反电动势不足），限幅 8%
  - 外特性边界外额外惩罚（过载区效率进一步下降）
  - 物理约束 clamp [0.65, 0.95]，零转速零转矩点置 0

峰值效率 93.5%，高效区（≥90%）平台约 2000-4000 r/min × 800-1600 N·m。
"""

from __future__ import annotations

import numpy as np

from .efficiency import MotorEfficiencyMap


def generate_motor_map(
    n_max: float = 7000.0,
    n_points: int = 120,
    T_max: float = 3500.0,
    T_points: int = 100,
    seed: int | None = None,
) -> MotorEfficiencyMap:
    """生成电机效率 MAP。

    参数
    ----
    n_max, n_points : 转速网格上限与点数（r/min）
    T_max, T_points : 转矩网格上限与点数（N·m）
    seed            : 保留参数（确定性生成，无需随机）
    """
    n = np.linspace(0.0, n_max, n_points)
    T_vec = np.linspace(0.0, T_max, T_points)

    N_grid, T_grid = np.meshgrid(n, T_vec)

    # ---- 外特性曲线（基速 2000 r/min，峰值转矩 3000 N·m，恒功率区）----
    n_base = 2000.0
    T_max_base = 3000.0
    P_max = T_max_base * n_base / 9550.0  # kW

    T_limit = np.where(
        n <= n_base,
        T_max_base,
        P_max * 9550.0 / np.maximum(n, 1e-9),
    )

    # ---- 损耗模型参数（与毕设代码一致）----
    eta_peak = 0.935
    n_opt = 3200.0
    T_opt = 1200.0
    sigma_n_high = 4000.0
    sigma_n_low = 3000.0
    sigma_T = 1800.0
    alpha_cu = 0.04
    T_rated = 1500.0
    n_crit = 3800.0
    n_ultra_low = 250.0

    # (a) 转速方向效率因子（不对称高斯）
    dn = N_grid - n_opt
    f_n = np.where(
        dn >= 0,
        np.exp(-dn**2 / (2 * sigma_n_high**2)),
        np.exp(-dn**2 / (2 * sigma_n_low**2)),
    )

    # (b) 转矩方向效率因子（宽平台高斯）
    f_T = np.exp(-((T_grid - T_opt) ** 2) / (2 * sigma_T**2))

    # (c) 铜损衰减因子（I²R）
    delta_cu = np.minimum(alpha_cu * (T_grid / T_rated) ** 2, 0.20)

    # (d) 铁损/弱磁衰减因子
    delta_fe = np.minimum(0.07 * np.maximum(0.0, (N_grid - n_crit) / 2000.0), 0.10)

    # (e) 极低转速衰减因子
    delta_low = 0.08 * np.maximum(0.0, 1.0 - N_grid / n_ultra_low)

    # (f) 合成效率（乘法损耗模型）
    eta_raw = (
        eta_peak * f_n * f_T
        * (1 - delta_cu) * (1 - delta_fe) * (1 - delta_low)
    )

    # 零转速零转矩点置 0（物理无意义）
    eta_raw[(N_grid < 50) & (T_grid < 50)] = 0.0

    # 外特性边界外惩罚（过载区）
    exceed = np.maximum(0.0, T_grid - T_limit) / np.maximum(T_limit, 1e-9)
    penalty = np.minimum(0.12, exceed * 0.15)
    eta_raw = np.where(T_grid > T_limit, eta_raw * (1 - penalty), eta_raw)

    # 物理约束
    eta = np.clip(eta_raw, 0.65, 0.95)
    eta[eta_raw <= 0] = 0.0

    return MotorEfficiencyMap(n, T_vec, eta)
