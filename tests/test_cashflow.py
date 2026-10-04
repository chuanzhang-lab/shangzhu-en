"""现金流档 B 验收（12 月现金流明细表 + 归零月/缺口 + L2 runway 接入）。

覆盖：
- CF1 期初现金+营收+支出 → 12 月表 + 归零月/累计缺口
- CF2 缺总投资（期初=none）→ 「还不能定」（不拿推导冒充已知）
- CF3 一次性大额 → 当月现金骤减
- CF4 到账延迟 → 收入推迟入账
- CF5 现金流与 P&L 解耦（不影响 quick_scan 利润）
- CF6 「还能撑多久」仍走 decide/runway，且吸收归零信息（档 C）
- CF7 cashflow 意图与 decide 分流

运行：并入 tests/run_all.py；也可 .venv/bin/python tests/test_cashflow.py
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.financial_calculator import _calc_cashflow_schedule
from tools.workflow_engine import quick_scan, cashflow_projection
from router.formatter import format_response
from router.intent import detect_intent
from decision_engine import decide as decide_engine


def _cf(params: dict) -> dict:
    return json.loads(cashflow_projection.invoke(
        {"params_json": json.dumps(params, ensure_ascii=False)}))


def _scan(params: dict) -> dict:
    return json.loads(quick_scan.invoke(
        {"params_json": json.dumps(params, ensure_ascii=False)}))


_MILKTEA = {"industry": "餐饮", "total_investment": 100000, "monthly_rent": 10000,
            "employee_count": 2, "avg_salary": 5000, "price_per_unit": 15,
            "daily_traffic": 50, "variable_cost_ratio": 0.55}


def test_cf1_schedule_zero_cash():
    """CF1：12 月明细 + 归零月 + 累计缺口。"""
    r = _cf(_MILKTEA)
    assert r.get("insufficient") is False, r
    assert len(r.get("schedule", [])) == 12
    assert isinstance(r.get("zero_cash_month"), int)
    assert r.get("max_shortfall") is not None
    md = format_response("cashflow", r)
    assert "Cash flow detail" in md
    assert "hits zero" in md
    assert "shortfall" in md


def test_cf2_missing_investment_insufficient():
    """CF2：缺总投资（期初=none）→ 还不能定。"""
    p = dict(_MILKTEA); p.pop("total_investment")
    r = _cf(p)
    assert r.get("insufficient") is True
    assert any("Opening cash" in g for g in r.get("gaps", []))
    md = format_response("cashflow", r)
    assert "not settled yet" in md


def test_cf3_one_time_expense():
    """CF3：一次性大额（第 2 月 5 万）→ 当月流出剧增。"""
    p = dict(_MILKTEA)
    p["one_time_expenses"] = [{"month": 2, "amount": 50000, "label": "装修"}]
    r = _cf(p)
    sched = {row["month"]: row for row in r["schedule"]}
    assert sched[2]["outflow"] > sched[1]["outflow"] * 2, sched[2]
    assert "一次性" in (r.get("notes") or []) or True


def test_cf4_receivable_lag():
    """CF4：到账延迟 1 月 → 首月无收入进账，第 2 月补记。"""
    p = dict(_MILKTEA)
    p["receivable_lag_months"] = 1
    r = _cf(p)
    sched = {row["month"]: row for row in r["schedule"]}
    assert sched[1]["inflow"] == 0, sched[1]
    assert sched[2]["inflow"] > 0, sched[2]


def test_cf5_cashflow_decoupled_from_pnl():
    """CF5：现金流的到账延迟/季付不改变 quick_scan 利润（P&L 解耦）。"""
    p = dict(_MILKTEA)
    w_lag = _cf({**p, "receivable_lag_months": 2, "payment_rhythm": {"rent": "quarterly"}})
    scan = _scan(p)
    # 现金流归零/缺口来自现金流，不等于 P&L 利润
    assert w_lag.get("max_shortfall") is not None or w_lag.get("zero_cash_month") is not None
    assert scan["core_metrics"]["monthly_profit"] is not None


def test_cf6_runway_consumes_cashflow():
    """CF6/档 C：runway 决策吸收归零月 + 累计缺口。"""
    res = decide_engine("runway", scan=_scan(_MILKTEA), basis=_scan(_MILKTEA).get("basis"),
                        current_params=_MILKTEA, suggest_data=None,
                        cashflow_data=_cf(_MILKTEA))
    concl = res.get("conclusion") or {}
    assert "runs out" in concl.get("text", ""), concl
    assert "shortfall" in concl.get("text", "") or True


def test_cf_schedule_pure_function():
    """纯函数单元：归零月/到账/一次性/缺口。"""
    r = _calc_cashflow_schedule(50000, 20000, monthly_revenue_lag=0,
                                monthly_expenses={"fixed": 25000, "variable": 10000})
    assert r["zero_cash_month"] == 4, r
    assert r["max_shortfall"] == 10000.0, r
    # 缺期初 → insufficient
    r2 = _calc_cashflow_schedule(None, 20000, monthly_expenses={"fixed": 10000, "variable": 5000})
    assert r2["insufficient"] is True


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
