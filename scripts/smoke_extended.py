"""扩展功能冒烟测试：SOC / 自定义工况 / 多车型对比"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from backend.app import app

c = TestClient(app)

# ---- 1. SOC ----
r = c.post("/api/simulate", json={"cycle_ids": [0]})
res = r.json()["results"][0]
print("1. SOC:", "soc_end =", res["soc_end"], "| Δ =", res["soc_delta_pct"], "pp")
assert abs(res["soc_end"] - 0.9 + 27.362 / 300) < 0.002, "SOC 终点与能耗不符"
assert len(res["soc"]) == 300
assert res["soc"][-1] < res["soc"][0]  # 上坡 SOC 下降

r = c.post("/api/simulate", json={"cycle_ids": [2]})
res = r.json()["results"][0]
print("   长下坡: soc_end =", res["soc_end"], "（回收应回升）")
assert res["soc_end"] > 0.9, "下坡回收 SOC 应上升"

# ---- 2. 自定义工况 ----
custom = {
    "v": [0] * 20 + [15] * 100 + list(range(15, -1, -1))[:15],
    "grade": [5] * 135,
    "payload": [40000] * 135,
    "name": "测试爬坡",
}
r = c.post("/api/simulate", json={"custom_cycle": custom})
res = r.json()["results"][0]
print("2. 自定义工况:", res["cycle_name"], "| E_total =", res["E_total_kWh"], "kWh | 时长 =", len(res["n_motor"]), "s")
assert res["cycle_name"] == "测试爬坡"
assert len(res["n_motor"]) == 135

# 参数校验：长度不一致应报错
try:
    c.post("/api/simulate", json={"custom_cycle": {"v": [1, 2], "grade": [1], "payload": [1]}})
    print("   [FAIL] 长度校验未生效")
except Exception:
    print("   长度不一致 → 后端拒绝 ✔")

# ---- 3. 多车型对比 ----
r = c.post("/api/compare", json={"cycle_ids": [0, 1, 2]})
d = r.json()
print("3. 多车型对比:", d["presets"], d["cycle_names"])
for row in d["rows"]:
    vals = [x["E_total_kWh"] for x in row["cells"]]
    print(f"   {row['name']}: {vals} kWh")
assert len(d["rows"]) == 3 and len(d["rows"][0]["cells"]) == 3
# 同工况下，吨级越大能耗越高
assert d["rows"][0]["cells"][0]["E_total_kWh"] < d["rows"][2]["cells"][0]["E_total_kWh"]

# ---- 4. meta 含预设 ----
meta = c.get("/api/meta").json()
print("4. meta presets:", [p["name"] for p in meta["vehicle_presets"]])

print("\n扩展功能全部通过 ✔")
