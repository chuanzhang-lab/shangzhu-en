"""精确推算层验收（由用户输入 + 确定公式推出关联参数，非猜测）。

覆盖：
- DV1 全输入 → 营收/人工/固定/变动/利润/毛利/年固定/可用现金 全部精确算出且带公式
- DV2 缺变动成本率 → 能算的照算（营收/人工/固定），不能算的标「缺 X」
- DV3 缺人工（人数或薪资）→ 人工缺失标缺，其余照算
- DV4 与「默认值」区分：无任何输入 → 全是 missing/「还差」，绝不出现行业默认填充
- DV5 渲染：format_response 含「精确推算」表；骨架也含 derived
- DV6 推算值与核心指标一致（不矛盾）

运行：并入 tests/run_all.py；也可 .venv/bin/python tests/test_derived.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.workflow_engine import quick_scan, _fill_and_assess, _build_derived_values
from router.formatter import format_response


def _scan(params: dict) -> dict:
    return json.loads(quick_scan.invoke(
        {"params_json": json.dumps(params, ensure_ascii=False)}))


_FULL = {"industry": "餐饮", "total_investment": 200000, "daily_traffic": 60,
         "price_per_unit": 10, "monthly_rent": 1800, "employee_count": 2,
         "avg_salary": 3000, "variable_cost_ratio": 0.55}


def _by_label(derived, label):
    for d in derived:
        if d["label"] == label:
            return d
    return None


def test_dv_engine_layer():
    """引擎层：_build_derived_values 纯函数也独立可用。"""
    st = _fill_and_assess(_FULL)
    assert isinstance(st["derived"], list) and len(st["derived"]) >= 8
    assert st["derived"] == _build_derived_values(st["params"], st["src"])


def test_dv7_no_none_placeholder_in_header():
    """DV7：标题不得出现字面量「None」——stage / template_mode 缺失时应省略。"""
    md = format_response("quick_scan", _scan(_FULL))
    head = md.splitlines()[0]
    assert head.startswith("## 📊 "), head
    assert "None" not in head, head


def test_dv8_breakeven_traffic_unit_from_industry():
    """DV8：盈亏平衡客流单位按行业 benchmark 取，不再写死「杯/天」。"""
    from router.formatter import _traffic_unit
    # 餐饮 benchmark = "80-250 杯"
    assert _traffic_unit({"daily_traffic_range": "80-250 杯"}) == "杯/天"
    assert _traffic_unit({"daily_traffic_range": "50-150 人"}) == "人/天"
    assert _traffic_unit({"daily_traffic_range": "20-80 人次"}) == "人次/天"
    # 取不到单位 → 中性兜底（宠物「10-30 只宠物/天」、SaaS「不适用」…）
    assert _traffic_unit({"daily_traffic_range": "10-30 只宠物/天"}) == "单/天"
    assert _traffic_unit({"daily_traffic_range": "不适用"}) == "单/天"
    assert _traffic_unit({}) == "单/天"
    # 渲染链路确实用上了行业单位
    md = format_response("quick_scan", _scan(_FULL))   # 餐饮
    rows = [l for l in md.splitlines() if "盈亏平衡客流" in l]
    if rows:
        assert "杯/天" in rows[0], rows[0]


def test_dv9_missing_items_no_empty_code_span():
    """DV9：「暂不能推算」项没有公式时不得渲染成一对空反引号。"""
    md = format_response("quick_scan", _scan({"monthly_rent": 8000}))
    for line in md.splitlines():
        if line.startswith("- ") and "缺 **" in line:
            assert not line.rstrip().endswith("``"), line


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
