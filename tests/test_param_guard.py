"""参数守门层测试 — 从根上断绝「6000%」「3500人」类失真。

覆盖：
- 比例归一化（60 → 0.6，6000% → 60 → 标记）
- 硬边界拦截（employee_count > 200）
- 软边界警告
- 历史矛盾检测
- 全链路集成（抽取 → 合并 → 引擎）
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from param_guard import (
    validate_field,
    validate_params,
    guard_extracted,
    guard_merge,
    normalize_value,
    LEVEL_CRITICAL,
    LEVEL_WARNING,
    LEVEL_CONTRADICTION,
)
from router.param_extractor import extract_params
from session_state import apply_turn_guarded, reset_state, get_state
from tools.workflow_engine import quick_scan


def _scan(params_dict):
    return json.loads(quick_scan.invoke({"params_json": json.dumps(params_dict, ensure_ascii=False)}))


# ── 归一化 ────────────────────────────────────────────────────────────────

def test_normalize_percent_60():
    v, note = normalize_value("variable_cost_ratio", 60)
    assert abs(v - 0.6) < 1e-9, v
    assert note is not None


def test_normalize_percent_0_6():
    v, note = normalize_value("variable_cost_ratio", 0.6)
    assert abs(v - 0.6) < 1e-9, v
    assert note is None


def test_normalize_percent_6000():
    v, note = normalize_value("variable_cost_ratio", 6000)
    assert abs(v - 60.0) < 1e-9, v  # 6000% → 60，仍超界交校验标记


def test_normalize_rate_field_keeps_absurd():
    """百分比字段（上限100）不归一化 6000，保留原值交硬边界拦截。"""
    v, note = normalize_value("variable_cost_rate", 6000)
    assert v == 6000, v  # 保留 → validate_field 按 >100 标 CRITICAL


def test_normalize_plain_field_unchanged():
    v, note = normalize_value("monthly_rent", 1500)
    assert v == 1500 and note is None


# ── 单字段校验 ────────────────────────────────────────────────────────────

def test_validate_ratio_6000_critical():
    r = validate_field("variable_cost_ratio", 60.0)  # 归一化后仍 60 > 1
    assert r["level"] == LEVEL_CRITICAL, r
    assert r["needs_confirmation"] is True
    assert r["auto_fix"] is not None and abs(r["auto_fix"] - 0.6) < 1e-9, r


def test_validate_rate_6000_critical_autofix():
    """百分比字段 6000 > 100：自动修正 6000/100=60 并标记确认（不丢弃，防落 chitchat）。"""
    r = validate_field("variable_cost_rate", 6000)
    assert r["level"] == LEVEL_CRITICAL, r
    assert r["needs_confirmation"] is True
    assert r["auto_fix"] is not None and abs(r["auto_fix"] - 60.0) < 1e-9, r
    assert "confirm" in r["message"]


def test_validate_ratio_0_6_ok():
    r = validate_field("variable_cost_ratio", 0.6)
    assert r["level"] == "ok", r


def test_validate_employee_3500_critical():
    r = validate_field("employee_count", 3500)
    assert r["level"] == LEVEL_CRITICAL, r
    assert "200" in r["message"]


def test_validate_employee_200_warning():
    r = validate_field("employee_count", 200)
    # 200 在硬边界内但超软边界
    assert r["level"] == LEVEL_WARNING, r


def test_validate_salary_reasonable():
    r = validate_field("avg_salary", 3500)
    assert r["level"] == "ok", r


# ── 批量校验 + 矛盾检测 ───────────────────────────────────────────────────

def test_validate_params_normalize_and_clean():
    params = {"variable_cost_ratio": 60, "monthly_rent": 1500}
    res = validate_params(params)
    assert abs(res["cleaned"]["variable_cost_ratio"] - 0.6) < 1e-9
    assert res["cleaned"]["monthly_rent"] == 1500


def test_contradiction_detection_ratio():
    new = {"variable_cost_ratio": 0.6}
    history = {"variable_cost_ratio": 0.06}  # 历史 6%，本次 60% → 矛盾
    res = validate_params(new, history_params=history)
    assert len(res["contradictions"]) == 1
    assert "typo" in res["contradictions"][0]["message"]


def test_contradiction_detection_big_jump():
    new = {"monthly_revenue": 500000}
    history = {"monthly_revenue": 20000}
    res = validate_params(new, history_params=history)
    assert len(res["contradictions"]) == 1


def test_no_contradiction_similar():
    new = {"monthly_revenue": 21000}
    history = {"monthly_revenue": 20000}
    res = validate_params(new, history_params=history)
    assert len(res["contradictions"]) == 0


# ── 抽取层集成 ────────────────────────────────────────────────────────────

def test_extract_6000_percent_flagged():
    p = extract_params("variable cost rate 6000%, monthly rent 1500")
    assert "_guard" in p, p
    assert p["_guard"]["has_critical"] or p["_guard"]["needs_confirmation"], p["_guard"]


def test_extract_60_percent_normalized():
    p = extract_params("variable cost rate 60%, monthly rent 1500")
    assert abs(p.get("variable_cost_ratio", 0) - 0.6) < 1e-9, p


# ── 会话层守门 ────────────────────────────────────────────────────────────

def test_apply_turn_guarded_detects_contradiction():
    tid = "guard-contra"
    reset_state(tid)
    # 第一轮：0.6
    apply_turn_guarded(tid, {"variable_cost_ratio": 0.6}, "变动成本率60%", "餐饮")
    # 第二轮：6000% → 归一化 60 → 硬界 CRITICAL 自动修正 0.6，与历史一致 → 无矛盾但标记待确认
    st, guard = apply_turn_guarded(tid, {"variable_cost_ratio": 6000}, "变动成本率6000%", None)
    assert guard["has_critical"], guard
    assert "variable_cost_ratio" in guard["needs_confirmation"]
    # 自动修正后与历史一致（0.6），不产生矛盾，但已标记「请确认」
    assert abs(st["params"]["variable_cost_ratio"] - 0.6) < 1e-9


def test_apply_turn_guarded_employee_critical():
    tid = "guard-emp"
    reset_state(tid)
    st, guard = apply_turn_guarded(tid, {"employee_count": 3500}, "人工3500人", "餐饮")
    assert "employee_count" in guard["needs_confirmation"]
    # 被拦截或标记
    assert guard["has_critical"] or guard["needs_confirmation"]


# ── 引擎层集成 ────────────────────────────────────────────────────────────

def test_engine_rejects_absurd_ratio():
    # 直接喂引擎 60（即 6000%），引擎守门归一化 60→0.6，不产出荒谬结果
    d = _scan({"industry": "餐饮", "monthly_rent": 1500, "daily_traffic": 50,
               "price_per_unit": 15, "variable_cost_ratio": 60})
    assert not d.get("error"), d
    vc = d["params"].get("variable_cost_ratio")
    # 出口是数值契约（0~1），不是展示串；百分比渲染归前端
    assert isinstance(vc, (int, float)) and abs(vc - 0.6) < 1e-9, vc
    profit = d.get("core_metrics", {}).get("monthly_profit")
    assert profit is not None and abs(profit) < 1e6, f"利润仍失真: {profit}"


def test_full_chain_6000_percent():
    """完整链路：用户说「变动成本率6000%」→ 抽取标记 + 矛盾 + 引擎护栏。"""
    tid = "guard-full"
    reset_state(tid)
    p = extract_params("Open a soup shop, monthly rent 1500, 50 cups a day, price per cup 15, variable cost rate 6000%")
    assert "_guard" in p, p
    st, guard = apply_turn_guarded(tid, p, "variable cost rate 6000%", p.get("industry"))
    assert guard["needs_confirmation"] or guard["contradictions"], guard
    d = _scan(st["params"])
    # 不应出现荒谬利润
    profit = d.get("core_metrics", {}).get("monthly_profit")
    if profit is not None:
        assert abs(profit) < 100_000, f"利润失真: {profit}"


# ── 独立运行入口 ─────────────────────────────────────────────────────────

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
            print(f"ERROR {t.__name__}: {e!r}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
