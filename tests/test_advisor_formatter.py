"""顾问面板格式化器测试。

覆盖：
- 数字防火墙：过滤 LLM 输出中未在参数面板出现的数字
- 来源过滤：过滤 [候选] 值的引用
- 风险解析：从文本中匹配风险关键词
- 动作解析：从 ops 中提取 propose
- 参数版本哈希：基于 6 关键字段的 md5
"""

import pytest
from advisor.advisor_formatter import (
    format_advice,
    validate_no_computed_numbers,
    _filter_citations,
    _parse_risks_from_text,
    _parse_actions_from_ops,
)


class TestValidateNoComputedNumbers:
    """数字防火墙测试。"""

    def test_keeps_known_numbers(self):
        """参数面板中已有的数字应保留。"""
        clean_view = {"params": {"monthly_rent": 15000, "daily_traffic": 50}}
        text = "月租金 15000 元，日均客流 50 人"
        result = validate_no_computed_numbers(text, clean_view)
        assert "15000" in result
        assert "50" in result

    def test_filters_unknown_numbers(self):
        """参数面板中没有的数字应被过滤。"""
        clean_view = {"params": {"monthly_rent": 15000}}
        text = "Monthly fixed cost is roughly 33000 dollars"
        result = validate_no_computed_numbers(text, clean_view)
        assert "[number filtered]" in result
        assert "33000" not in result

    def test_empty_text(self):
        """空文本不处理。"""
        assert validate_no_computed_numbers("", {"params": {}}) == ""

    def test_no_params(self):
        """无参数面板时所有数字被过滤。"""
        result = validate_no_computed_numbers("number 123", {"params": {}})
        assert "[number filtered]" in result


class TestFilterCitations:
    """来源过滤测试。"""

    def test_filters_candidate_source(self):
        """[候选] 值的引用应被过滤。"""
        citations = [
            {"field": "monthly_rent", "value": 15000, "source": "[用户]"},
            {"field": "variable_cost_ratio", "value": 0.4, "source": "[候选]"},
        ]
        param_sources = {
            "monthly_rent": "[用户]",
            "variable_cost_ratio": "[候选]",
        }
        result = _filter_citations(citations, param_sources)
        assert len(result) == 1
        assert result[0]["field"] == "monthly_rent"

    def test_keeps_user_and_derived(self):
        """[用户] 和 [推算] 的引用应保留。"""
        citations = [
            {"field": "monthly_rent", "value": 15000, "source": "[用户]"},
            {"field": "monthly_revenue", "value": 75000, "source": "[推算]"},
        ]
        param_sources = {
            "monthly_rent": "[用户]",
            "monthly_revenue": "[推算]",
        }
        result = _filter_citations(citations, param_sources)
        assert len(result) == 2


class TestParseRisksFromText:
    """风险解析测试。"""

    def test_parses_risk_lines(self):
        """匹配含风险关键词的行。"""
        text = "Risk: pricing is close to variable cost, leaving little room to raise prices.\nThis is normal analysis content."
        result = _parse_risks_from_text(text)
        assert len(result) == 1
        assert "Risk" in result[0]["text"]

    def test_empty_text(self):
        """空文本返回空列表。"""
        assert _parse_risks_from_text("") == []


class TestParseActionsFromOps:
    """动作解析测试。"""

    def test_parses_valid_ops(self):
        """提取含 propose 的 op。"""
        ops = [{"propose": {"field": "monthly_rent", "value": 12000}}]
        result = _parse_actions_from_ops(ops)
        assert len(result) == 1
        assert result[0]["op"]["field"] == "monthly_rent"

    def test_skips_invalid_ops(self):
        """跳过不含 propose 的 op。"""
        ops = [{"invalid": "data"}]
        result = _parse_actions_from_ops(ops)
        assert result == []


class TestFormatAdvice:
    """format_advice 综合测试。"""

    def test_basic_structure(self):
        """输出结构完整。"""
        advice = {"text": "固定成本占比偏高。", "ops": []}
        clean_view = {"params": {"monthly_rent": 15000}}
        result = format_advice(advice, clean_view)
        assert "judgment" in result
        assert "judgment_citations" in result
        assert "risks" in result
        assert "actions" in result
        assert "citations" in result
        assert "params_version" in result

    def test_filters_numbers_in_judgment(self):
        """judgment 中的未知数字被过滤。"""
        advice = {"text": "Monthly fixed cost is roughly 33000 dollars", "ops": []}
        clean_view = {"params": {"monthly_rent": 15000}}
        result = format_advice(advice, clean_view)
        assert "[number filtered]" in result["judgment"]

    def test_params_version_consistency(self):
        """相同参数产生相同版本号。"""
        advice = {"text": "test", "ops": []}
        clean_view = {"params": {"monthly_rent": 15000}}
        r1 = format_advice(advice, clean_view)
        r2 = format_advice(advice, clean_view)
        assert r1["params_version"] == r2["params_version"]

    def test_empty_advice(self):
        """空 advice 不崩溃。"""
        result = format_advice({"text": "", "ops": []}, {"params": {}})
        assert result["judgment"] == ""
        assert result["citations"] == []
