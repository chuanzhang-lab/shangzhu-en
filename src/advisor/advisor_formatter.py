"""顾问面板格式化器 — 把 LLM 输出转为顾问面板 JSON。

输入：llm_advisor.advise 的输出 {text, ops} + to_llm_view 的清洁视图
输出：{judgment, judgment_citations, risks, actions, citations, params_version}

约束：
- 不做公式求值（#1 公式唯一出处）
- 不直接写状态（#2 合并安全）
- 数字防火墙：过滤 LLM 输出中任何未在参数面板出现的数字
- 不引用 [候选] 值作为建议依据（#6 保守兜底）
- 引用数字必须带来源标签（#11 来源必标）
"""

import hashlib
import json
import re
from typing import Any, Optional
from i18n import t
from source_tags import CANDIDATE, DERIVED, code_of, mark


def validate_no_computed_numbers(text: str, clean_view: dict) -> str:
    """过滤 LLM 输出中任何未在参数面板中出现的数字（防幻觉）。

    原则：顾问面板可以引用参数面板已有的数字，但不能出现任何"新"数字。
    已知数字 = params 中所有 int/float 值的字符串形式。
    """
    if not text:
        return text

    # 收集参数面板中已知的数字
    known_numbers = set()
    params = clean_view.get("params", {}) if isinstance(clean_view, dict) else {}
    for v in params.values():
        if isinstance(v, (int, float)):
            known_numbers.add(str(int(v)))
            known_numbers.add(str(float(v)))
            # 也加带千分位的版本
            if isinstance(v, (int, float)) and v >= 1000:
                known_numbers.add(f"{v:,.0f}")
                known_numbers.add(f"{int(v):,}")

    # 匹配数字（含千分位和小数）
    number_re = re.compile(r'\d[\d,]*\.?\d*')

    def replace_unknown(m):
        matched = m.group(0)
        return matched if matched in known_numbers else t("af.filtered_number")

    return number_re.sub(replace_unknown, text)


def _filter_citations(citations: list, param_sources: Optional[dict] = None) -> list:
    """过滤掉 [候选] 值的引用（保守兜底原则 #6）。

    如果某个字段的来源是 [候选]，则从 citations 中移除。
    """
    if not param_sources:
        return citations

    filtered = []
    for c in citations:
        field = c.get("field", "")
        # 用**状态码**判断，不拿展示文案做语义判断：
        # 英文下展示串变成 "[Candidate]"，startswith("[候选]") 会静默失效（M4 同类事故）。
        if code_of(param_sources, field) == CANDIDATE:
            continue
        filtered.append(c)
    return filtered


def _build_citations_from_text(text: str, clean_view: dict,
                               param_sources: Optional[dict] = None) -> list:
    """从 LLM 文本中提取引用的参数（基于参数名匹配）。

    返回 [{field, value, source}] 列表。
    """
    citations = []
    params = clean_view.get("params", {}) if isinstance(clean_view, dict) else {}
    if not params:
        return citations

    # 参数名 → 中文标签的反向映射（用于在文本中匹配）
    # 标签跟随 locale：英文面板要在英文 LLM 文本里匹配到对应说法。
    field_labels = {
        "monthly_rent": t("af.label.monthly_rent"),
        "daily_traffic": t("af.label.daily_traffic"),
        "price_per_unit": t("af.label.price_per_unit"),
        "employee_count": t("af.label.employee_count"),
        "avg_salary": t("af.label.avg_salary"),
        "variable_cost_ratio": t("af.label.variable_cost_ratio"),
        "total_investment": t("af.label.total_investment"),
        "monthly_revenue": t("af.label.monthly_revenue"),
        "monthly_profit": t("af.label.monthly_profit"),
        "gross_margin": t("af.label.gross_margin"),
        "monthly_fixed_cost": t("af.label.monthly_fixed_cost"),
        "runway_months": t("af.label.runway_months"),
    }

    for field, value in params.items():
        if value is None:
            continue
        label = field_labels.get(field, field)
        # 检查文本中是否引用了该参数的中文标签或字段名
        if label in text or field in text:
            source = (param_sources or {}).get(field, mark(DERIVED))
            citations.append({
                "field": field,
                "value": value,
                "source": source,
            })

    return citations


def _parse_risks_from_text(text: str) -> list:
    """从 LLM 文本中解析风险段落。

    匹配以"风险"、"注意"、"警告"、"警惕"开头的句子。
    """
    risks = []
    if not text:
        return risks

    # 按行匹配风险关键词
    risk_keywords = ["风险", "注意", "警告", "警惕", "警惕", "小心", "谨防"]
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
        for kw in risk_keywords:
            if kw in line:
                risks.append({"text": line})
                break
    return risks


def _parse_actions_from_ops(ops: list) -> list:
    """从 LLM 输出的 ops 列表中解析建议动作。

    每条 op 应含 propose 字段（如 {"propose": {"field": "monthly_rent", "value": 12000}}）。
    """
    actions = []
    if not ops:
        return actions

    for op in ops:
        if not isinstance(op, dict):
            continue
        propose = op.get("propose")
        if not isinstance(propose, dict):
            continue
        field = propose.get("field", "")
        value = propose.get("value")
        if not field or value is None:
            continue
        actions.append({
            "op": propose,
            "preview": f"{field} → {value}",
        })
    return actions


def format_advice(advice: dict, clean_view: dict,
                  param_sources: Optional[dict] = None) -> dict:
    """格式化 LLM 输出为顾问面板 JSON。

    参数：
        advice: llm_advisor.advise 的输出 {text: str, ops: list}
        clean_view: to_llm_view 的输出 {params, industry, turn, last_changes}
        param_sources: 可选，字段来源标注 {field: "[用户]/[推算]/[候选]/[缺失]"}

    返回：
        {judgment, judgment_citations, risks, actions, citations, params_version}
    """
    text = (advice.get("text") or "") if isinstance(advice, dict) else ""
    ops = (advice.get("ops") or []) if isinstance(advice, dict) else []

    # 数字防火墙：过滤 LLM 输出中任何未在参数面板出现的数字
    filtered_text = validate_no_computed_numbers(text, clean_view)

    # 提取依据标注
    citations = _build_citations_from_text(filtered_text, clean_view, param_sources)
    citations = _filter_citations(citations, param_sources)

    # 解析风险
    risks = _parse_risks_from_text(filtered_text)

    # 解析建议动作
    actions = _parse_actions_from_ops(ops)

    # 参数版本哈希（用于并发控制）
    params = clean_view.get("params", {}) if isinstance(clean_view, dict) else {}
    key_fields = ['monthly_rent', 'daily_traffic', 'price_per_unit',
                  'employee_count', 'avg_salary', 'variable_cost_ratio']
    key_values = {k: params.get(k) for k in key_fields}
    params_version = hashlib.md5(
        json.dumps(key_values, sort_keys=True).encode()
    ).hexdigest()[:8]

    return {
        "judgment": filtered_text,
        "judgment_citations": citations[:5],  # 最多 5 条依据
        "risks": risks[:3],                    # 最多 3 条风险
        "actions": actions[:3],                # 最多 3 个动作
        "citations": citations,
        "params_version": params_version,
    }
