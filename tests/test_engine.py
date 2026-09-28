"""量纲引擎单元测试。"""
import math

import pytest

from app.engine import analyze, UNCERTAINTY_STEP


def ok(expr, variables=None, target=None, uncertainties=None):
    res = analyze(expr, variables or {}, target, uncertainties)
    assert res["ok"], res["errors"]
    assert res["result"] is not None
    return res["result"]


def fail(expr, variables=None, target=None, uncertainties=None):
    res = analyze(expr, variables or {}, target, uncertainties)
    assert not res["ok"]
    assert res["errors"], "应当返回错误"
    return res["errors"]


# ---------------- 基本运算与量纲推导 ----------------

def test_simple_addition_same_dim():
    r = ok("1 m + 2 m")
    assert r["si_value"] == pytest.approx(3.0)
    assert r["dimension_str"] == "L"
    assert r["si_unit"] == "m"


def test_unit_conversion_in_addition():
    r = ok("1 km + 2 m")
    assert r["si_value"] == pytest.approx(1002.0)
    r = ok("1 h + 30 min")
    assert r["si_value"] == pytest.approx(5400.0)
    assert r["si_unit"] == "s"


def test_acceleration_expression():
    r = ok("9.8 m/s^2")
    assert r["si_value"] == pytest.approx(9.8)
    assert r["dimension_str"] == "L·T^-2"
    assert r["si_unit"] == "m·s^-2"


def test_newton_derived():
    r = ok("1 kg*m/s^2")
    assert r["dimension_str"] == "L·M·T^-2"
    assert "N" in r["derived"]


def test_energy_with_variables():
    r = ok("0.5*mass*v^2 + mass*g0*h0", {"mass": "2 kg", "v": "10 m/s", "h0": "5 m"})
    # 0.5*2*100 + 2*9.80665*5 = 100 + 98.0665
    assert r["si_value"] == pytest.approx(198.0665)
    assert r["dimension_str"] == "L^2·M·T^-2"
    assert "J" in r["derived"]


def test_power_precedence_and_associativity():
    assert ok("2^3^2")["si_value"] == pytest.approx(512.0)   # 右结合
    assert ok("-2^2")["si_value"] == pytest.approx(-4.0)     # 一元负号优先级低于 ^
    assert ok("2^-2")["si_value"] == pytest.approx(0.25)
    assert ok("(2^3)^2")["si_value"] == pytest.approx(64.0)


def test_power_dimension_scaling():
    r = ok("(4 m^2)^0.5")
    assert r["si_value"] == pytest.approx(2.0)
    assert r["dimension_str"] == "L"
    r = ok("(1 m)^3")
    assert r["dimension_str"] == "L^3"


def test_sqrt_and_functions():
    assert ok("sqrt(4 m^2)")["si_value"] == pytest.approx(2.0)
    assert ok("sqrt(9 m^2/s^2)")["si_unit"] == "m·s^-1"
    assert ok("sin(30 deg)")["si_value"] == pytest.approx(0.5)
    assert ok("cos(pi)")["si_value"] == pytest.approx(-1.0)
    assert ok("log(100)")["si_value"] == pytest.approx(2.0)
    assert ok("ln(e)")["si_value"] == pytest.approx(1.0)
    assert ok("abs(-3 m)")["si_value"] == pytest.approx(3.0)


def test_implicit_multiplication_and_unicode():
    assert ok("2(3+4)")["si_value"] == pytest.approx(14.0)
    assert ok("2π r", {"r": "3 m"})["si_value"] == pytest.approx(2 * math.pi * 3)
    assert ok("5 m²")["dimension_str"] == "L^2"
    assert ok("5 m²")["si_value"] == pytest.approx(5.0)
    assert ok("2×3")["si_value"] == pytest.approx(6.0)
    assert ok("6÷2")["si_value"] == pytest.approx(3.0)
    assert ok("1 kg·m/s²")["dimension_str"] == "L·M·T^-2"
    assert ok("10 m⁻¹")["dimension_str"] == "L^-1"


def test_constants():
    assert ok("c")["si_value"] == pytest.approx(299792458.0)
    assert ok("c")["dimension_str"] == "L·T^-1"
    assert ok("kb * 300 K")["dimension_str"] == "L^2·M·T^-2"


def test_dimensionless_and_percent():
    r = ok("50%")
    assert r["si_value"] == pytest.approx(0.5)
    assert r["dimension_str"] == "无量纲"


# ---------------- 错误定位 ----------------

