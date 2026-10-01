"""financial_calculator 公共财务函数的直接单测（提升 A 路径覆盖率与防回归）。

覆盖：NPV / IRR / ROI / 盈亏平衡 / 单位经济(LTV-CAC) / 跑道 / 收入预测 /
成本结构 / 敏感性分析，以及关键错误处理（除零、售价≤变动成本、坏 JSON）。
"""
import json
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from i18n import reset_locale, set_locale
from tools.financial_calculator import (
    calculate_npv,
    calculate_irr,
    calculate_roi,
    calculate_breakeven,
    calculate_unit_economics,
    calculate_runway,
    build_revenue_projection,
    build_cost_structure,
    sensitivity_analysis,
)


def _call(fn, **kwargs):
    """LangChain @tool 标准调用：返回 JSON 字符串，解析为 dict。"""
    raw = fn.invoke(kwargs)
    return json.loads(raw)


@pytest.fixture
def en():
    """会话级切到 en（压过 conftest 的部署级 zh 钉），用完复位。"""
    set_locale("en")
    yield
    reset_locale()


# ── NPV ────────────────────────────────────────────────────────────────────
def test_npv_zero_rate_is_sum(en):
    d = _call(calculate_npv, rate_percent=0, cashflows_json="[100000,100000,100000]")
    assert d["npv"] == 300000.0, d
    assert d["interpretation"] == "Project is worth investing in", d


def test_npv_positive_discount():
    d = _call(calculate_npv, rate_percent=10, cashflows_json="[100000,100000,100000]")
    # 第 0 期不折现：100000 + 100000/1.1 + 100000/1.21 ≈ 273553.72
    assert abs(d["npv"] - 273553.72) < 1.0, d
    assert d["npv"] > 0


def test_npv_with_initial_investment():
    d = _call(calculate_npv, rate_percent=10, cashflows_json="[100000,100000]",
              initial_investment=200000)
    assert d["cashflows"][0] == -200000.0
    assert d["periods"] == 3


def test_npv_empty_cashflows_errors():
    d = _call(calculate_npv, rate_percent=10, cashflows_json="[]")
    assert "error" in d


# ── IRR ──────────────────────────────────────────────────────────────────
def test_irr_single_period():
    d = _call(calculate_irr, cashflows_json="[-1000,1100]")
    assert abs(d["irr_decimal"] - 0.1) < 1e-4, d
    assert d["irr"] == "10.0%"


def test_irr_with_initial_investment():
    d = _call(calculate_irr, cashflows_json="[500,500,500]", initial_investment=1000)
    assert d["cashflows"][0] == -1000.0


# ── ROI ──────────────────────────────────────────────────────────────────
def test_roi_basic(en):
    d = _call(calculate_roi, total_return=150, total_investment=100)
    assert d["roi"] == "50.0%", d
    assert d["net_profit"] == 50.0
    assert d["interpretation"] == "Decent return"


def test_roi_zero_investment_errors():
    d = _call(calculate_roi, total_return=100, total_investment=0)
    assert "error" in d


# ── 盈亏平衡 ──────────────────────────────────────────────────────────────
def test_breakeven_basic():
    d = _call(calculate_breakeven, fixed_costs=10000, price_per_unit=20,
              variable_cost_per_unit=10)
    assert d["breakeven_units"] == 1000.0, d
    assert d["contribution_margin_per_unit"] == 10.0


def test_breakeven_price_le_variable_errors():
    d = _call(calculate_breakeven, fixed_costs=10000, price_per_unit=5,
              variable_cost_per_unit=10)
    assert "error" in d


# ── 单位经济 LTV/CAC ─────────────────────────────────────────────────────
def test_unit_economics_healthy(en):
    d = _call(calculate_unit_economics, customer_acquisition_cost=100,
              customer_lifetime_value=300, gross_margin_percent=60)
    assert d["ltv_cac_ratio"] == 3.0, d
    assert d["health"] == "Healthy"


def test_unit_economics_dangerous(en):
    d = _call(calculate_unit_economics, customer_acquisition_cost=200,
              customer_lifetime_value=100, gross_margin_percent=30)
    assert d["ltv_cac_ratio"] == 0.5
    assert d["health"] == "Danger"


def test_unit_economics_zero_cac_errors():
    d = _call(calculate_unit_economics, customer_acquisition_cost=0,
              customer_lifetime_value=100, gross_margin_percent=60)
    assert "error" in d


# ── 跑道 ──────────────────────────────────────────────────────────────────
def test_runway_finite():
    d = _call(calculate_runway, current_cash=120000, monthly_burn_rate=30000)
    assert d["runway_months"] == 4.0, d
    assert d["net_monthly_burn"] == 30000.0
    assert d["urgency"] == "critical"


