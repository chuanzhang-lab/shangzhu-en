"""行业季节模板测试 — 覆盖季节系数读取与趋势渲染。

⚠️ 季节系数已改为**美国日历**（2026-10），断言随之改：
- 餐饮：1 月不再是中国春节旺季，而是节后淡月；11-12 月感恩节/年末团餐最强。
- 零售/电商：11-12 月（黑五 + 圣诞季）为全年峰值，6 月不再是 618 高峰。

测试模块：
- C1: 餐饮季节系数正确读取
- C2: 零售季节系数正确读取
- C3: 电商季节系数正确读取
- C4: 无行业 → fallback 通用 map
- C5: 趋势预测中季节系数生效
- C6: seasonal_source 标注
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tools.workflow_engine import (
    _get_industry_seasonal_profile,
    _DEFAULT_SEASON_MAP,
    _project_trend_12m,
)


# ─── C1-C3: 行业季节系数读取 ──────────────────────────────────────────────

class TestIndustrySeasonalProfile:
    """行业季节系数从 YAML 模板读取。"""

    def test_restaurant_seasonal(self):
        """餐饮（美国日历）：12月年末团餐最强，1月节后最弱。"""
        profile = _get_industry_seasonal_profile("餐饮")
        assert len(profile) == 12
        assert profile[12] == 1.18   # 12月：感恩节余温 + 年末聚餐/团餐
        assert profile[11] == 1.10   # 11月：感恩节
        assert profile[1] == 0.90    # 1月：节后清淡（不再是中国春节旺季）
        # 峰值必须在年末，不能留在 1 月——改回中国日历这里会红
        assert max(profile, key=profile.get) == 12

    def test_retail_seasonal(self):
        """零售（美国日历）：12月圣诞季为全年最高，1-2月节后最淡。"""
        profile = _get_industry_seasonal_profile("零售")
        assert len(profile) == 12
        assert profile[12] == 1.35   # 圣诞季
        assert profile[11] == 1.30   # 黑五
        assert profile[1] == 0.85    # 节后淡
        assert max(profile, key=profile.get) == 12
        assert profile[8] == 1.05    # 8月返校季次高峰

    def test_ecommerce_seasonal(self):
        """电商（美国日历）：12月最强、11月次之、7月 Prime Day 小高峰。"""
        profile = _get_industry_seasonal_profile("电商")
        assert len(profile) == 12
        assert profile[12] == 1.40   # 圣诞季
        assert profile[11] == 1.35   # 黑五 + 网一
        assert profile[7] == 1.10    # Prime Day（不再是 618 ）
        assert profile[6] == 0.98    # 6月是美国电商的淡月
        assert max(profile, key=profile.get) == 12

    def test_alias_resolution(self):
        """别名解析：cafe → 餐饮。"""
        profile = _get_industry_seasonal_profile("cafe")
        assert profile[12] == 1.18  # 同餐饮

    def test_unknown_industry_fallback(self):
        """未知行业 → 通用 map。"""
        profile = _get_industry_seasonal_profile("未知行业")
        assert profile == _DEFAULT_SEASON_MAP

    def test_empty_industry_fallback(self):
        """空行业名 → 通用 map。"""
        profile = _get_industry_seasonal_profile("")
        assert profile == _DEFAULT_SEASON_MAP


# ─── C5: 趋势预测中季节系数生效 ────────────────────────────────────────────

class TestTrendWithSeasonal:
    """趋势预测中行业季节系数正确应用。"""

    def test_restaurant_dec_higher_than_jan(self):
        """餐饮（美国日历）：12月（年末团餐）营收应高于1月（节后淡）。"""
        params = {
            "monthly_revenue": 30000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.4,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "餐饮",
        }
        result = _project_trend_12m(params)
        jan_revenue = result["months"][0]["revenue"]   # 1月
        dec_revenue = result["months"][11]["revenue"]  # 12月
        # 12月系数 1.18 > 1月系数 0.90
        assert dec_revenue > jan_revenue

    def test_ecommerce_dec_peak(self):
        """电商（美国日历）：12月营收应为全年最高（11月次之）。"""
        params = {
            "monthly_revenue": 30000,
            "monthly_fixed_cost": 15000,
            "variable_cost_ratio": 0.6,
            "available_cash": 100000,
            "total_investment": 100000,
            "industry_name": "电商",
        }
        result = _project_trend_12m(params)
        revenues = [m["revenue"] for m in result["months"]]
        # 12月（index 11）应为最高
        assert revenues[11] == max(revenues)
        assert revenues[10] == sorted(revenues)[-2]  # 11月第二


# ─── 运行 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