def test_dim_mismatch_points_to_right_operand():
    errs = fail("1 m + 1 s")
    e = errs[0]
    assert e["kind"] == "dim_mismatch"
    assert e["source"] == "expression"
    assert "1 m + 1 s"[e["start"]:e["end"]] == "1 s"
    assert "L" in e["message"] and "T" in e["message"]


def test_dim_mismatch_in_subtraction_with_units():
    errs = fail("3 kg - 2 m")
    assert errs[0]["kind"] == "dim_mismatch"
    assert "3 kg - 2 m"[errs[0]["start"]:errs[0]["end"]] == "2 m"


def test_bad_exponent_points_to_exponent():
    errs = fail("(1 m)^(1 s)")
    e = errs[0]
    assert e["kind"] == "bad_exponent"
    assert "(1 m)^(1 s)"[e["start"]:e["end"]] == "(1 s)"
    assert "无量纲" in e["message"]


def test_bad_exponent_via_variable():
    errs = fail("2^t", {"t": "3 s"})
    assert any(e["kind"] == "bad_exponent" for e in errs)


def test_undefined_variable_span():
    errs = fail("foo + 1")
    e = errs[0]
    assert e["kind"] == "undefined"
    assert "foo + 1"[e["start"]:e["end"]] == "foo"
    assert "foo" in e["message"]


def test_undefined_inside_variable_definition():
    errs = fail("a * 2", {"a": "zz"})
    kinds = {e["kind"] for e in errs}
    assert "undefined" in kinds
    var_err = next(e for e in errs if e["source"] == "var:a")
    assert "zz"[var_err["start"]:var_err["end"]] == "zz"


def test_variable_cycle_detected():
    errs = fail("a", {"a": "b", "b": "a"})
    assert any(e["kind"] == "var_cycle" for e in errs)


def test_trig_requires_dimensionless():
    errs = fail("sin(1 m)")
    e = errs[0]
    assert e["kind"] == "dimless_arg"
    assert "sin(1 m)"[e["start"]:e["end"]] == "1 m"


def test_division_by_zero():
    errs = fail("1 / 0")
    assert errs[0]["kind"] == "value"


def test_syntax_error_span():
    errs = fail("1 + * 2")
    assert errs[0]["kind"] == "syntax"


def test_empty_expression():
    errs = fail("")
    assert errs[0]["kind"] == "syntax"


def test_function_arity():
    errs = fail("sqrt(1, 2)")
    assert errs[0]["kind"] == "arity"


# ---------------- 目标单位换算 ----------------

def test_target_unit_conversion():
    r = ok("10 m/s", target="km/h")
    assert r["target"]["value"] == pytest.approx(36.0)
    assert r["target"]["unit"] == "km/h"


def test_target_unit_energy():
    r = ok("0.5*mass*v^2", {"mass": "2 kg", "v": "10 m/s"}, "kJ")
    assert r["target"]["value"] == pytest.approx(0.1)


def test_target_unit_dim_mismatch():
    errs = fail("10 m/s", target="kg")
    e = errs[0]
    assert e["kind"] == "target_dim"
    assert e["source"] == "target"


def test_target_unit_unknown():
    errs = fail("10 m/s", target="furlong")
    assert errs[0]["source"] == "target"
    assert errs[0]["kind"] == "undefined"


# ---------------- 变量进阶 ----------------

def test_variables_can_reference_each_other():
    r = ok("F / A", {
        "mass": "80 kg",
        "F": "mass * g0",
        "A": "0.05 m^2",
    })
    assert r["dimension_str"] == "L^-1·M·T^-2"
    assert "Pa" in r["derived"]
    assert r["si_value"] == pytest.approx(80 * 9.80665 / 0.05)


def test_variable_shadows_unit_with_warning():
    # 用户变量优先于同名单位，并返回遮蔽警告
    res = analyze("min + 1", {"min": "2"})
    assert res["ok"]
    assert res["result"]["si_value"] == pytest.approx(3.0)
    assert any(w["name"] == "min" and w["kind"] == "shadow_unit" for w in res["warnings"])


def test_fractional_dimension_via_sqrt():
    r = ok("sqrt(2 m)")
    assert r["dimension"] == ["1/2", "0", "0", "0", "0", "0", "0"]


# ---------------- 测量不确定度传播 ----------------

VARS = {"mass": "2 kg", "v": "10 m/s", "h0": "5 m"}
UNCS = {"mass": "0.01 kg", "v": "0.2 m/s", "h0": "0.05 m"}


