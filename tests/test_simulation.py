"""核心算法单元测试

包含：
  1. 动力学方程手算校验（60 吨级矿卡典型工作点）
  2. 效率查表五种边界工况
  3. C-rate 电池效率模型
  4. 仿真结果与 MATLAB 基准值一致性（硬编码对账）
  5. 能量分解守恒性
  6. 敏感性分析排序
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from core.battery import battery_efficiency  # noqa: E402
from core.cycles import build_default_cycles  # noqa: E402
from core.decomposition import decompose_cycle  # noqa: E402
from core.efficiency import MotorEfficiencyMap  # noqa: E402
from core.map_generator import generate_motor_map  # noqa: E402
from core.sensitivity import run_sensitivity  # noqa: E402
from core.simulation import simulate_all, simulate_cycle  # noqa: E402
from core.vehicle import VehicleParams, calc_dynamics  # noqa: E402


@pytest.fixture(scope="module")
def eff_map():
    return generate_motor_map()


@pytest.fixture(scope="module")
def param():
    return VehicleParams()


@pytest.fixture(scope="module")
def cycles():
    return build_default_cycles()


# ----------------------------------------------------------------------
# 1. 动力学
# ----------------------------------------------------------------------
class TestDynamics:
    def test_typical_work_point(self, param):
        """手算校验：v=10km/h 满载 8% 上坡匀速"""
        v, a, grade, payload = 10.0, 0.0, 8.0, 60000.0
        n, T, P, flag = calc_dynamics(v, a, grade, payload, param)

        m_total = param.m_vehicle + payload
        alpha = np.arctan(grade / 100.0)
        v_ms = v / 3.6
        F_roll = m_total * param.g * param.f_r * np.cos(alpha)
        F_grade = m_total * param.g * np.sin(alpha)
        F_air = 0.5 * param.rho * param.Cd * param.A * v_ms**2
        F_total = F_roll + F_grade + F_air
        P_expect = F_total * v_ms
        n_expect = v_ms * param.i_total * 60 / (2 * np.pi * param.r_wh)

        assert P == pytest.approx(P_expect, rel=1e-9)
        assert n == pytest.approx(n_expect, rel=1e-9)
        # 8% 上坡满载，功率应显著为正（几十 kW 到几百 kW 量级）
        assert P > 200e3 and P < 300e3
        assert T > 0
        assert flag == 0  # 未触及外特性

    def test_negative_velocity_clamped(self, param):
        n, T, P, _ = calc_dynamics(-5.0, 0.0, 0.0, 0.0, param)
        assert n == 0
        assert P == 0

    def test_torque_limited_by_external_curve(self, param):
        """极陡上坡满载起步：转矩应被外特性截断"""
        n, T, P, flag = calc_dynamics(1.0, 0.0, 20.0, 60000.0, param)
        assert flag == 1
        T_max = np.interp(n, param.n_vec, param.T_max_vec)
        assert abs(T) <= T_max * 1.001


# ----------------------------------------------------------------------
# 2. 效率查表边界
# ----------------------------------------------------------------------
class TestLookup:
    def test_inside_grid(self, eff_map):
        eta = eff_map.lookup(3000.0, 1200.0)  # 高效区典型点
        assert 0.85 <= eta <= 0.96

    def test_high_speed_field_weakening(self, eff_map):
        """转速超上限（>7000）应低于边界值并衰减"""
        eta_boundary = eff_map.lookup(7000.0, 800.0)
        eta_over = eff_map.lookup(8000.0, 800.0)
        assert eta_over < eta_boundary
        assert eta_over >= 0.60

    def test_low_speed(self, eff_map):
        eta_low = eff_map.lookup(50.0, 800.0)
        assert 0.60 <= eta_low <= 0.96

    def test_overload_torque(self, eff_map):
        """转矩超上限：效率应降低但保持物理下限"""
        eta_over = eff_map.lookup(1500.0, 4500.0)  # 超 3500 上限
        assert eta_over >= 0.55
        assert eta_over <= eff_map.lookup(1500.0, 2500.0) + 1e-9

    def test_vectorized(self, eff_map):
        n = np.array([1000.0, 3000.0, 5000.0])
        T = np.array([500.0, 1200.0, 2000.0])
        eta = eff_map.lookup(n, T)
        assert eta.shape == (3,)
        assert np.all(eta >= 0.50) and np.all(eta <= 0.96)


# ----------------------------------------------------------------------
# 3. 电池效率
# ----------------------------------------------------------------------
class TestBattery:
    def test_low_power_near_open_circuit(self, param):
        assert battery_efficiency(50.0, "discharge", param) == pytest.approx(0.995)

    def test_discharge_c_rate(self, param):
        # 1C = 300 kW 放电
        eta_1c = battery_efficiency(300e3, "discharge", param)
        assert eta_1c == pytest.approx(0.96 - 0.02, abs=1e-9)
        # 高 C-rate 有下限
        eta_10c = battery_efficiency(3000e3, "discharge", param)
        assert eta_10c >= 0.85

    def test_charge_more_lossy(self, param):
        eta_dis = battery_efficiency(150e3, "discharge", param)
        eta_chg = battery_efficiency(150e3, "charge", param)
        assert eta_chg < eta_dis


# ----------------------------------------------------------------------
# 4. 仿真结果与 MATLAB 基准一致（硬编码对账值）
# ----------------------------------------------------------------------
class TestSimulationAgainstMatlab:
    """基准值来自毕设 MATLAB simulation_results.mat（已通过脚本对账）"""

    def test_energy_total(self, cycles, eff_map, param):
        res = simulate_all(cycles, eff_map, param)
        expects = [27361.5452, 7469.4328, -4238.7208]  # Wh
        for r, e in zip(res, expects):
            assert r.E_total == pytest.approx(e, abs=0.05)

    def test_avg_efficiency(self, cycles, eff_map, param):
        res = simulate_all(cycles, eff_map, param)
        # 重载上坡：功率加权平均效率
        assert res[0].eta_avg_drive_weighted == pytest.approx(0.697242, abs=1e-5)
        # 长下坡：无驱动段，效率为 NaN
        assert np.isnan(res[2].eta_avg_drive_arith)

    def test_energy_conservation(self, cycles, eff_map, param):
        """驱动能耗 - 回收能量 应等于净能耗（近似）"""
        for r in simulate_all(cycles, eff_map, param):
            net = r.E_drive_kWh - r.E_regen_kWh
            assert abs(net - r.E_total / 1000.0) < 0.01

    def test_cumulative_energy_monotonic_shape(self, cycles, eff_map, param):
        r = simulate_all(cycles, eff_map, param)[0]
        assert r.E_cum[-1] == pytest.approx(r.E_total, abs=0.05)


# ----------------------------------------------------------------------
# 5. 能量分解
# ----------------------------------------------------------------------
class TestDecomposition:
    def test_breakdown_sums_consistent(self, cycles, eff_map, param):
        res = simulate_all(cycles, eff_map, param)
        for c, r in zip(cycles, res):
            bd = decompose_cycle(c, r, param)
            # 各项损耗非负
            for k in ["E_loss_cu", "E_loss_fe", "E_loss_low", "E_loss_base",
                      "E_loss_trans", "E_loss_roll", "E_loss_air"]:
                assert getattr(bd, k) >= -1e-9, f"{k} 为负"
            # 分解项之和不超过总能耗绝对值
            total = abs(bd.E_batt_total)
            parts = (bd.E_loss_cu + bd.E_loss_fe + bd.E_loss_low + bd.E_loss_base
                     + bd.E_loss_trans + bd.E_loss_roll + bd.E_loss_air
                     + bd.E_other)
            assert parts <= total * 1.02

    def test_downhill_recovery_negative(self, cycles, eff_map, param):
        """长下坡工况净能耗为负（回收大于消耗）"""
        res = simulate_all(cycles, eff_map, param)[2]
        assert res.E_total < 0


# ----------------------------------------------------------------------
# 6. 敏感性分析
# ----------------------------------------------------------------------
class TestSensitivity:
    def test_order(self, cycles, eff_map, param):
        """传动效率对能耗影响最大（效率直接进入功率链路）"""
        results = run_sensitivity(cycles[0], eff_map, param)
        by_key = {r.param_key: r.sensitivity for r in results}
        assert by_key["eta_T"] > by_key["m_payload"] > by_key["f_r"]
        assert by_key["i_total"] > 0

    def test_zero_perturbation_equals_baseline(self, cycles, eff_map, param):
        results = run_sensitivity(cycles[0], eff_map, param)
        base = simulate_cycle(cycles[0], eff_map, param).E_total / 1000.0
        for r in results:
            assert r.E_values_kWh[2] == pytest.approx(base, rel=1e-6)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
