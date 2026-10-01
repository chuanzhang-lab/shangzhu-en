"""CJK 泄漏护栏（M-07）——英文产品的渲染出口必须零中文。

为什么是「运行时」护栏而非源码扫描：
`test_i18n_guard` 的源码扫描只能查硬编码字面量，抓不到**数据面**的中文 ——
如 `industry: 餐饮`（数据键）、`runway_months: "无限"`（引擎数据标记）。
这些值在渲染时若没映射成展示名，就会直接漏给用户，而源码里干干净净。
本文件驱动**真实管线**（英文输入 → 引擎计算 → 各渲染出口），
断言最终渲染文本不含任何 CJK（汉字 + 中文标点）。

覆盖四个渲染出口：
  1. format_response      —— 对话主界面（quick_scan 等 intent）
  2. build_report_markdown —— Markdown 报告导出
  3. render_decision       —— 决策输出
  4. format_advice         —— LLM 建议格式化

实战记录：本护栏首次运行即抓到 build_report_markdown 漏出「无限」
（引擎数据标记未映射），已修复为「Unlimited」。这正是运行时护栏的价值。
"""
import json
import re

import pytest

from i18n import reset_locale, set_locale

# 汉字（含扩展 A）+ 中文标点/全角符号 —— 全部属泄漏。
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]")


@pytest.fixture(autouse=True)
def _en_locale():
    """强制 en 场景：护栏验证的是英文产品，必须在 en 下跑。"""
    set_locale("en")
    yield
    reset_locale()


# ── 英文输入样本（过真实抽取与计算管线）────────────────────────────────────
_EN_INPUTS = [
    "I want to sell coffee, daily traffic 80, price 12, investment 180000, "
    "2 employee 2800, cost 55%, roomrent 1800",
    "Monthly rent 8000, avg daily traffic 80, avg ticket 25, "
    "2 employees at 5000/mo, variable cost ratio 35%, total investment 300000",
    "SaaS product, monthly revenue 60000, monthly expense 45000, "
    "total investment 500000, unit price 99, daily traffic 30",
]


def _scan(text: str) -> dict:
    """英文文本过真实引擎 → quick_scan 结构化结果。"""
    from tools.workflow_engine import quick_scan as quick_scan_tool

    raw = quick_scan_tool.invoke({"params_json": text})
    return json.loads(raw)


def _assert_no_cjk(rendered: str, outlet: str) -> None:
    leaked = _CJK.findall(rendered or "")
    assert not leaked, (
        f"[{outlet}] 渲染输出泄漏中文 {len(leaked)} 处（英文产品不得出现中文）: "
        + "; ".join(
            repr(line[:70]) for line in (rendered or "").splitlines() if _CJK.search(line)
        )
    )


# ── 出口 1: format_response（对话主界面）──────────────────────────────────
@pytest.mark.parametrize("text", _EN_INPUTS)
def test_format_response_has_no_cjk(text):
    from router.formatter import format_response

    data = _scan(text)
    out = format_response("quick_scan", data)
    assert isinstance(out, str) and out
    _assert_no_cjk(out, "format_response")


# ── 出口 2: build_report_markdown（Markdown 报告）─────────────────────────
@pytest.mark.parametrize("text", _EN_INPUTS)
def test_build_report_markdown_has_no_cjk(text):
    from tools.report_generator import build_report_markdown

    data = _scan(text)
    md = build_report_markdown(data, title="Monthly Report")
    assert isinstance(md, str) and md
    _assert_no_cjk(md, "build_report_markdown")


# ── 出口 3: render_decision（决策输出）───────────────────────────────────
@pytest.mark.parametrize("text", _EN_INPUTS)
def test_render_decision_has_no_cjk(text):
    from decision_engine import decide, render_decision

    data = _scan(text)
    decision = decide(
        "go_no_go", data, current_params=data.get("params", {}), user_text=text
    )
    out = render_decision(decision)
    assert isinstance(out, str) and out
    _assert_no_cjk(out, "render_decision")


# ── 出口 4: format_advice（LLM 建议格式化）───────────────────────────────
@pytest.mark.parametrize("text", _EN_INPUTS)
def test_format_advice_has_no_cjk(text):
    from advisor.advisor_formatter import format_advice

    data = _scan(text)
    # format_advice(advice, clean_view, param_sources)：advice 用 llm_advisor.advise
    # 的输出形状 {text, ops}，clean_view/param_sources 取自同一扫描，保证真实数据面。
    advice = {"text": "Consider raising the unit price.", "ops": []}
    out = format_advice(advice, data, data.get("param_sources", {}))
    # 返回 dict（{judgment, risks, actions, citations, ...}）：序列化后查 CJK，
    # 覆盖所有渲染给用户的字符串字段。
    assert isinstance(out, dict) and out
    _assert_no_cjk(json.dumps(out, ensure_ascii=False), "format_advice")


# ── 引擎数据标记专项：runway「无限」映射 ──────────────────────────────────
def test_runway_infinite_mark_is_mapped_not_leaked():
    """`runway_months == \"无限\"` 是引擎数据标记，报表里必须映射成 Unlimited。

    实战：护栏首跑抓到 build_report_markdown 直接印出「无限」，已修复。
    此用例锁死该行为，防止回归。
    """
    from tools.report_generator import _cell

    assert _cell("无限") == "Unlimited", _cell("无限")
    _assert_no_cjk(_cell("无限"), "_cell(infinite mark)")
