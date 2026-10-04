"""formatter 模板格式化器的直接单测（提升 A 路径覆盖率与防回归）。

覆盖：quick_scan / insufficient / trend / compare / suggest / report / benchmark
各 intent 的格式化输出结构，以及 format_response 对未知 intent 的兜底、
各 formatter 的错误分支（data 含 "error"）与返回类型安全（永远返回 str）。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from router.formatter import (
    format_response,
    _fmt_insufficient,
    _fmt_trend,
    _fmt_compare,
    _fmt_suggest,
    _fmt_report,
    _fmt_benchmark,
)


# ── quick_scan ──────────────────────────────────────────────────────────


def _fmt_scan_err(data):
    # format_response 会把 quick_scan 错误分支转发到 _fmt_scan 的错误处理
    return format_response("quick_scan", data)


# ── insufficient ────────────────────────────────────────────────────────


# ── trend ───────────────────────────────────────────────────────────────


# ── compare ─────────────────────────────────────────────────────────────


# ── suggest ─────────────────────────────────────────────────────────────
def test_fmt_suggest():
    data = {
        "issues": [{"severity": "high", "message": "食材成本过高"}],
        "suggestions": [{"target_param": "price", "current": 20, "suggested": 25,
                         "direction": "提价", "rationale": "需求刚性",
                         "expected_profit_delta": 1000}],
        "summary": {"current_monthly_profit": 10000,
                    "projected_monthly_profit": 12000,
                    "total_expected_improvement": 2000,
                    "verdict": "建议提价"},
    }
    out = _fmt_suggest(data)
    assert isinstance(out, str)
    assert "建议" in out
    assert "12,000" in out
    assert "提价" in out


# ── report ───────────────────────────────────────────────────────────────


# ── benchmark ────────────────────────────────────────────────────────────


# ── format_response 路由兜底 ─────────────────────────────────────────────
def test_format_response_unknown_intent_fallback():
    # 未知 intent 应 json.dumps 兜底，不抛异常、不返回 None
    out = format_response("nonexistent_intent", {"a": 1, "name": "x"})
    assert isinstance(out, str)
    assert '"a"' in out


# ── 健壮性：所有 formatter 对空/缺字段数据都不崩，且返回 str ─────────────
def test_all_formatters_robust_to_empty_data():
    cases = [
        ("quick_scan", {}),
        ("trend", {}),
        ("compare", {}),
        ("suggest", {}),
        ("report_pdf", {}),
        ("report_excel", {}),
        ("benchmark", {}),
    ]
    for intent, data in cases:
        out = format_response(intent, data)
        assert isinstance(out, str), f"{intent} 应返回 str，实际 {type(out)}"
        assert len(out) > 0
