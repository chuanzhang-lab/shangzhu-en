"""报告产出契约（report_generator = 用户直接拿到手的文件）。

这里守的是**渲染契约**，不是计算：报告一旦印错，用户拿到的是一份看着正规、
内容却是内部结构或空行的文件，比报错更难发现。

三条必须永远成立：
1. **不印数据键**：core_metrics / status 的 snake_case 键是引擎内部结构，
   报告里必须换成展示名（monthly_revenue → Monthly revenue）。
2. **缺失不印 None**：None 直出会被读者当成「数值就是 None」，
   与项目「缺失不冒充 0」属同一类事故 —— 缺失要显式成「—」。
3. **敏感性只呈现带标签的三档**：_calc_sensitivity 返回 steps×steps 网格
   （默认 9 条），标签落在索引 2/4/6。按 [:3] 切片取到的是无标签网格行，
   渲染成「?」或空行 —— 按标签筛才与 steps 解耦。
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import i18n  # noqa: E402
from i18n import t  # noqa: E402
from tools.report_generator import build_excel_sheets, build_report_markdown  # noqa: E402

# 最小 scan：刻意保留一个 None，用来验证「缺失不被印成 None」
_SCAN = {
    "project_type": "餐饮",
    "stage": None,
    "core_metrics": {
        "monthly_revenue": 22500,
        "monthly_profit": -9875.0,
        "daily_breakeven": 99.0,
        "breakeven_revenue_monthly": None,  # 缺失
        "runway_months": 10.1,
        "gross_margin_percent": 45.0,
    },
    "status": {"profit": "亏损", "cash": "偏紧", "breakeven": "客流不足", "margin": "一般"},
    "sensitivity": {"scenarios": [
        # 3×3 网格中的无标签行（真实 _calc_sensitivity 会产出这类行）
        {"revenue_change": "-20.0%", "cost_change": "-20.0%", "profit": -7900.0},
        {"revenue_change": "-20.0%", "cost_change": "0.0%", "profit": -11900.0},
        {"revenue_change": "-20.0%", "cost_change": "20.0%", "profit": -15900.0, "scenario": "悲观"},
        {"revenue_change": "0.0%", "cost_change": "-20.0%", "profit": -5875.0},
        {"revenue_change": "0.0%", "cost_change": "0.0%", "profit": -9875.0, "scenario": "中性"},
        {"revenue_change": "0.0%", "cost_change": "20.0%", "profit": -13875.0},
        {"revenue_change": "20.0%", "cost_change": "-20.0%", "profit": -3850.0, "scenario": "乐观"},
        {"revenue_change": "20.0%", "cost_change": "0.0%", "profit": -8375.0},
        {"revenue_change": "20.0%", "cost_change": "20.0%", "profit": -12875.0},
    ]},
}

_METRIC_KEYS = ("monthly_revenue", "monthly_profit", "daily_breakeven",
                "breakeven_revenue_monthly", "runway_months", "gross_margin_percent")
_STATUS_KEYS = ("profit", "cash", "breakeven", "margin")


@pytest.fixture(params=["zh", "en"])
def locale(request):
    i18n.set_locale(request.param)
    yield request.param
    i18n.reset_locale()


def test_report_prints_no_engine_data_keys(locale):
    """报告不得把引擎 snake_case 数据键印给用户看。"""
    md = build_report_markdown(_SCAN)
    leaked = [k for k in _METRIC_KEYS if k in md]
    assert not leaked, f"[{locale}] 报告泄漏了引擎数据键: {leaked}"


def test_status_dimension_is_localized_not_raw_key(locale):
    """状态维度同样不得是原始键（中文下键与名易混淆，用英文断言才有判别力）。"""
    md = build_report_markdown(_SCAN)
    for k in _STATUS_KEYS:
        # 英文下若未映射，原文 `- profit:` 会直接出现
        assert f"- {k}:" not in md, f"[{locale}] 状态维度未映射成展示名: {k}"


def test_report_never_prints_literal_none(locale):
    """缺失值必须显式成「—」，不能是字面量 None。"""
    md = build_report_markdown(_SCAN)
    assert "None" not in md, f"[{locale}] 报告把缺失印成了字面量 None"


def test_sensitivity_table_has_exactly_three_labeled_rows(locale):
    """敏感性表只呈现悲观/中性/乐观三档，不得混入无标签网格行。"""
    md = build_report_markdown(_SCAN)
    # 标题本身是文案，按当 locale 的标题定位段落，不能写死英文 "Scenario"
    lines = md.splitlines()
    start = lines.index(t("rg.report.sens_header"))
    header_row = t("rg.report.sens_table").splitlines()[0]
    rows = [
        ln for ln in lines[start:]
        if ln.startswith("|") and "---" not in ln and ln != header_row
    ]
    assert len(rows) == 3, f"[{locale}] 敏感性表行数应为 3，实际 {len(rows)}: {rows}"
    for r in rows:
        cells = [c.strip() for c in r.strip("|").split("|")]
        assert cells and cells[0], f"[{locale}] 情景名列为空（取到无标签网格行）: {r}"


def test_industry_data_key_is_mapped_for_display():
    """行业是**数据键**（餐饮）：英文报告里必须映射成展示名，不能原样印中文键。"""
    i18n.set_locale("en")
    try:
        md = build_report_markdown(_SCAN)
        assert "餐饮" not in md, "英文报告里残留了行业数据键（未走 industry_name 映射）"
    finally:
        i18n.reset_locale()


def test_excel_sheets_use_display_names(locale):
    """Excel 与 Markdown 同一套展示名，不能一处映射一处泄漏。"""
    sheets = build_excel_sheets(_SCAN)
    flat = str(sheets)
    leaked = [k for k in _METRIC_KEYS if k in flat]
    assert not leaked, f"[{locale}] Excel 泄漏了引擎数据键: {leaked}"
