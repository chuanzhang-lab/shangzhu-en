"""固定成本「不完整」标注回归（第 3 步：人工缺失时不得假装算全了）。

背景（真实事故）：只给租金 8000、没给人工 → 引擎算出 monthly_fixed_cost=8000
并标 `[推算] 组件求和(租金)`。人工通常占固定成本 3~5 成（本例 5600，占 41%），
于是利润虚高、保本客流虚低、跑道虚长 —— 而报表里**没有任何一处**告诉用户
「这个数是不全的」。

修法不是「把人工猜出来」（那是虚构），而是：
- 值照旧（不虚构缺失项）；
- 状态码改 `incomplete`（机器可读，语言无关）；
- 来源串与报表正文显式写出缺什么、成本被低估。

本文件断言的是**协议**（状态码）与**文案契约**（中英各一份），
不依赖中文前缀匹配 —— 英文版同样必须成立。

locale：conftest.py 把 SHANGZHU_LOCALE 钉成 zh，故默认用例走中文；
英文用例用 `set_locale("en")` 会话级覆盖（优先级高于 env）。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from i18n import reset_locale, set_locale, t  # noqa: E402
import source_tags as st  # noqa: E402
from router.formatter import _fmt_scan  # noqa: E402
from tools.pitfall_markers import conflict_markers  # noqa: E402
from tools.workflow_engine import quick_scan  # noqa: E402

# 收入/投资给足，避免门禁拦下走 insufficient 分支（那会换一套渲染）
_BASE = {"monthly_revenue": 30000, "variable_cost_ratio": 0.5,
         "total_investment": 100000}


def _scan(params_dict):
    """经唯一计算点 quick_scan 出仪表盘 dict。"""
    return json.loads(quick_scan.invoke(
        {"params_json": json.dumps(params_dict, ensure_ascii=False)}))


def _code_of(d, field="monthly_fixed_cost"):
    return st.code_of(d["param_sources"], field)


# ── 正例：核心组件缺失 → 标 incomplete ────────────────────────────────────

def test_labor_missing_marks_incomplete():
    """只给租金：值不虚构（仍 8000），但状态码必须是 incomplete，且点名缺人工。"""
    d = _scan({**_BASE, "monthly_rent": 8000})
    assert d["params"]["monthly_fixed_cost"] == 8000, d["params"]
    assert _code_of(d) == st.INCOMPLETE, d["param_sources"]
    src = d["param_sources"]["monthly_fixed_cost"]
    assert "组件求和" in src and "人工" in src, src
    # 值必须是真数字，不能因为「不完整」就变成 None
    assert d["core_metrics"]["monthly_profit"] is not None


def test_rent_missing_marks_incomplete():
    """同根因的另一半：只给人工、没给租金 → 同样标 incomplete（点名缺租金）。"""
    d = _scan({**_BASE, "employee_count": 2, "avg_salary": 2800})
    assert d["params"]["monthly_fixed_cost"] == 5600, d["params"]
    assert _code_of(d) == st.INCOMPLETE, d["param_sources"]
    assert "租金" in d["param_sources"]["monthly_fixed_cost"]


def test_both_core_missing_names_both():
    """两个核心组件都缺时，文案必须把两个都点出来（不能只说一个）。"""
    d = _scan({**_BASE, "utilities": 800})
    src = d["param_sources"]["monthly_fixed_cost"]
    assert _code_of(d) == st.INCOMPLETE
    assert "租金" in src and "人工" in src, src


# ── 反例：不该标 incomplete 的情况 ────────────────────────────────────────

def test_complete_components_not_flagged():
    """租金 + 人工齐全 → 仍是正常的组件求和，不得误报不完整。"""
    d = _scan({**_BASE, "monthly_rent": 8000, "employee_count": 2,
               "avg_salary": 2800})
    assert d["params"]["monthly_fixed_cost"] == 13600, d["params"]
    assert _code_of(d) != st.INCOMPLETE, d["param_sources"]
    assert _code_of(d) == st.USER, d["param_sources"]


def test_optional_components_absent_not_flagged():
    """水电/包装/提成/其他是可选细项，缺失不算不完整 —— 否则天天报警变噪音。"""
    d = _scan({**_BASE, "monthly_rent": 8000, "employee_count": 2,
               "avg_salary": 2800})
    assert _code_of(d) != st.INCOMPLETE


def test_explicit_total_is_authoritative_not_flagged():
    """用户给了权威总数且吻合 → 总数即完整口径，不该再标不完整。"""
    d = _scan({**_BASE, "monthly_rent": 8000, "monthly_expense": 8000})
    assert d["params"]["monthly_fixed_cost"] == 8000
    assert _code_of(d) != st.INCOMPLETE, d["param_sources"]


def test_conflict_takes_precedence_over_incomplete():
    """显式总数与组件和矛盾 → 「待澄清」优先（比不完整更急需用户介入）。"""
    d = _scan({**_BASE, "monthly_rent": 8000, "monthly_expense": 20000})
    src = d["param_sources"]["monthly_fixed_cost"]
    assert "矛盾" in src, src
    assert _code_of(d) != st.INCOMPLETE, src


def test_all_components_missing_stays_missing():
    """一个组件都没有 → 仍是 [缺失]（不虚构），不是 incomplete。"""
    d = _scan(dict(_BASE))
    src = d["param_sources"]["monthly_fixed_cost"]
    assert _code_of(d) == st.MISSING, src
    assert d["params"]["monthly_fixed_cost"] is None


# ── 英文契约 ──────────────────────────────────────────────────────────────

def test_english_text_is_english():
    """英文版：文案全英文、无未填充占位符、状态码不变（语言无关）。"""
    set_locale("en")
    try:
        d = _scan({**_BASE, "monthly_rent": 8000})
        src = d["param_sources"]["monthly_fixed_cost"]
        assert _code_of(d) == st.INCOMPLETE, src
        assert "Labor not provided" in src, src
        assert "{" not in src and "}" not in src, src
        assert not any("一" <= ch <= "鿿" for ch in src), src
        # 状态码标记本身也是英文
        assert st.mark(st.INCOMPLETE) == "[Incomplete]", st.mark(st.INCOMPLETE)
        assert "missing:" not in st.mark(st.INCOMPLETE)
    finally:
        reset_locale()


# ── 报表可见性 ────────────────────────────────────────────────────────────

def test_report_surfaces_caveat_when_incomplete():
    """提示必须在报表正文里出现（不能只藏在「参数来源」表的一行里）。"""
    txt = _fmt_scan(_scan({**_BASE, "monthly_rent": 8000}))
    assert "人工" in txt and "成本不完整" in txt, txt


def test_report_has_no_caveat_when_complete():
    """组件齐全时报表不得出现该警告（防误报）。"""
    txt = _fmt_scan(_scan({**_BASE, "monthly_rent": 8000,
                           "employee_count": 2, "avg_salary": 2800}))
    assert "成本不完整" not in txt, txt


# ── 协议防漂移 ────────────────────────────────────────────────────────────

def test_incomplete_text_must_not_read_as_conflict():
    """「不完整」不是「矛盾」：文案不得含冲突词，否则 AnomalyReport 会误报成自相矛盾。

    `_emit_anomaly_report` 靠 `pitfall_markers.conflict_markers()` 扫来源串，
    一旦 incomplete 文案里出现「矛盾/conflict」，运维告警就被污染。
    """
    set_locale("en")
    try:
        src = _scan({**_BASE, "monthly_rent": 8000})["param_sources"]["monthly_fixed_cost"]
        low = src.lower()
        assert not any(kw.lower() in low for kw in conflict_markers()), src
    finally:
        reset_locale()


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_frontend_maps_incomplete_code():
    """前端必须认得 incomplete，否则参数面板会把它兜成「推算」（静默误导店主）。

    后端标了 incomplete，前端 `srcCodeOf` 若不认，就落进 else 分支显示「推算」——
    等于告诉店主「这个数算全了」，后端改了也白改。
    """
    root = os.path.join(os.path.dirname(__file__), "..")
    js = _read(os.path.join(root, "src", "web_static", "app.js"))
    assert "'incomplete'" in js, "srcCodeOf 未返回 incomplete 码"
    assert "[不完整]" in js and "[Incomplete]" in js, "缺 _codes 的历史存档回退分支"
    assert "ui.src_incomplete" in js, "参数面板未接入 incomplete 文案"
    css = _read(os.path.join(root, "src", "web_static", "app.css"))
    assert ".psrc-incomplete" in css, "缺 incomplete 配色"
    for loc in ("zh", "en"):
        assert "src_incomplete:" in _read(
            os.path.join(root, "src", "i18n", f"{loc}.yaml")), loc


@pytest.mark.parametrize("loc,expect", [("zh", "[不完整]"), ("en", "[Incomplete]")])
def test_mark_resolves_in_both_locales(loc, expect):
    """两个 locale 的标记键都必须存在（缺键会渲染成 [i18n:missing:...]）。"""
    set_locale(loc)
    try:
        assert st.mark(st.INCOMPLETE) == expect, st.mark(st.INCOMPLETE)
        # 组件名与连接符同样双语齐备
        assert "missing:" not in t("src.comp.labor")
        assert "missing:" not in t("src.comp_join")
        assert t("src.comp_join")
    finally:
        reset_locale()
