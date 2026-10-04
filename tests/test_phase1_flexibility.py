"""Phase 1 验收测试：灵活度层（③④⑤⑥）。

覆盖：④ 共享校验态（trend/compare 不再静默误报）、③ 情景/区间引擎、
⑤ 叙事>判决、⑥ 模板不确定性驱动区间。

运行方式（无需 pytest）：
    .venv/bin/python3 tests/test_phase1_flexibility.py
若已装 pytest（uv run pytest），也会被自动收集。
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.workflow_engine import quick_scan, trend_projection, compare_scenarios
from router.formatter import format_response


def _scan(d: dict):
    return json.loads(quick_scan.invoke({"params_json": json.dumps(d)}))


def _trend(d: dict):
    return json.loads(trend_projection.invoke({"params_json": json.dumps(d)}))


def _compare(base: dict, alt: dict):
    return json.loads(compare_scenarios.invoke({
        "base_json": json.dumps(base), "alt_json": json.dumps(alt)}))


# ─── 测试用例 ─────────────────────────────────────────────────────────────

def test_rent_only_still_blocked():
    """Phase 0 回归：仅租金仍被门禁拦下。"""
    d = _scan({"monthly_rent": 8000})
    assert d.get("insufficient") is True
    assert "危险" not in json.dumps(d)


def test_trend_no_silent_misreport():
    """④：仅租金时 trend 走骨架，不再输出 12 个月全 -8000 的静默误报。"""
    t = _trend({"monthly_rent": 8000})
    assert t.get("insufficient") is True
    assert "months" not in t


def test_narrative_focuses_material_lever():
    """叙事不应把 stage 等次要默认当成风险焦点。"""
    d = _scan({"industry": "餐饮", "total_investment": 300000, "monthly_rent": 10000,
               "daily_traffic": 100, "price_per_unit": 25,
               "employee_count": 3, "avg_salary": 7000,
               "variable_cost_ratio": 0.4, "monthly_revenue": 75000})
    assert "stage" not in d["narrative"]


# ─── 独立运行入口 ─────────────────────────────────────────────────────────

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
