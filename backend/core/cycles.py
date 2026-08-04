"""典型矿山工况生成器（移植自 MATLAB create_drive_cycles02.m）

构造三种典型矿山工况的速度-坡度-载重时间序列（采样间隔 1 s）：
  - 工况1 重载上坡（8% 坡度，满载 60 t）
  - 工况2 平路满载（0% 坡度，满载 60 t）
  - 工况3 长下坡回收（-7% 坡度，空载，制动能量回收场景）

加速度由速度差分得到，并经高斯平滑（与 MATLAB smoothdata(x,'gaussian',3)
边界截断+归一化策略一致）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class DriveCycle:
    """一个工况：时间序列的速度 / 加速度 / 坡度 / 载重"""

    name: str
    t: np.ndarray
    v: np.ndarray
    a: np.ndarray
    grade: np.ndarray
    payload: np.ndarray

    @property
    def duration(self) -> int:
        return int(len(self.t))

    @property
    def distance_km(self) -> float:
        """工况总行驶距离 (km)，梯形积分"""
        v_ms = self.v / 3.6
        return float(np.trapezoid(v_ms, self.t) / 1000.0)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "t": self.t.tolist(),
            "v": self.v.tolist(),
            "a": self.a.tolist(),
            "grade": self.grade.tolist(),
            "payload": self.payload.tolist(),
            "duration": self.duration,
            "distance_km": round(self.distance_km, 3),
        }


def smoothdata_gaussian(x: np.ndarray, window: int = 3) -> np.ndarray:
    """MATLAB smoothdata(x, 'gaussian', window) 的等价实现。

    MATLAB 内部使用高斯核：w(x) = exp(-x²/(2·σ²))，σ = window/5，
    对窗口内整数偏移 x = -(window-1)/2 .. (window-1)/2 采样。
    边界处窗口截断并重新归一化（默认 'shrink' 策略）。
    已用毕设 MATLAB 工况数据逐点验证（误差 ~1e-17）。
    """
    x = np.asarray(x, dtype=np.float64)
    n = len(x)
    half = (window - 1) // 2
    sigma = window / 5.0
    offsets = np.arange(-half, half + 1)
    w = np.exp(-offsets**2 / (2.0 * sigma**2))

    out = np.zeros_like(x)
    for i in range(n):
        idx = i + offsets
        valid = (idx >= 0) & (idx < n)
        out[i] = np.sum(w[valid] * x[idx[valid]]) / np.sum(w[valid])
    return out


def _build_cycle(name: str, t: np.ndarray, v: np.ndarray, grade: np.ndarray, payload: np.ndarray) -> DriveCycle:
    dt = 1.0
    a = np.concatenate([[0.0], np.diff(v) / 3.6 / dt])
    a = smoothdata_gaussian(a, window=3)
    return DriveCycle(name=name, t=t, v=v, a=a, grade=grade, payload=payload)


def build_custom_cycle(v, grade, payload, name: str = "自定义工况") -> DriveCycle:
    """由前端传入的速度/坡度/载重序列构造工况（加速度差分 + 高斯平滑）

    参数
    ----
    v, grade, payload : 等长数组（v: km/h, grade: %, payload: kg）
    """
    v = np.asarray(v, dtype=np.float64)
    grade = np.asarray(grade, dtype=np.float64)
    payload = np.asarray(payload, dtype=np.float64)
    n = len(v)
    if not (len(grade) == n and len(payload) == n):
        raise ValueError("v / grade / payload 长度必须一致")
    t = np.arange(n, dtype=np.float64)
    return _build_cycle(name, t, v, grade, payload)


def build_default_cycles() -> list[DriveCycle]:
    """构造论文中的三种基准工况"""

    # ---- 工况1：重载上坡（300 s）----
    t1 = np.arange(0, 300, dtype=np.float64)
    v1 = np.zeros(300)
    v1[0:50] = np.linspace(0, 10, 50)
    v1[50:250] = 10.0
    v1[250:300] = np.linspace(10, 0, 50)
    grade1 = np.full(300, 8.0)
    payload1 = np.full(300, 60000.0)
    c1 = _build_cycle("重载上坡", t1, v1, grade1, payload1)

    # ---- 工况2：平路满载（240 s）----
    t2 = np.arange(0, 240, dtype=np.float64)
    v2 = np.zeros(240)
    v2[0:40] = np.linspace(0, 20, 40)
    v2[40:200] = 20.0
    v2[200:240] = np.linspace(20, 0, 40)
    grade2 = np.zeros(240)
    payload2 = np.full(240, 60000.0)
    c2 = _build_cycle("平路满载", t2, v2, grade2, payload2)

    # ---- 工况3：长下坡回收（250 s）----
    t3 = np.arange(0, 250, dtype=np.float64)
    v3 = np.zeros(250)
    v3[0:30] = np.linspace(0, 25, 30)
    v3[30:200] = 25.0
    v3[200:250] = np.linspace(25, 0, 50)
    grade3 = np.full(250, -7.0)
    payload3 = np.zeros(250)
    c3 = _build_cycle("长下坡回收", t3, v3, grade3, payload3)

    return [c1, c2, c3]
