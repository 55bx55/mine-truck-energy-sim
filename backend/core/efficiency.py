"""电机效率 MAP 查表模块（移植自 MATLAB lookup_efficiency.m）

基于转速-转矩二维网格（永磁同步电机台架标定数据），对任意工作点
(n_motor, T_motor) 查询瞬时效率。支持 5 种工况的智能边界处理：

  1. 工作点在网格内部        → 双线性插值
  2. 转速超上限（高速弱磁区）→ 沿最外列插值 + 每超 1000 r/min 衰减 3%
  3. 转速低于下限（极低速）  → 效率随转速线性下降（最多 12%）
  4. 转矩超上限（过载区）    → 沿边界插值 + 每超 500 N·m 衰减 5%
  5. 其他（兜底）            → 0.82

最终物理约束 clamp 在 [0.50, 0.96]。
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import RegularGridInterpolator


class MotorEfficiencyMap:
    """电机效率 MAP（转速 n × 转矩 T 网格上的效率矩阵 eta）"""

    def __init__(self, n_vec, T_vec, eta):
        self.n_vec = np.asarray(n_vec, dtype=np.float64)
        self.T_vec = np.asarray(T_vec, dtype=np.float64)
        self.eta = np.asarray(eta, dtype=np.float64)  # 形状 (len(T_vec), len(n_vec))

        # 网格由 meshgrid(T_vec, n_vec) 生成，与 MATLAB 一致:
        #   N_grid 形状 (len(T_vec), len(n_vec))，每行相同（列方向为转速）
        #   T_grid 形状相同，每列相同（行方向为转矩）
        self.N_grid, self.T_grid = np.meshgrid(self.n_vec, self.T_vec)

        self.n_min, self.n_max = self.n_vec[0], self.n_vec[-1]
        self.T_min, self.T_max = self.T_vec[0], self.T_vec[-1]

        # 双线性插值器：坐标顺序 (T, n)，与 eta 的轴顺序一致
        self._interp = RegularGridInterpolator(
            (self.T_vec, self.n_vec),
            self.eta,
            method="linear",
            bounds_error=False,
            fill_value=np.nan,  # 网格外返回 NaN，由调用方按边界策略处理
        )

    @classmethod
    def from_dict(cls, d: dict) -> "MotorEfficiencyMap":
        return cls(d["n_vec"], d["T_vec"], d["eta"])

    def to_dict(self) -> dict:
        return {
            "n_vec": self.n_vec.tolist(),
            "T_vec": self.T_vec.tolist(),
            "eta": self.eta.tolist(),
        }

    # ------------------------------------------------------------------
    def _interp2(self, nq, Tq):
        """双线性插值（坐标 (Tq, nq)），网格外返回 NaN"""
        pts = np.stack([np.atleast_1d(Tq), np.atleast_1d(nq)], axis=-1)
        return self._interp(pts)

    def lookup(self, n_motor, T_motor):
        """查询瞬时效率（向量化，支持标量或数组）"""
        n = np.asarray(n_motor, dtype=np.float64)
        T = np.asarray(T_motor, dtype=np.float64)
        scalar = n.ndim == 0
        n_flat = np.atleast_1d(n)
        T_flat = np.atleast_1d(T)
        T_abs = np.abs(T_flat)

        eta_q = np.zeros_like(n_flat, dtype=np.float64)

        # ---- 情况 1：网格内部 → 双线性插值 ----
        m_in = (
            (n_flat >= self.n_min) & (n_flat <= self.n_max)
            & (T_abs >= self.T_min) & (T_abs <= self.T_max)
        )
        if m_in.any():
            eta_q[m_in] = self._interp2(n_flat[m_in], T_abs[m_in])

        # ---- 情况 2：转速超上限（高速弱磁区）----
        m_hi = n_flat > self.n_max
        if m_hi.any():
            eta_b = self._interp2(np.full_like(T_abs[m_hi], self.n_max), T_abs[m_hi])
            eta_b = np.where(np.isnan(eta_b), 0.65, eta_b)
            eta_b = np.where(eta_b < 0.01, 0.82, eta_b)
            decay = 0.03 * (n_flat[m_hi] - self.n_max) / 1000.0
            eta_q[m_hi] = np.maximum(0.60, eta_b - decay)

        # ---- 情况 3：转速低于下限（极低速）----
        m_lo = (n_flat < self.n_min) & (n_flat >= 0)
        if m_lo.any():
            eta_b = self._interp2(np.full_like(T_abs[m_lo], self.n_min), T_abs[m_lo])
            eta_b = np.where(np.isnan(eta_b), 0.70, eta_b)
            eta_b = np.where(eta_b < 0.01, 0.75, eta_b)
            ratio = np.maximum(0.0, 1.0 - (self.n_min - n_flat[m_lo]) / self.n_min)
            eta_q[m_lo] = np.maximum(0.60, eta_b * (0.88 + 0.12 * ratio))

        # ---- 情况 4：转矩超上限（过载区）----
        m_ov = T_abs > self.T_max
        if m_ov.any():
            eta_b = self._interp2(n_flat[m_ov], np.full_like(n_flat[m_ov], self.T_max))
            eta_b = np.where(np.isnan(eta_b), 0.70, eta_b)
            eta_b = np.where(eta_b < 0.01, 0.75, eta_b)
            decay = 0.05 * (T_abs[m_ov] - self.T_max) / 500.0
            eta_q[m_ov] = np.maximum(0.55, eta_b - decay)

        # ---- 情况 5：兜底（理论上不发生）----
        m_else = (~m_in) & (~m_hi) & (~m_lo) & (~m_ov)
        eta_q[m_else] = 0.82

        # 最终物理约束
        eta_q = np.clip(eta_q, 0.50, 0.96)

        return eta_q[0] if scalar else eta_q


def load_efficiency_map(path: str) -> MotorEfficiencyMap:
    """从 .npz 文件加载效率 MAP"""
    data = np.load(path, allow_pickle=True)
    return MotorEfficiencyMap(data["n_vec"], data["T_vec"], data["eta"])
