"""数据驱动代理模型流水线的冒烟测试（小样本，秒级完成）。

覆盖：工况采样 → 特征提取 → 标签生成 → 岭回归/MLP 训练 → 评测。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from backend.core.efficiency import load_efficiency_map
from ml.data import ConstantEfficiencyMap, FEATURE_NAMES, build_dataset, extract_features, sample_cycle
from ml.models import MLPRegressor, RidgeRegression

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "data" / "motor_efficiency_map.npz"


@pytest.fixture(scope="module")
def eff_map():
    return load_efficiency_map(str(MAP_PATH))


def test_feature_vector_shape_and_finiteness(eff_map):
    rng = np.random.default_rng(0)
    cycle, param, _ = sample_cycle(rng, 0)
    x = extract_features(cycle, param)
    assert x.shape == (len(FEATURE_NAMES),)
    assert np.all(np.isfinite(x))


def test_constant_efficiency_baseline_returns_eta(eff_map):
    cmap = ConstantEfficiencyMap(0.90)
    out = cmap.lookup(np.array([1000.0, 3000.0]), np.array([500.0, 900.0]))
    assert np.allclose(out, 0.90)


def test_dataset_labels_are_finite(eff_map):
    ds = build_dataset(20, eff_map, seed=1)
    assert ds["X"].shape == (20, len(FEATURE_NAMES))
    assert np.all(np.isfinite(ds["y"]))
    assert len(ds["y_const"]) == 20


def test_ridge_and_mlp_can_fit_small_dataset(eff_map):
    train = build_dataset(120, eff_map, seed=2)
    test = build_dataset(40, eff_map, seed=3)

    ridge = RidgeRegression(alpha=1.0).fit(train["X"], train["y"])
    pred_r = ridge.predict(test["X"])
    assert pred_r.shape == test["y"].shape

    mlp = MLPRegressor(hidden=(16,), lr=3e-3, epochs=40, batch_size=32, seed=0, patience=10)
    mlp.fit(train["X"], train["y"])
    pred_m = mlp.predict(test["X"])
    assert pred_m.shape == test["y"].shape
    assert np.all(np.isfinite(pred_m))

    # 岭回归在小样本上也必须优于“全预测均值”这种平凡基准
    mae_ridge = float(np.mean(np.abs(pred_r - test["y"])))
    mae_trivial = float(np.mean(np.abs(train["y"].mean() - test["y"])))
    assert mae_ridge < mae_trivial
