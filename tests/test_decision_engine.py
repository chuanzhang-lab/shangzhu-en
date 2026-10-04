"""Phase 1 · L2 决策引擎验收（验证期决策工作台）。

覆盖（决策输出规范 4.3 / D5-D9）：
- D1 决策类型路由（turnaround / go_no_go / continue_stop / runway / validate_first / choose）
- D5 决策输出无倾向（结构纯客观；forbidden_tone_scan 守护 LLM 倾向词）
- D6 缺关键事实 → 「还不能定」+ 该补什么（G6）
- D7 输出是「选项+风险+验证实验」，不是答案
- D8 两样板优先：怎么扭亏 + 先验证什么
- D9 选项排序用规则/配置（decision_policy.yaml），非 LLM
- G4 怎么扭亏 → 2-4 个互斥选项，每个含改什么+引擎回算+风险
- G5 先验证什么 → 一件事 + 最小实验设计（一次性，无跟踪）

运行：并入 tests/run_all.py；也可 .venv/bin/python tests/test_decision_engine.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.workflow_engine import quick_scan
from tools import param_advisor
from decision_engine import (
    decide, render_decision, build_evidence, policy,
    normalize_type, forbidden_tone_scan,
)
from router.intent import decide_type_of


def _scan(params: dict) -> dict:
    return json.loads(quick_scan.invoke({"params_json": json.dumps(params, ensure_ascii=False)}))


def _suggest(params: dict):
    return json.loads(param_advisor.suggest_params.invoke(
        {"params_json": json.dumps(params, ensure_ascii=False)}))


_FULL = {"industry": "餐饮", "total_investment": 200000, "price_per_unit": 10,
         "daily_traffic": 60, "monthly_rent": 1800, "employee_count": 2,
         "avg_salary": 3000, "variable_cost_ratio": 0.55}


def test_type_normalize():
    assert normalize_type("turnaround") == "turnaround"
    assert normalize_type("GO_NO_GO") == "go_no_go"
    assert normalize_type("不存在的") == "turnaround"


def test_g4_turnaround_options_mutual_exclusive():
    """G4/D7：怎么扭亏 → 2-4 互斥可执行选项，每个含引擎回算结果。"""
    d = _scan(_FULL)
    res = decide("turnaround", d, d.get("basis"), _FULL, _suggest(_FULL))
    assert res["confidence"] == "full_user", res["confidence"]
    opts = res["options"]
    assert 2 <= len(opts) <= 4, f"选项应 2-4 个，实际 {len(opts)}"
    fields = [tuple(sorted(o["changes"].keys())) for o in opts]
    # 互斥：无重复字段
    assert len(set(fields)) == len(fields), "选项应互斥（不同杠杆）"
    for o in opts:
        assert isinstance(o["_delta"], (int, float)), o
        assert "reversible" in o
    assert res["recommended_first_validation"]["what"]


def test_g6_missing_vc_insufficient():
    """G6：缺变动成本率 → 「还不能定」+ 该补什么。"""
    p = {"industry": "餐饮", "total_investment": 200000, "price_per_unit": 10,
         "daily_traffic": 60, "monthly_rent": 1800, "employee_count": 2,
         "avg_salary": 3000}
    d = _scan(p)
    res = decide("turnaround", d, d.get("basis"), p, None)
    assert res["confidence"] == "insufficient"
    assert "变动成本率" in res.get("gaps", [])
    md = render_decision(res)
    assert "还不能定" in md


def test_forbidden_tone_scan():
    """D5 硬守卫：LLM 输出命中倾向词被检出。"""
    assert forbid_has("建议你先把月租谈下来")
    assert forbid_has("你应该立刻提价")
    assert not forbid_has("在现有数据下月利润 -2100 元")


def test_d9_ranking_uses_policy():
    """D9：选项排序基于 decision_policy.yaml（可配置、非 LLM）。"""
    rp = policy()["ranking"]
    assert "price_per_unit" in rp["lever_weights"]
    assert "monthly_rent" in rp["reversibility"]
    assert rp["validation_cost"]["price_per_unit"] < rp["validation_cost"]["monthly_rent"]


def test_build_evidence_basis_exposed():
    """证据包含数据基础，供决策层区分用户事实/缺失/假设。"""
    d = _scan(_FULL)
    ev = build_evidence(d, d.get("basis"))
    assert ev["metrics"]["monthly_profit"] is not None
    assert ev["basis"].get("variable_cost_ratio") == "user"
    assert ev["metrics"]["runway_months"] is not None


def test_render_no_tendency_words():
    """D5：整段决策渲染后仍无倾向/命令词。"""
    d = _scan(_FULL)
    res = decide("turnaround", d, d.get("basis"), _FULL, _suggest(_FULL))
    md = render_decision(res)
    for bad in policy()["output"]["forbidden_tone_words"]:
        assert bad not in md, f"渲染结果含倾向词 {bad}"


def forbid_has(text: str) -> bool:
    return bool(forbidden_tone_scan(text))


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
