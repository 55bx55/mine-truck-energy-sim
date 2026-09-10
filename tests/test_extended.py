"""扩展功能测试：SOC 跟踪 / 自定义工况 / 多车型对比"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from core.cycles import build_custom_cycle, build_default_cycles  # noqa: E402
from core.map_generator import generate_motor_map  # noqa: E402
from core.simulation import simulate_cycle  # noqa: E402
from core.vehicle import VehicleParams  # noqa: E402
from backend.app import app  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def eff_map():
    return generate_motor_map()


@pytest.fixture(scope="module")
def cycles():
    return build_default_cycles()


@pytest.fixture(scope="module")
def param():
    return VehicleParams()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ----------------------------------------------------------------------
# SOC 跟踪
# ----------------------------------------------------------------------
class TestSOC:
    def test_drive_discharges(self, cycles, eff_map, param):
        r = simulate_cycle(cycles[0], eff_map, param)
        assert r.soc_end == pytest.approx(0.9 - 27.362 / 300, abs=1e-3)
        assert r.soc_delta_pct < 0

    def test_regen_charges(self, cycles, eff_map, param):
        r = simulate_cycle(cycles[2], eff_map, param)
        assert r.soc_end > 0.9  # 下坡回收 SOC 回升

    def test_soc_monotonic_with_energy(self, cycles, eff_map, param):
        """SOC 曲线与累积能耗严格对应"""
        r = simulate_cycle(cycles[0], eff_map, param)
        expect = 0.9 - r.E_cum / 1000.0 / param.batt_capacity_kWh
        assert np.allclose(r.soc, expect, atol=1e-9)


# ----------------------------------------------------------------------
# 自定义工况
# ----------------------------------------------------------------------
class TestCustomCycle:
    def test_build_and_simulate(self, eff_map, param):
        v = np.concatenate([
            np.linspace(0, 15, 30), np.full(60, 15.0), np.linspace(15, 0, 30),
        ])
        grade = np.full(len(v), 5.0)
        payload = np.full(len(v), 40000.0)
        cycle = build_custom_cycle(v, grade, payload, name="测试")
        assert cycle.duration == len(v)
        assert cycle.name == "测试"
        r = simulate_cycle(cycle, eff_map, param)
        assert len(r.n_motor) == len(v)
        assert r.E_total > 0  # 上坡消耗能量

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError):
            build_custom_cycle([1, 2, 3], [1, 2], [1, 2, 3])

    def test_downhill_custom_regen(self, eff_map, param):
        """自定义空载下坡工况应产生能量回收（净能耗为负）"""
        v = np.concatenate([
            np.linspace(0, 20, 30), np.full(60, 20.0), np.linspace(20, 0, 30),
        ])
        grade = np.full(len(v), -6.0)
        payload = np.zeros(len(v))
        cycle = build_custom_cycle(v, grade, payload, name="下坡")
        r = simulate_cycle(cycle, eff_map, param)
        assert r.E_total < 0
        assert r.E_regen_kWh > 0


# ----------------------------------------------------------------------
# API 集成：自定义工况 / 对比
# ----------------------------------------------------------------------
class TestAPI:
    def test_simulate_custom(self, client):
        custom = {
            "v": [0] * 10 + [12] * 60 + list(range(12, -1, -1))[:12],
            "grade": [4] * 82,
            "payload": [50000] * 82,
            "name": "API测试",
        }
        r = client.post("/api/simulate", json={"custom_cycle": custom})
        assert r.status_code == 200
        res = r.json()["results"][0]
        assert res["cycle_name"] == "API测试"
        assert len(res["soc"]) == 82

    def test_compare_matrix(self, client):
        r = client.post("/api/compare", json={"cycle_ids": [0, 1, 2]})
        d = r.json()
        assert len(d["presets"]) == 3
        assert len(d["rows"]) == 3
        for row in d["rows"]:
            assert len(row["cells"]) == 3
        # 同工况下 110 吨级能耗 > 60 吨级
        e60 = d["rows"][0]["cells"][0]["E_total_kWh"]
        e110 = d["rows"][2]["cells"][0]["E_total_kWh"]
        assert e110 > e60

    def test_efficiency_model_comparison(self, client):
        r = client.post("/api/compare-efficiency", json={"cycle_ids": [0, 1, 2]})
        assert r.status_code == 200
        d = r.json()
        assert d["eta_constant"] == pytest.approx(0.90)
        assert len(d["results"]) == 3
        uphill = d["results"][0]
        assert uphill["E_constant_kWh"] < uphill["E_map_kWh"]

    def test_meta_presets(self, client):
        meta = client.get("/api/meta").json()
        keys = {p["key"] for p in meta["vehicle_presets"]}
        assert {"t60", "t90", "t110"} <= keys

    def test_decompose_custom(self, client):
        custom = {"v": [0] * 5 + [10] * 50, "grade": [5] * 55, "payload": [30000] * 55}
        r = client.post("/api/decompose", json={"custom_cycle": custom})
        assert r.status_code == 200
        d = r.json()
        assert d["E_batt_total"] > 0