def test_uncertainty_rss_sum_of_squares():
    # E = 0.5 m v^2 + m g0 h0
    # dE/dm = 0.5v^2 + g0 h0 = 99.03325 ; dE/dv = m v = 20 ; dE/dh = m g0 = 19.6133
    r = ok("0.5*mass*v^2 + mass*g0*h0", VARS, uncertainties=UNCS)
    u = r["uncertainty"]
    expected = math.sqrt((99.03325 * 0.01) ** 2 + (20 * 0.2) ** 2 + (19.6133 * 0.05) ** 2)
    assert u["si_value"] == pytest.approx(expected, rel=1e-8)
    assert u["si_unit"] == r["si_unit"]
    assert u["relative"] == pytest.approx(expected / r["si_value"])
    assert u["step_factor"] == UNCERTAINTY_STEP
    by_name = {s["name"]: s for s in u["sources"]}
    assert by_name["v"]["contribution_si"] == pytest.approx(4.0, rel=1e-8)
    assert by_name["mass"]["contribution_si"] == pytest.approx(0.9903325, rel=1e-8)
    # 平方和占比
    assert by_name["v"]["contribution_si"] <= u["si_value"]


def test_uncertainty_zero_nominal_uses_valid_step():
    # 标称值为 0 时必须以不确定度为尺度取有效步长，不能得到 0
    r = ok("a + b", {"a": "0 m", "b": "3 m"}, uncertainties={"a": "1 mm", "b": "0 m"})
    u = r["uncertainty"]
    assert u["si_value"] == pytest.approx(0.001, abs=1e-10)
    assert [s["name"] for s in u["sources"]] == ["a"]  # 零不确定度不构成扰动源
    assert u["sources"][0]["step_si"] > 0


def test_uncertainty_nominal_zero_gives_null_relative():
    r = ok("x", {"x": "0 m"}, uncertainties={"x": "0.1 m"})
    assert r["uncertainty"]["relative"] is None
    assert r["uncertainty"]["si_value"] == pytest.approx(0.1, abs=1e-12)


def test_uncertainty_absent_without_inputs():
    r = ok("1 m + 2 m")
    assert "uncertainty" not in r
    # 全部填零：有块可算、数值为零、无扰动源
    r = ok("x", {"x": "5 m"}, uncertainties={"x": "0 m"})
    assert r["uncertainty"]["si_value"] == 0
    assert r["uncertainty"]["sources"] == []


def test_uncertainty_propagates_through_variable_chain():
    variables = {"mass": "80 kg", "F": "mass * g0", "A": "0.05 m^2"}
    r = ok("F / A", variables, uncertainties={"mass": "0.5 kg"})
    u = r["uncertainty"]
    # u(F) = 0.5 * g0；u(P) = u(F) / A
    assert u["si_value"] == pytest.approx(0.5 * 9.80665 / 0.05, rel=1e-8)
    by_name = {v["name"]: v for v in u["variables"]}
    assert by_name["F"]["uncertainty_si"] == pytest.approx(0.5 * 9.80665, rel=1e-8)
    assert by_name["mass"]["uncertainty_si"] == pytest.approx(0.5)
    assert by_name["A"]["uncertainty_si"] == 0.0


def test_uncertainty_converts_with_target_unit():
    r = ok("0.5*mass*v^2", {"mass": "2 kg", "v": "10 m/s"}, "kJ",
           uncertainties={"v": "1 m/s"})
    assert r["uncertainty"]["si_value"] == pytest.approx(20.0)
    assert r["target"]["uncertainty"] == pytest.approx(0.02)
    assert r["target"]["value"] == pytest.approx(0.1)


def test_uncertainty_chain_sources_and_unused_variables():
    r = ok("2*a", {"a": "3 m", "unused": "1 m"},
           uncertainties={"a": "0.1 m", "unused": "0.5 m"})
    assert r["uncertainty"]["si_value"] == pytest.approx(0.2, abs=1e-10)
    assert [v["name"] for v in r["uncertainty"]["variables"]] == ["a", "unused"]


def test_uncertainty_functions_and_powers():
    r = ok("sin(theta)", {"theta": "pi/3"}, uncertainties={"theta": "0.01"})
    assert r["uncertainty"]["si_value"] == pytest.approx(0.01 * 0.5, abs=1e-9)
    r = ok("x^3", {"x": "2 m"}, uncertainties={"x": "1 mm"})
    assert r["uncertainty"]["si_value"] == pytest.approx(12e-3, rel=1e-8)
    r = ok("sqrt(x)", {"x": "4 m^2"}, uncertainties={"x": "0.01 m^2"})
    assert r["uncertainty"]["si_value"] == pytest.approx(0.0025, rel=1e-8)


