"""电池充放电效率模型（移植自 MATLAB battery_efficiency.m）

C-rate 相关效率模型：电池内阻损耗随电流（功率）增大而增大。
  - 放电: η = η_base - 0.02 * min(C_rate, 3)
  - 充电: η = η_base - 0.03 * min(C_rate, 2)   （充电损耗通常更大）
  - 极低功率（<100 W，接近开路）: η = 0.995
  - 物理约束 clamp [0.85, 0.995]
"""

from __future__ import annotations

import numpy as np


def battery_efficiency(P_batt, mode: str, param):
    """计算电池端效率。

    参数
    ----
    P_batt : 电池端功率 (W)，正=放电需求，负=充电（回收）
    mode   : 'discharge' 或 'charge'
    param  : 含 batt_capacity_kWh / eta_dis / eta_chg 的参数对象

    返回
    ----
    eta_batt : 电池效率 [0.85, 0.995]
    """
    P_abs = np.abs(np.asarray(P_batt, dtype=np.float64))

    base = param.eta_dis if mode == "discharge" else param.eta_chg

    C_rate = P_abs / (param.batt_capacity_kWh * 1000.0)

    if mode == "discharge":
        eta = base - 0.02 * np.minimum(C_rate, 3.0)
    elif mode == "charge":
        eta = base - 0.03 * np.minimum(C_rate, 2.0)
    else:
        raise ValueError("mode 必须是 'discharge' 或 'charge'")

    eta = np.where(P_abs < 100.0, 0.995, eta)
    return np.clip(eta, 0.85, 0.995)
