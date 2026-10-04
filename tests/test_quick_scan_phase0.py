"""Phase 0 验收测试：置信层 + 门禁 + 默认人力策略（决策A①③ / 决策B）。

运行：uv run pytest tests/test_quick_scan_phase0.py
（pyproject 已设 pythonpath=src，可直接 import tools / router）
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.workflow_engine import quick_scan, compare_scenarios, trend_projection
from router.formatter import format_response


def _scan(json_str):
    return json.loads(quick_scan.invoke({"params_json": json_str}))


def test_rent_plus_revenue_missing_vc_is_blocked():
    """租金+营收 但未给变动成本率 → 利润/保本「还不能定」：月利润为 None + 缺口号，不产假硬数。"""
    d = _scan('{"monthly_rent": 8000, "monthly_revenue": 50000}')
    assert d.get("insufficient") is not True  # 基础参数够，仍渲染（不全盘退回骨架）
    assert d["core_metrics"]["monthly_profit"] is None
    assert d["param_sources"]["variable_cost_ratio"].startswith("[Missing]")
    # 缺口信息应出现在假设清单/来源标注（替代旧 skeleton.gaps）
    kinds = {a["field"]: a["kind"] for a in d["assumptions"]}
    assert kinds.get("variable_cost_ratio") == "Missing"
    md = format_response("quick_scan", d)
    assert "monthly loss" not in md.lower()
    assert "not settled yet" in md.lower() or "variable cost ratio" in md.lower()  # 明确提示补 vc，而非假硬数


def test_full_case_no_regression():
    """完整餐饮案例 → 正常计算，无崩溃，人工为用户值，跑道非未知。"""
    d = _scan('{"industry":"餐饮","monthly_rent":15000,"employee_count":3,'
              '"avg_salary":7000,"variable_cost_ratio":0.55,'
              '"daily_traffic":50,"price_per_unit":25,"total_investment":300000}')
    assert d.get("insufficient") is not True
    assert d["params"]["available_cash"] is not None
    assert d["param_sources"]["employee_count"].startswith("[User]")
    assert "error" not in d
    assert d["core_metrics"]["runway_months"] != "Unknown"


# ─── compare_scenarios 防崩溃回归（2026-08-17）────────────────────────────

def _cmp(base_json, alt_json):
    return json.loads(compare_scenarios.invoke({"base_json": base_json, "alt_json": alt_json}))


def test_compare_full_params_succeeds():
    """补全变动成本率后 compare 正常出对比表（回归护栏）。"""
    base = '{"monthly_rent":15000,"daily_traffic":50,"price_per_unit":25,"employee_count":3,"avg_salary":5000,"variable_cost_ratio":0.4}'
    alt = '{"monthly_rent":8000,"daily_traffic":50,"price_per_unit":25,"employee_count":3,"avg_salary":5000,"variable_cost_ratio":0.4}'
    d = _cmp(base, alt)
    assert d.get("insufficient") is not True
    assert "diff" in d and "profit" in d["diff"]
    assert d["base_scenario"]["monthly_profit"] is not None
    assert "error" not in d


# ─── trend 统一降级（M1，2026-08-19）──────────────────────────────────────

def _trend(pj):
    return json.loads(trend_projection.invoke({"params_json": pj}))


def test_trend_full_params_succeeds():
    """补全变动成本率与固定成本后 trend 正常输出 12 个月趋势（回归护栏）。"""
    pj = ('{"monthly_rent":15000,"daily_traffic":50,"price_per_unit":25,'
          '"employee_count":3,"avg_salary":5000,"variable_cost_ratio":0.4}')
    d = _trend(pj)
    assert d.get("insufficient") is not True
    assert "months" in d and len(d["months"]) == 12
    assert "summary" in d
    assert "error" not in d


# ─── 独立运行入口（无需 pytest）──────────────────────────────────────────

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa
            print(f"ERROR {t.__name__}: {e}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
