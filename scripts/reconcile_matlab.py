"""对账脚本：验证 Python 移植结果与 MATLAB 基准数据一致

读取毕设 MATLAB 生成的 simulation_results.mat / drive_cycles.mat /
motor_efficiency_map.mat，与 Python 移植版逐项对比：
  1. 工况加速度序列（高斯平滑复现）
  2. 效率 MAP 曲面（生成公式复现）
  3. 三个工况的 E_total / 平均效率
  4. 逐点序列（n_motor/T_motor/eta_inst/P_wheel/P_batt/E_cum）最大误差

用法：python scripts/reconcile_matlab.py [mat_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import scipy.io as sio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from core.cycles import build_default_cycles  # noqa: E402
from core.efficiency import MotorEfficiencyMap  # noqa: E402
from core.map_generator import generate_motor_map  # noqa: E402
from core.simulation import simulate_all  # noqa: E402
from core.vehicle import VehicleParams  # noqa: E402


def _unwrap(x):
    """解开 scipy.io.loadmat 嵌套 object 数组，最终取出标量"""
    while isinstance(x, np.ndarray) and x.dtype == object and x.size == 1:
        x = x[0]
    if isinstance(x, np.ndarray):
        return x.item()
    return x


def main(mat_dir: str) -> int:
    mat_dir = Path(mat_dir)
    ok = True

    def check(name: str, cond: bool, detail: str):
        nonlocal ok
        tag = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"  [{tag}] {name}: {detail}")

    # ---------- 1. 工况数据 ----------
    print("== 1. 工况加速度（高斯平滑）==")
    c_mat = sio.loadmat(mat_dir / "drive_cycles.mat")
    cycles_py = build_default_cycles()
    for i, cy in enumerate(cycles_py):
        cy_mat = c_mat[f"cycle{i+1}"][0, 0]
        a_mat = cy_mat["a"].ravel()
        a_py = cy.a
        err = np.max(np.abs(a_py - a_mat))
        check(f"cycle{i+1} a", err < 1e-9, f"max|Δa|={err:.2e}")

    # ---------- 2. 效率 MAP ----------
    print("== 2. 效率 MAP 曲面 ==")
    m_mat = sio.loadmat(mat_dir / "motor_efficiency_map.mat")
    eta_mat = m_mat["eta"]
    eta_py = generate_motor_map()
    err = np.max(np.abs(eta_py.eta - eta_mat))
    check("eta 曲面", err < 1e-12, f"max|Δeta|={err:.2e}")

    # ---------- 3. 仿真结果 ----------
    print("== 3. 仿真结果对账 ==")
    r_mat = sio.loadmat(mat_dir / "simulation_results.mat")["results"]  # (1,3) object

    # 用 MATLAB 的 MAP 数据跑 Python 仿真，隔离 MAP 生成差异
    eff_map_matlab = MotorEfficiencyMap(m_mat["n"].ravel(), m_mat["T_grid"][:, 0], eta_mat)
    param = VehicleParams()
    res_py = simulate_all(cycles_py, eff_map_matlab, param)

    field_map = {
        "n_motor": "n_motor",
        "T_motor": "T_motor",
        "eta_inst": "eta_inst",
        "P_wheel": "P_wheel",
        "P_batt": "P_batt",
        "E_cum": "E_cum",
    }
    for i, (r_mat_i, r_py) in enumerate(zip(r_mat[0], res_py)):
        st = r_mat_i[0, 0]  # 结构体
        name = str(_unwrap(st["cycle_name"]))
        print(f"  --- {name} ---")
        E_mat = float(_unwrap(st["E_total"]))
        check(
            "E_total",
            abs(E_mat - r_py.E_total) < 1e-6,
            f"MATLAB={E_mat:.4f}Wh Py={r_py.E_total:.4f}Wh",
        )
        a_mat_v = float(_unwrap(st["eta_avg_drive_arith"]))
        w_mat_v = float(_unwrap(st["eta_avg_drive_weighted"]))
        for label, v_mat, v_py in [
            ("eta_arith", a_mat_v, r_py.eta_avg_drive_arith),
            ("eta_weighted", w_mat_v, r_py.eta_avg_drive_weighted),
        ]:
            same = (np.isnan(v_mat) and np.isnan(v_py)) or abs(v_mat - v_py) < 1e-9
            check(label, same, f"MATLAB={v_mat:.6f} Py={v_py:.6f}")
        for f_mat, f_py in field_map.items():
            arr_mat = np.asarray(st[f_mat]).ravel()
            arr_py = getattr(r_py, f_py)
            err = np.max(np.abs(arr_mat - arr_py))
            check(f"{f_mat}", err < 1e-6, f"max|Δ|={err:.2e}")

    print()
    print("结论:", "全部通过 ✔  Python 移植与 MATLAB 基准一致" if ok else "存在差异，需要排查 ✘")
    return 0 if ok else 1


if __name__ == "__main__":
    mat_dir = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\LV\Desktop\毕设\02_数据"
    raise SystemExit(main(mat_dir))