def test_uncertainty_accepts_unit_expression():
    r = ok("x", {"x": "10 m"}, uncertainties={"x": "2 cm + 3 mm"})
    assert r["uncertainty"]["si_value"] == pytest.approx(0.023, abs=1e-11)


def test_uncertainty_large_nominal_small_uncertainty():
    # 步长不得因相对值过小而失效
    r = ok("x", {"x": "1.23456789e12 m"}, uncertainties={"x": "1 um"})
    assert r["uncertainty"]["si_value"] == pytest.approx(1e-6, abs=1e-12)


def test_uncertainty_dimension_mismatch_rejected():
    errs = fail("x", {"x": "5 m"}, uncertainties={"x": "2 s"})
    e = errs[0]
    assert e["kind"] == "uncertainty_dim"
    assert e["source"] == "unc:x"


def test_uncertainty_negative_rejected():
    errs = fail("x", {"x": "5 m"}, uncertainties={"x": "-1 m"})
    assert errs[0]["kind"] == "uncertainty_value"
    assert errs[0]["source"] == "unc:x"


def test_uncertainty_for_unknown_variable_rejected():
    errs = fail("x", {"x": "5 m"}, uncertainties={"y": "1 m"})
    assert errs[0]["kind"] == "uncertainty_unknown"
    assert errs[0]["source"] == "unc:y"


def test_uncertainty_bad_syntax_points_to_uncertainty():
    errs = fail("x", {"x": "5 m"}, uncertainties={"x": "2 +"})
    assert errs[0]["source"] == "unc:x"
    errs = fail("x", {"x": "5 m"}, uncertainties={"x": "1 foo"})
    assert errs[0]["source"] == "unc:x"
    assert errs[0]["kind"] == "undefined"


def test_uncertainty_not_summarized_when_perturbation_leaves_domain():
    # sqrt(0)：负方向取到负数，应指出对应输入并停止汇总
    errs = fail("sqrt(a)", {"a": "0 m^2"}, uncertainties={"a": "1 m^2"})
    e = errs[0]
    assert e["kind"] == "uncertainty_eval"
    assert e["source"] == "unc:a"
    assert "sqrt" in e["message"]
    # 单侧越界也要停止：asin(1) 正方向 > 1
    errs = fail("asin(x)", {"x": "1"}, uncertainties={"x": "0.01"})
    assert errs[0]["kind"] == "uncertainty_eval"


def test_uncertainty_failure_inside_variable_chain_reports_root_cause():
    # 链上节点 sqrt(a*b) 在 a 负偏移时越界
    errs = fail("sqrt(p)", {"a": "0 m", "b": "3 m", "p": "a*b"},
                uncertainties={"a": "1 m", "b": "0.1 m"})
    assert errs[0]["kind"] == "uncertainty_eval"
    assert errs[0]["source"] == "unc:a"
    assert "sqrt" in errs[0]["message"]


def test_uncertainty_not_summarized_when_nominal_invalid():
    res = analyze("x + 1 s", {"x": "1 m"}, uncertainties={"x": "0.1 m"})
    assert not res["ok"]
    assert res["result"] is None  # 不能返回貌似有效的数值


def test_uncertainty_skipped_when_variable_chain_broken():
    errs = fail("a", {"a": "zz"}, uncertainties={"a": "1 m"})
    assert any(e["source"] == "var:a" for e in errs)
    assert all(e["kind"] != "uncertainty_dim" for e in errs)


def test_uncertainty_independent_input_warning():
    res = analyze("F", {"mass": "80 kg", "F": "mass*g0"},
                  uncertainties={"F": "1 N"})
    assert res["ok"]
    assert any(w["kind"] == "uncertainty_independent" and w["name"] == "F"
               for w in res["warnings"])
    # 作为独立扰动源，贡献就是 1 N
    assert res["result"]["uncertainty"]["si_value"] == pytest.approx(1.0)


def test_uncertainty_target_dim_mismatch_keeps_si_uncertainty():
    res = analyze("x", {"x": "5 m"}, "kg", uncertainties={"x": "0.1 m"})
    assert not res["ok"]
    assert res["result"]["target"] is None
    assert res["result"]["uncertainty"]["si_value"] == pytest.approx(0.1)
