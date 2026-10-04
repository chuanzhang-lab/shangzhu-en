"""模拟器升级测试 — 覆盖 P0-P1 全部新增功能。

测试模块：
- S1: 用户指定多期收入序列
- S2: NPV/IRR 接入 quick_scan
- S3: 动态跑道
- S5: 多期场景对比
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.workflow_engine import (
    _project_trend_12m,
    _fill_and_assess,
    _get_industry_seasonal_profile,
    compare_scenarios,
    quick_scan,
)


def _season(params: dict, month: int) -> float:
    """动态读取行业季节系数——断言应校验「引擎正确应用模板系数」，
    而非写死某个数值（模板调整时测试不再误报）。"""
    return _get_industry_seasonal_profile(params.get("industry_name", "")).get(month, 1.0)


# ─── S1: 收入序列支持 ─────────────────────────────────────────────────────

class TestRevenueSeries:
    """S1: monthly_revenue 支持数组输入。"""

    def test_single_value_mode(self):
        """单值模式：原有逻辑不变。"""
        params = {
            "monthly_revenue": 30000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "monthly_growth_rate": 0.1,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        assert result["input_mode"] == "growth_rate"
        assert result["months_count"] == 12
        assert len(result["months"]) == 12
        # 第1月 = 30000 × 餐饮1月季节系数（模板值，动态读取）
        assert result["months"][0]["revenue"] == round(30000 * _season(params, 1), 0)

    def test_series_mode_basic(self):
        """收入序列模式：5 期序列，默认延续到 12 期。"""
        params = {
            "monthly_revenue": [30000, 45000, 60000, 75000, 90000],
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        assert result["input_mode"] == "series"
        assert result["months_count"] == 12  # 默认延续到 12 期
        assert len(result["months"]) == 12
        # 前 5 期使用用户序列值 × 对应月份季节系数
        assert result["months"][0]["revenue"] == round(30000 * _season(params, 1), 0)
        assert result["months"][4]["revenue"] == round(90000 * _season(params, 5), 0)

    def test_series_mode_first_value(self):
        """收入序列：第一个值应正确。"""
        params = {
            "monthly_revenue": [30000, 45000, 60000],
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        # 第1月收入 = 30000 × 餐饮1月季节系数（模板值，动态读取）
        assert result["months"][0]["revenue"] == round(30000 * _season(params, 1), 0)

    def test_series_with_post_growth(self):
        """收入序列：序列用完后按 growth_rate 延续。"""
        params = {
            "monthly_revenue": [30000, 45000],
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "monthly_growth_rate": 0.1,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        # 第3月 = 45000 × (1+0.1)^1 × 餐饮3月季节系数（模板值，动态读取）
        assert result["months"][2]["revenue"] == round(45000 * 1.1 * _season(params, 3), 0)

    def test_custom_analysis_months(self):
        """analysis_months 参数生效。"""
        params = {
            "monthly_revenue": 30000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "analysis_months": 6,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        assert result["months_count"] == 6
        assert len(result["months"]) == 6


# ─── S2: NPV/IRR 接入 ────────────────────────────────────────────────────

class TestInvestmentMetrics:
    """S2: quick_scan 输出含 investment_metrics。"""

    def test_npv_irr_in_trend(self):
        """有总投资时，趋势预测含 NPV/IRR。"""
        params = {
            "monthly_revenue": 30000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "monthly_growth_rate": 0.05,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        assert "investment_metrics" in result
        im = result["investment_metrics"]
        assert "npv_8pct" in im
        assert "irr" in im
        assert "irr_percent" in im
        assert im["discount_rate"] == "8%"

    def test_npv_irr_with_series(self):
        """收入序列模式也含 NPV/IRR。"""
        params = {
            "monthly_revenue": [30000, 45000, 60000, 75000, 90000],
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        assert "investment_metrics" in result


# ─── S3: 动态跑道 ─────────────────────────────────────────────────────────

class TestDynamicRunway:
    """S3: 动态跑道（趋势感知）。"""

    def test_dynamic_runway_present(self):
        """有可用现金时，趋势预测含 dynamic_runway。"""
        params = {
            "monthly_revenue": 30000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        assert "dynamic_runway" in result
        dr = result["dynamic_runway"]
        assert "runway_months" in dr
        assert "runway_label" in dr
        assert "min_cash" in dr
        assert "min_cash_month" in dr

    def test_dynamic_runway_exhaustion(self):
        """现金耗尽时，runway_months 应为具体月数。"""
        params = {
            "monthly_revenue": 10000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "available_cash": 5000,  # 很少现金
            "total_investment": 5000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        dr = result["dynamic_runway"]
        # 月亏损 = 10000*0.85 - 15000 - 10000*0.85*0.4 = 8500 - 15000 - 3400 = -9900
        # 现金 5000，第1个月就耗尽
        assert dr["runway_months"] is not None
        assert dr["runway_months"] <= 1

    def test_dynamic_runway_not_exhausted(self):
        """现金充足时，runway_months 为 None。"""
        params = {
            "monthly_revenue": 50000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "available_cash": 500000,
            "total_investment": 500000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        dr = result["dynamic_runway"]
        assert dr["runway_months"] is None  # 未耗尽
        assert ">" in dr["runway_label"]


# ─── S5: 多期场景对比 ────────────────────────────────────────────────────

class TestMultiPeriodComparison:
    """S5: compare_scenarios 含趋势对比。"""

    def test_trend_comparison_present(self):
        """两个方案都有完整参数时，含 trend_comparison。"""
        base = json.dumps({
            "monthly_revenue": 50000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "available_cash": 200000,
            "total_investment": 200000,
            "monthly_rent": 8000,
            "employee_count": 3,
            "avg_salary": 5000,
            "price_per_unit": 25,
            "daily_traffic": 100,
            "industry_name": "餐饮",
        })
        alt = json.dumps({
            "monthly_revenue": 80000,
            "monthly_fixed_cost": 25000,
            "variable_cost_ratio": 0.4,
            "available_cash": 300000,
            "total_investment": 300000,
            "monthly_rent": 15000,
            "employee_count": 5,
            "avg_salary": 5000,
            "price_per_unit": 25,
            "daily_traffic": 160,
            "industry_name": "餐饮",
        })
        result = json.loads(compare_scenarios.invoke({"base_json": base, "alt_json": alt}))
        assert "trend_comparison" in result
        tc = result["trend_comparison"]
        assert "base_annual_profit" in tc
        assert "alt_annual_profit" in tc
        assert "annual_profit_diff" in tc
        assert "base_breakeven_month" in tc
        assert "alt_breakeven_month" in tc
        assert "verdict_annual" in tc


# ─── 集成测试：quick_scan 含趋势数据 ──────────────────────────────────────

class TestQuickScanIntegration:
    """quick_scan 输出含 trend / investment_metrics / dynamic_runway。"""

    def test_quick_scan_with_growth_rate(self):
        """quick_scan 正常模式含趋势数据。"""
        result = json.loads(quick_scan.invoke({
            "params_json": json.dumps({
                "monthly_revenue": 50000,
                "monthly_fixed_cost": 15000,
                "variable_cost_ratio": 0.4,
                "monthly_rent": 8000,
                "employee_count": 3,
                "avg_salary": 5000,
                "monthly_growth_rate": 0.05,
                "available_cash": 200000,
                "total_investment": 200000,
                "price_per_unit": 25,
                "daily_traffic": 100,
                "industry_name": "餐饮",
            })
        }))
        assert "trend" in result
        assert "investment_metrics" in result
        assert "dynamic_runway" in result
        assert result["trend"]["input_mode"] == "growth_rate"

    def test_quick_scan_with_revenue_series(self):
        """quick_scan 收入序列模式。"""
        result = json.loads(quick_scan.invoke({
            "params_json": json.dumps({
                "monthly_revenue": [30000, 45000, 60000, 75000, 90000],
                "monthly_fixed_cost": 15000,
                "variable_cost_ratio": 0.4,
                "monthly_rent": 8000,
                "employee_count": 3,
                "avg_salary": 5000,
                "available_cash": 200000,
                "total_investment": 200000,
                "price_per_unit": 25,
                "daily_traffic": 100,
                "industry_name": "餐饮",
            })
        }))
        assert "trend" in result
        assert result["trend"]["input_mode"] == "series"
        assert result["trend"]["months_count"] == 12  # 序列延续到 12 期
