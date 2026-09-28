"""FastAPI 接口测试（使用临时 SQLite 数据库）。"""
import os
import tempfile

import pytest

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="dimcheck-test-"), "test.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "量纲校验台" in r.text


def test_check_ok():
    r = client.post("/api/check", json={
        "expression": "0.5*mass*v^2",
        "variables": {"mass": "2 kg", "v": "10 m/s"},
        "target_unit": "kJ",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["result"]["dimension_str"] == "L^2·M·T^-2"
    assert data["result"]["target"]["value"] == 0.1
    assert "J" in data["result"]["derived"]


def test_check_error_with_span():
    r = client.post("/api/check", json={"expression": "1 m + 1 s"})
    data = r.json()
    assert data["ok"] is False
    err = data["errors"][0]
    assert err["kind"] == "dim_mismatch"
    assert "1 m + 1 s"[err["start"]:err["end"]] == "1 s"


def test_check_undefined_variable():
    r = client.post("/api/check", json={"expression": "x + 1"})
    data = r.json()
    assert data["ok"] is False
    assert data["errors"][0]["kind"] == "undefined"


def test_check_with_uncertainties():
    r = client.post("/api/check", json={
        "expression": "0.5*mass*v^2",
        "variables": {"mass": "2 kg", "v": "10 m/s"},
        "uncertainties": {"v": "1 m/s"},
        "target_unit": "kJ",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    u = data["result"]["uncertainty"]
    # dE/dv = m v = 20
    assert u["si_value"] == pytest.approx(20.0, rel=1e-9)
    assert u["sources"][0]["name"] == "v"
    # 标称值与不确定度一起换算到 kJ
    assert data["result"]["target"]["value"] == 0.1
    assert data["result"]["target"]["uncertainty"] == pytest.approx(0.02, rel=1e-9)


def test_check_uncertainty_validation_error():
    r = client.post("/api/check", json={
        "expression": "x",
        "variables": {"x": "5 m"},
        "uncertainties": {"x": "2 s"},
    })
    data = r.json()
    assert data["ok"] is False
    assert data["errors"][0]["kind"] == "uncertainty_dim"
    assert data["errors"][0]["source"] == "unc:x"
    # 标称值仍可返回，但没有不确定度汇总
    assert data["result"]["si_value"] == 5.0
    assert data["result"].get("uncertainty") is None


def test_check_zero_nominal_uncertainty():
    r = client.post("/api/check", json={
        "expression": "x",
        "variables": {"x": "0 m"},
        "uncertainties": {"x": "1 mm"},
    })
    data = r.json()
    assert data["ok"] is True
    assert data["result"]["uncertainty"]["si_value"] == pytest.approx(0.001, abs=1e-10)


def test_units_catalog():
    r = client.get("/api/units")
    assert r.status_code == 200
    data = r.json()
    names = {u["name"] for u in data["units"]}
    assert {"m", "kg", "s", "N", "J", "km", "kJ"} <= names
    assert any(f["name"] == "sqrt" for f in data["functions"])
    assert any(c["name"] == "g0" for c in data["constants"])


def test_cases_crud_and_export():
    # 创建
    r = client.post("/api/cases", json={
        "name": "自由落体",
        "expression": "0.5*g0*t^2",
        "variables": {"t": "3 s"},
        "uncertainties": {"t": "0.05 s"},
        "target_unit": "m",
    })
    assert r.status_code == 201
    case = r.json()
    assert case["id"] >= 1
    assert case["variables"] == {"t": "3 s"}
    assert case["uncertainties"] == {"t": "0.05 s"}

    # 列表
    r = client.get("/api/cases")
    assert any(c["name"] == "自由落体" for c in r.json()["cases"])

    # 读取
    r = client.get(f"/api/cases/{case['id']}")
    assert r.status_code == 200
    assert r.json()["expression"] == "0.5*g0*t^2"
    assert r.json()["uncertainties"] == {"t": "0.05 s"}

    # 导出
    r = client.get("/api/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    payload = r.json()
    assert payload["kind"] == "cases"
    assert any(c["name"] == "自由落体" for c in payload["cases"])

    # 删除
    r = client.delete(f"/api/cases/{case['id']}")
    assert r.status_code == 204
    r = client.get(f"/api/cases/{case['id']}")
    assert r.status_code == 404


def test_case_not_found():
    assert client.get("/api/cases/99999").status_code == 404
    assert client.delete("/api/cases/99999").status_code == 404
