"""FastAPI 应用入口

启动：uvicorn backend.app:app --reload
数据：首次启动自动生成（scripts/prepare_data.py），clone 即用
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core import decomposition, sensitivity, simulation
from .core.cycles import build_custom_cycle, build_default_cycles
from .core.efficiency import MotorEfficiencyMap
from .core.vehicle import VehicleParams

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(
    title="电动矿卡能耗预测与仿真分析系统",
    description="基于效率MAP查表与纵向动力学耦合的电动矿卡能耗仿真引擎",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# 数据加载（进程级缓存）
# ----------------------------------------------------------------------
@lru_cache(maxsize=1)
def _load_map() -> MotorEfficiencyMap:
    data = np.load(DATA_DIR / "motor_efficiency_map.npz")
    return MotorEfficiencyMap(data["n_vec"], data["T_vec"], data["eta"])


@lru_cache(maxsize=1)
def _load_cycles() -> list:
    """返回 DriveCycle 对象列表"""
    from .core.cycles import DriveCycle

    with open(DATA_DIR / "drive_cycles.json", encoding="utf-8") as f:
        raw = json.load(f)
    return [
        DriveCycle(
            name=c["name"],
            t=np.array(c["t"]),
            v=np.array(c["v"]),
            a=np.array(c["a"]),
            grade=np.array(c["grade"]),
            payload=np.array(c["payload"]),
        )
        for c in raw
    ]


def _resolve_params(overrides: dict | None) -> VehicleParams:
    """默认参数与前端覆盖参数合并"""
    p = VehicleParams()
    if overrides:
        for k, v in overrides.items():
            if hasattr(p, k):
                setattr(p, k, float(v))
    return p


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/meta")
def meta():
    m = _load_map()
    return {
        "vehicle_params": VehicleParams().to_dict(),
        "vehicle_presets": VehicleParams.PRESETS,
        "cycles": [c.name for c in _load_cycles()],
        "motor_map": {
            "peak_eta": round(float(m.eta.max()), 4),
            "peak_eta_pct": round(float(m.eta.max()) * 100, 1),
            "n_range": [float(m.n_vec[0]), float(m.n_vec[-1])],
            "T_range": [float(m.T_vec[0]), float(m.T_vec[-1])],
        },
    }


@app.get("/api/motor-map")
def motor_map(step_n: int = 4, step_T: int = 4):
    """效率 MAP 网格数据（默认降采样 4 倍，供前端热力图渲染）"""
    m = _load_map()
    return {
        "n": m.n_vec[::step_n].tolist(),
        "T": m.T_vec[::step_T].tolist(),
        "eta": (m.eta[::step_T, ::step_n] * 100).tolist(),  # %
    }


@app.get("/api/cycles")
def cycles():
    return {"cycles": [c.to_dict() for c in _load_cycles()]}


@app.post("/api/simulate")
def simulate(payload: dict):
    """运行仿真。

    body: {
      "params": {车辆参数字段覆盖},
      "cycle_ids": [0, 1, 2],
      "custom_cycle": {v: [...], grade: [...], payload: [...], name?: str}  # 可选
    }
    当传入 custom_cycle 时，返回结果为 [自定义工况结果]（忽略 cycle_ids）。
    """
    params = _resolve_params(payload.get("params"))
    cycle_ids = payload.get("cycle_ids") or [0, 1, 2]
    all_cycles = _load_cycles()

    results = []
    custom = payload.get("custom_cycle")
    if custom:
        cycle = build_custom_cycle(
            custom["v"], custom["grade"], custom["payload"],
            name=custom.get("name", "自定义工况"),
        )
        res = simulation.simulate_cycle(cycle, _load_map(), params)
        results.append(res.to_dict())
    else:
        for cid in cycle_ids:
            cycle = all_cycles[int(cid)]
            res = simulation.simulate_cycle(cycle, _load_map(), params)
            results.append(res.to_dict())
    return {"results": results}


@app.post("/api/compare")
def compare(payload: dict):
    """多车型同工况能耗对比（预设车型 × 工况矩阵）

    body: {"cycle_ids": [0,1,2]}
    """
    cycle_ids = payload.get("cycle_ids") or [0, 1, 2]
    all_cycles = _load_cycles()
    eff_map = _load_map()

    rows = []
    for preset in VehicleParams.PRESETS:
        params = _resolve_params(preset["params"])
        cells = []
        for cid in cycle_ids:
            res = simulation.simulate_cycle(all_cycles[int(cid)], eff_map, params)
            cells.append({
                "E_total_kWh": round(res.E_total / 1000.0, 3),
                "E_drive_kWh": round(res.E_drive_kWh, 3),
                "E_regen_kWh": round(res.E_regen_kWh, 3),
                "soc_delta_pct": round(res.soc_delta_pct, 3),
                "eta_wtd": None if res.eta_avg_drive_weighted != res.eta_avg_drive_weighted
                            else round(res.eta_avg_drive_weighted, 4),
            })
        rows.append({"key": preset["key"], "name": preset["name"], "desc": preset["desc"], "cells": cells})
    return {
        "presets": [p["name"] for p in VehicleParams.PRESETS],
        "cycle_names": [all_cycles[i].name for i in cycle_ids],
        "rows": rows,
    }


@app.post("/api/sensitivity")
def run_sensitivity(payload: dict):
    """参数敏感性分析（Tornado 数据）"""
    params = _resolve_params(payload.get("params"))
    cycle = _load_cycles()[0]  # 基准工况：重载上坡
    results = sensitivity.run_sensitivity(cycle, _load_map(), params)
    return {
        "results": [r.to_dict() for r in results],
        "tornado": sensitivity.tornado_data(results),
    }


@app.post("/api/decompose")
def decompose(payload: dict):
    """能量损耗分解（支持 cycle_id 或 custom_cycle）"""
    params = _resolve_params(payload.get("params"))
    custom = payload.get("custom_cycle")
    if custom:
        cycle = build_custom_cycle(
            custom["v"], custom["grade"], custom["payload"],
            name=custom.get("name", "自定义工况"),
        )
    else:
        cycle_id = int(payload.get("cycle_id") or 0)
        cycle = _load_cycles()[cycle_id]
    res = simulation.simulate_cycle(cycle, _load_map(), params)
    return decomposition.decompose_cycle(cycle, res, params).to_dict()


# ----------------------------------------------------------------------
# 前端静态资源
# ----------------------------------------------------------------------
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
