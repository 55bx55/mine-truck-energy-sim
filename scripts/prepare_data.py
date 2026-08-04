"""数据自包含脚本

把 MATLAB 时代的 .mat 数据源改为程序化生成：
  - 电机效率 MAP → generate_motor_map() 生成，缓存为 .npz
  - 三工况 → build_default_cycles() 生成，缓存为 JSON

运行后 backend/data/ 下生成：
  motor_efficiency_map.npz
  drive_cycles.json

开源后 clone 即用，无需 MATLAB 及原始 .mat 文件。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

# 保证从 scripts/ 或项目根运行均可导入 backend
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.cycles import build_default_cycles  # noqa: E402
from backend.core.map_generator import generate_motor_map  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def prepare_data(force: bool = False) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 电机效率 MAP ----
    map_path = DATA_DIR / "motor_efficiency_map.npz"
    if force or not map_path.exists():
        eff_map = generate_motor_map()
        np.savez_compressed(
            map_path,
            n_vec=eff_map.n_vec,
            T_vec=eff_map.T_vec,
            eta=eff_map.eta,
        )
        print(f"[prepare] 已生成 {map_path.name}: "
              f"峰值效率 {eff_map.eta.max()*100:.1f}%, "
              f"网格 {eff_map.eta.shape}")

    # ---- 三工况 ----
    cycles_path = DATA_DIR / "drive_cycles.json"
    if force or not cycles_path.exists():
        cycles = [c.to_dict() for c in build_default_cycles()]
        with open(cycles_path, "w", encoding="utf-8") as f:
            json.dump(cycles, f, ensure_ascii=False, indent=1)
        print(f"[prepare] 已生成 {cycles_path.name}: "
              f"{[c['name'] for c in cycles]}")


if __name__ == "__main__":
    prepare_data(force=True)
