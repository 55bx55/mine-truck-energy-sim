"""车辆动力学模型（移植自 MATLAB calc_dynamics.m）

计算给定车速/加速度/坡度/载重下的：
  - 电机转速 n_motor (r/min)
  - 电机需求转矩 T_motor (N·m)
  - 轮边需求功率 P_wheel (W)
  - 是否触及外特性限制 flag_limit

数学链路（纵向动力学平衡方程）:
    F_total = F_roll + F_air + F_grade + F_acc
    P_wheel = F_total * v_ms

支持标量与向量两种输入（np.float64 或 np.ndarray）。
"""

from __future__ import annotations

import numpy as np


class VehicleParams:
    """整车参数（60 吨级电动矿卡典型值，与毕设论文一致）"""

    def __init__(self) -> None:
        self.m_vehicle = 30000.0      # 整车整备质量 (kg)
        self.g = 9.8                   # 重力加速度 (m/s^2)
        self.f_r = 0.020               # 滚动阻力系数（矿用路面）
        self.rho = 1.225               # 空气密度 (kg/m^3)
        self.Cd = 0.8                  # 空气阻力系数
        self.A = 12.0                  # 迎风面积 (m^2)
        self.delta = 1.04              # 旋转质量换算系数
        self.r_wh = 0.85               # 车轮滚动半径 (m)
        self.i_total = 45.0            # 主减速比（总传动比）
        self.eta_T = 0.95              # 传动系统效率
        self.eta_dis = 0.96            # 电池放电基准效率
        self.eta_chg = 0.96            # 电池充电基准效率
        self.batt_capacity_kWh = 300.0  # 电池额定容量 (kWh)

        # 电机外特性（基速 2000 r/min，峰值转矩 3000 N·m）
        self.n_vec = np.array([0, 500, 1000, 1500, 2000, 3000, 4000, 5000, 6000])
        self.T_max_vec = np.array([3000, 3000, 3000, 3000, 3000, 2000, 1500, 1200, 1000])

    def to_dict(self) -> dict:
        return {
            "m_vehicle": self.m_vehicle,
            "g": self.g,
            "f_r": self.f_r,
            "rho": self.rho,
            "Cd": self.Cd,
            "A": self.A,
            "delta": self.delta,
            "r_wh": self.r_wh,
            "i_total": self.i_total,
            "eta_T": self.eta_T,
            "eta_dis": self.eta_dis,
            "eta_chg": self.eta_chg,
            "batt_capacity_kWh": self.batt_capacity_kWh,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VehicleParams":
        p = cls()
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, float(v))
        return p


def _t_max_curve(n_motor, n_vec, T_max_vec):
    """外特性限制转矩曲线（分段线性插值，超出转速范围返回 0，与 MATLAB interp1(...,'linear',0) 一致）"""
    n_motor = np.asarray(n_motor, dtype=np.float64)
    scalar = n_motor.ndim == 0
    n_flat = np.atleast_1d(n_motor)

    out = np.zeros_like(n_flat)
    n_lo, n_hi = n_vec[0], n_vec[-1]
    mask = (n_flat >= n_lo) & (n_flat <= n_hi)
    if mask.any():
        out[mask] = np.interp(n_flat[mask], n_vec, T_max_vec)
    return out[0] if scalar else out


def calc_dynamics(v, a, grade, m_payload, param: VehicleParams):
    """计算电机工作点。

    参数
    ----
    v, a, grade, m_payload : 标量或同长度向量
        v: 车速 (km/h), a: 加速度 (m/s^2), grade: 坡度 (%),
        m_payload: 载重 (kg)
    param : VehicleParams

    返回
    ----
    n_motor, T_motor, P_wheel, flag_limit
    """
    v = np.asarray(v, dtype=np.float64)
    a = np.asarray(a, dtype=np.float64)
    grade = np.asarray(grade, dtype=np.float64)
    m_payload = np.asarray(m_payload, dtype=np.float64)

    v = np.maximum(v, 0.0)
    m_payload = np.maximum(m_payload, 0.0)

    m_total = param.m_vehicle + m_payload

    v_ms = v / 3.6
    alpha = np.arctan(grade / 100.0)

    F_roll = m_total * param.g * param.f_r * np.cos(alpha)
    F_air = 0.5 * param.rho * param.Cd * param.A * v_ms**2
    F_grade = m_total * param.g * np.sin(alpha)
    F_acc = param.delta * m_total * a

    F_total = F_roll + F_air + F_grade + F_acc

    P_wheel = F_total * v_ms

    n_motor = v_ms * param.i_total * 60.0 / (2.0 * np.pi * param.r_wh)
    n_motor = np.maximum(n_motor, 0.0)

    # 驱动 / 制动分工况计算电机转矩
    T_motor = np.where(
        P_wheel >= 0,
        F_total * param.r_wh / (param.i_total * param.eta_T),
        F_total * param.r_wh * param.eta_T / param.i_total,
    )

    # 外特性约束
    T_max_curr = _t_max_curve(n_motor, param.n_vec, param.T_max_vec)
    T_limit = np.abs(T_motor) > T_max_curr
    T_motor = np.where(T_limit, np.sign(T_motor) * T_max_curr, T_motor)
    flag_limit = T_limit.astype(np.float64)

    return n_motor, T_motor, P_wheel, flag_limit