def test_runway_infinite_when_profitable():
    d = _call(calculate_runway, current_cash=100000, monthly_burn_rate=20000,
              monthly_revenue=30000)
    assert d["runway_months"] == "无限", d
    assert d["congratulations"] is True


def test_runway_missing_cash():
    # @tool 的 pydantic schema 不接受 None；函数内部支持 None（返回 runway_months=None）。
    # 直接调原函数(.func)以验证该分支；原函数返回 JSON 字符串，需解析。
    raw = calculate_runway.func(current_cash=None, monthly_burn_rate=20000)
    d = json.loads(raw)
    assert d["runway_months"] is None


# ── 收入预测 ──────────────────────────────────────────────────────────────
def test_revenue_projection():
    d = _call(build_revenue_projection, base_revenue=10000,
              monthly_growth_rate_percent=10, months=3, churn_rate_percent=0)
    # 10000*1.1=11000, *1.1=12100, *1.1=13310
    assert d["final_month_revenue"] == 13310.0, d
    assert d["total_revenue"] == 11000 + 12100 + 13310, d
    assert d["projection_months"] == 3


def test_revenue_projection_month_bounds():
    d = _call(build_revenue_projection, base_revenue=10000,
              monthly_growth_rate_percent=5, months=0)
    assert "error" in d


# ── 成本结构 ──────────────────────────────────────────────────────────────
def test_cost_structure():
    fixed = '[{"name":"房租","amount":15000}]'
    variable = '[{"name":"食材","amount":30000}]'
    d = _call(build_cost_structure, fixed_costs_json=fixed,
              variable_costs_json=variable, projected_revenue=60000)
    assert d["total_fixed_cost"] == 15000
    assert d["total_variable_cost"] == 30000
    assert d["total_cost"] == 45000
    assert d["gross_profit"] == 30000
    assert d["net_profit"] == 15000
    assert d["gross_margin"] == "50.0%"


def test_cost_structure_bad_json():
    d = _call(build_cost_structure, fixed_costs_json="not-json",
              variable_costs_json="[]", projected_revenue=1000)
    assert "error" in d


# ── 敏感性分析（F3：变动成本随营收联动，固定成本独立波动）────────────────
def test_sensitivity(en):
    # 典型餐饮：月营收 6 万，固定成本 3 万（租金+人工+水电），变动成本 1.8 万（食材+包装+佣金）
    # 变动成本率 = 18000/60000 = 30%，月利润 = 12000
    d = _call(sensitivity_analysis, base_revenue=60000, fixed_cost=30000, variable_cost=18000,
              revenue_range_percent=20, cost_range_percent=20, steps=3)
    assert d["base_profit"] == 12000, d
    assert len(d["sensitivity_matrix"]) == 3
    # 最坏：营收-20% → 变动成本同步-20%，固定成本+20%
    #   rev=48000, var_c=14400, fix_c=36000, profit=48000-50400=-2400
    assert d["worst_case"]["profit"] == -2400.0, d
    # 最好：营收+20% → 变动成本同步+20%，固定成本-20%
    #   rev=72000, var_c=21600, fix_c=24000, profit=72000-45600=26400
    assert d["best_case"]["profit"] == 26400.0, d
    assert d["profit_range"] == 26400 - (-2400)
    # 口径说明必须存在
    assert "Variable cost moves with revenue" in d["note"]


# ── M2：None 消费一致性（2026-08-19）──────────────────────────────────
# _safe_runway / _do_cashflow_check 在派生字段缺失时不得因 None 算术崩溃，
# 与引擎「缺失按 0 / 跳过」哲学保持一致。

def test_safe_runway_missing_fixed_no_crash():
    """_safe_runway 在固定成本缺失（None）时不应因 None 相加崩溃。"""
    from tools.workflow_engine import _safe_runway
    # 有 cash 但 fixed=None → burn = 0+0 = 0，不崩，返回有限月数或 N/A
    r = _safe_runway({"available_cash": 100000, "monthly_fixed_cost": None,
                      "monthly_variable_cost": None, "monthly_revenue": 20000})
    assert r is not None


def test_safe_runway_no_cash_unknown():
    """可用现金缺失 → 返回 '未知'。"""
    from tools.workflow_engine import _safe_runway
    r = _safe_runway({"available_cash": None, "monthly_fixed_cost": 10000,
                      "monthly_variable_cost": 0, "monthly_revenue": 20000})
    assert r == "未知"


def test_cashflow_check_revenue_none_no_crash():
    """_do_cashflow_check 在月营收为 None 时不得崩溃（缺失按 0 计）。"""
    from tools.pitfall_detector import _do_cashflow_check
    d = _do_cashflow_check(current_cash=100000, monthly_expense=20000,
                           monthly_revenue=None)
    # 不应抛异常；net_burn = 20000 - 0 = 20000，跑道应算出
    assert "runway" in str(d) or "pitfalls" in d or "suggestions" in d
