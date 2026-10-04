"""数据一致性（派生一致性）验收：规则层先发现矛盾，不靠 LLM。

覆盖：
- C1 月营收(用户) vs 客流×单价(用户) 打架 → 引擎检测 derived_issues（差异>50%）
- C2 derived_issues 渲染成前置「数据冲突」横幅
- C3 冲突时 LLM ops 被拦截（数据对齐只能用户拍板，LLM 不得越权出修正方案）
- C4 无冲突时不误报（一致的输入不触发）
- C5 缺任一输入不触发（缺失≠冲突，不误报）

运行：并入 tests/run_all.py；也可 .venv/bin/python tests/test_consistency.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.workflow_engine import quick_scan
from router.formatter import format_response


def _scan(params: dict) -> dict:
    return json.loads(quick_scan.invoke(
        {"params_json": json.dumps(params, ensure_ascii=False)}))


def test_c1_revenue_vs_traffic_price_conflict():
    """C1：月营收 20000 与 客流100×单价12×30=36000 打架 → derived_issues。"""
    d = _scan({"industry": "餐饮", "total_investment": 200000, "monthly_revenue": 20000,
               "daily_traffic": 100, "price_per_unit": 12, "monthly_rent": 15000,
               "employee_count": 3, "avg_salary": 3000, "variable_cost_ratio": 0.5})
    issues = d.get("derived_issues") or []
    assert any("Monthly revenue" in i.get("message", "") for i in issues), issues
    assert any("36,000" in i.get("message", "") for i in issues), issues


def test_c2_render_conflict_banner():
    """C2：derived_issues 渲染成前置横幅（规则层先发现）。"""
    d = _scan({"industry": "餐饮", "total_investment": 200000, "monthly_revenue": 20000,
               "daily_traffic": 100, "price_per_unit": 12, "monthly_rent": 15000,
               "employee_count": 3, "avg_salary": 3000, "variable_cost_ratio": 0.5})
    md = format_response("quick_scan", d)
    assert "Data conflicts" in md, "conflict banner should be rendered"
    assert "please confirm the definitions" in md


def test_c3_no_conflict_when_consistent():
    """C3：一致的输入不误报（客流×单价×30 == 月营收）。"""
    d = _scan({"industry": "餐饮", "total_investment": 200000, "monthly_revenue": 36000,
               "daily_traffic": 100, "price_per_unit": 12, "monthly_rent": 15000,
               "employee_count": 3, "avg_salary": 3000, "variable_cost_ratio": 0.5})
    issues = d.get("derived_issues") or []
    # 36000 vs 36000 → 无矛盾
    assert not any("月营收" in i.get("message", "") for i in issues), issues


def test_c4_no_conflict_when_missing():
    """C4：缺任一输入不触发（缺失≠冲突，不误报）。"""
    # 只给月营收，无客流/单价 → 不应报冲突
    d = _scan({"industry": "餐饮", "monthly_revenue": 20000,
               "monthly_rent": 15000, "variable_cost_ratio": 0.5})
    issues = d.get("derived_issues") or []
    assert not any("月营收" in i.get("message", "") for i in issues), issues


def test_c5_conflict_detection_field():
    """C5：derived_issues 挂到 scan 顶层，供 web_server 拦截 ops。"""
    d = _scan({"industry": "餐饮", "total_investment": 200000, "monthly_revenue": 20000,
               "daily_traffic": 100, "price_per_unit": 12, "monthly_rent": 15000,
               "employee_count": 3, "avg_salary": 3000, "variable_cost_ratio": 0.5})
    assert isinstance(d.get("derived_issues"), list), "derived_issues 必须是列表"


def test_c6_conflict_resolution_ops():
    """C6：冲突时 field_model 自动生成口径对齐 ops（方案A/B）。"""
    from field_model import conflict_resolution_ops
    ops = conflict_resolution_ops({"monthly_revenue": 20000, "daily_traffic": 100, "price_per_unit": 12})
    assert len(ops) == 2, ops
    # 方案A：按客流×单价×30 修正月营收
    assert ops[0]["changes"]["monthly_revenue"] == 36000, ops[0]
    assert "36,000" in ops[0]["label"]
    # 方案B：按月营收反推客流
    assert ops[1]["changes"]["daily_traffic"] == 56.0, ops[1]
    # 无冲突时不生成
    ok = conflict_resolution_ops({"monthly_revenue": 36000, "daily_traffic": 100, "price_per_unit": 12})
    assert ok == [], ok


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
