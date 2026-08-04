"""API 冒烟测试：验证全部端点"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from backend.app import app

c = TestClient(app)

print("health:", c.get("/api/health").json())

meta = c.get("/api/meta").json()
print("meta: cycles =", meta["cycles"], "| peak_eta =", meta["motor_map"]["peak_eta_pct"], "%")

mm = c.get("/api/motor-map").json()
print("motor-map: n x T =", len(mm["n"]), "x", len(mm["T"]))

r = c.post("/api/simulate", json={"cycle_ids": [0, 1, 2]})
assert r.status_code == 200, r.text
for res in r.json()["results"]:
    eta = res["eta_avg_drive_weighted"]
    eta_s = "—" if eta is None else f"{eta:.4f}"
    print(f"  simulate {res['cycle_name']}: E_total={res['E_total_kWh']} kWh, "
          f"eta_wtd={eta_s}")

r2 = c.post("/api/sensitivity", json={})
assert r2.status_code == 200
for x in r2.json()["results"]:
    print("  sens:", x["display_name"], "| 敏感度 =", x["sensitivity"],
          "| Δ(-20%→+20%) =", round(x["delta_pct"][0], 2), "→", round(x["delta_pct"][4], 2))

r3 = c.post("/api/decompose", json={"cycle_id": 0})
assert r3.status_code == 200
d = r3.json()
print("decompose 重载上坡: 净能耗 =", d["E_batt_total"], "kWh, 铜损 =", d["电机铜损"],
      "kWh, 滚动阻力 =", d["滚动阻力损耗"], "kWh")

r4 = c.post("/api/simulate", json={
    "params": {"m_vehicle": 35000, "f_r": 0.03},
    "cycle_ids": [0],
})
res4 = r4.json()["results"][0]
print("自定义参数(整备35t, fr=0.03): E_total =", res4["E_total_kWh"], "kWh (基准 27.36)")

print("\n全部 API 冒烟测试通过 ✔")
