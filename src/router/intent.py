"""意图路由器 — 不调 LLM 的纯规则路由

9 类意图，按关键词/正则匹配：
- quick_scan: 描述项目 / 改参数
- trend: 趋势/预测/未来/走势
- compare: 对比/A vs B/如果
- suggest: 怎么调/扭亏/建议改
- report_pdf: 生成PDF
- report_excel: 生成Excel
- benchmark: 行业基准/数据
- market: 市场调研/竞品
- chitchat: 兜底（走 LLM）

匹配顺序：先匹配更具体的（suggest/compare/report），后匹配宽泛的（quick_scan/chitchat）。
多意图：按句号/问号切分输入，每段独立判意图，取最具体的非 chitchat 段为主意图。
"""

import re
from typing import Tuple, Optional, List

from . import rules


# ─── 意图规则 ──────────────────────────────────────────────────────────────
# 每条规则: (intent_name, [keyword_list], [regex_list])
# 命中其中任一关键词或正则即匹配

# ─── 意图规则 ──────────────────────────────────────────────────────────────
# 数据化：13 类意图的关键词/正则已迁到 src/router/rules/{zh,en}.yaml。
# 结构不变：每条规则仍是三元组 (意图名, 关键词列表, 正则列表)，命中任一即匹配。
# 匹配顺序不变：先匹配更具体的（report/suggest/compare），后匹配宽泛的
# （quick_scan/chitchat）——由 intent_priority 与 yaml 书写顺序共同保证。
#
# 此处保留 **zh 快照**绑定供向后兼容；实际路由走 rules.intent_rules()。
_RULES = rules.intent_rules("zh")


# 意图优先级（数字越大越优先）
# 意图优先级（数字越大越优先）——数据化到 rules/{zh,en}.yaml 的 intent_priority。
# 保留 zh 快照绑定供向后兼容；实际路由走 rules.intent_priority()。
_INTENT_PRIORITY = rules.intent_priority("zh")


# 核心「数值型」项目字段——命中任一即视为「用户在供给/修改参数」。
# 注意：不含 industry（纯行业词如「餐饮」不应强制走 quick_scan，否则会
# 劫持 benchmark/market 类提问）。仅数值字段才强制作重算。
_CORE_PARAM_FIELDS = {
    "monthly_revenue", "monthly_rent", "total_investment", "price_per_unit",
    "daily_traffic", "employee_count", "avg_salary", "monthly_expense",
    "variable_cost_rate", "variable_cost_ratio", "founder_count", "city",
}


def _looks_like_param_update(text: str) -> bool:
    """文本是否携带了核心项目数值参数。

    用于兜底：当用户只说「月营收20000元」「月租金8000元」这类纯补参句子时，
    关键词路由会误判为 chitchat，导致该轮不写 SessionState、跨轮累积断裂。
    命中核心数值字段则强制走 quick_scan。
    """
    try:
        from router.param_extractor import extract_params
    except Exception:  # pragma: no cover - 极端导入失败
        return False
    try:
        p = extract_params(text)
    except Exception:  # pragma: no cover
        return False
    if any(f in p for f in _CORE_PARAM_FIELDS):
        return True
    # 守门层拦截/修正过参数（如「6000%」）也视为参数更新，让 quick_scan 渲染确认横幅，
    # 而不是落入 chitchat 把「请确认」信号吞掉
    g = p.get("_guard") if isinstance(p, dict) else None
    if isinstance(g, dict) and (g.get("needs_confirmation") or g.get("issues")):
        return True
    return False



# 对比标记词走规则层（locale 感知）：识别不出 "which is better" 时
# compare 意图静默降级成 quick_scan，用户永远拿不到方案对比。
# 中文正则「好…还是 / 还是…好」是语言结构本身，保留为显式分支。
_ZH_COMPARE_RES = (
    re.compile(r"好\s*[，,]?\s*还是"),
    re.compile(r"还是\s*[^，。？！]*好"),
    re.compile(r"方案\s*[abAB]"),
)


def _has_strong_compare(text: str) -> bool:
    raw = text or ""
    low = raw.lower()
    if any(m.lower() in low for m in rules.strong_compare_markers()):
        return True
    if re.search(r"\bvs\.?\b", raw, re.IGNORECASE):
        return True
    return any(rx.search(raw) for rx in _ZH_COMPARE_RES)


def _finalize(intent: str, score: float, text: str,
              has_base: bool = False) -> Tuple[str, float]:
    """最终意图裁定（含兜底）。

    Phase 3 修复：纯 chitchat 但文本含核心项目参数时，强制走 quick_scan，
    让引擎基于 SessionState 合并后的参数重算，避免跨轮累积的参数被「闲聊」
    路径吞掉而丢失。

    L2 决策：文本含强决策问句（该不该/要不要开或续/先验证/撑多久/值不值）时，
    即使夹带参数描述也应归 decide，让规则层给「选项+风险+验证」而非纯仪表盘。

    弱对比词（如果/假如/假设/要是）：仅当会话已有 base、或句内确有对比结构
    时才保留 compare；否则降为 quick_scan 并允许 merge。
    """
    if intent == "compare" and not _has_strong_compare(text):
        if any(m.lower() in (text or "").lower() for m in rules.weak_compare_markers()):
            if not has_base:
                intent = "quick_scan" if _looks_like_param_update(text) else "chitchat"
    if intent == "chitchat" and _looks_like_param_update(text):
        return ("quick_scan", max(score, 0.9))
    if intent in ("quick_scan", "chitchat") and _looks_like_decision_question(text):
        return ("decide", max(score, 0.9))
    if intent in ("quick_scan", "chitchat") and _looks_like_cashflow_question(text):
        return ("cashflow", max(score, 0.9))
    return (intent, score)


# 决策/现金流问句标记也走规则层：识别不出 "should I continue" 就不会给
# 「选项+风险+验证」，落回纯仪表盘 —— 症状看起来像功能缺失，实为识别没命中。


def _looks_like_decision_question(text: str) -> bool:
    """文本是否含 L2 决策问句（夹带参数也应路由 decide）。"""
    low = (text or "").lower()
    return any(m.lower() in low for m in rules.decision_markers())


def _looks_like_cashflow_question(text: str) -> bool:
    """文本是否含现金流明细问句（夹带参数也应路由 cashflow；「撑多久」归 decide）。"""
    low = (text or "").lower()
    return any(m.lower() in low for m in rules.cashflow_markers())


def _split_clauses(text: str) -> List[str]:
    """按句末标点切分文本为多个子句"""
    # 按 . ? ! 。？！ 切分
    parts = re.split(r"[。.？！?!;；\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def _score_intent(text: str) -> Tuple[str, float]:
    """对单段文本判意图，返回 (intent, score)"""
    if not text or not text.strip():
        return ("chitchat", 0.0)

    text_lower = text.lower()
    matches = []

    for intent, keywords, regexes in rules.intent_rules():
        score = 0.0
        for kw in keywords:
            if kw in text or kw in text_lower:
                score += 1.0
            elif kw.lower() in text_lower:
                score += 0.8
        for rgx in regexes:
            if re.search(rgx, text, re.IGNORECASE):
                score += 1.5
        if score > 0:
            matches.append((intent, score))

    if not matches:
        return ("chitchat", 0.0)

    matches.sort(key=lambda x: -x[1])
    return matches[0]


def detect_intent(text: str, has_base: bool = False) -> Tuple[str, float]:
    """
    检测用户输入的意图。
    多子句时按"优先级最高的非 chitchat 子句"选主意图。

    has_base: 会话里是否已有项目参数。弱对比词（如果/假如）仅在已有 base
    时才进 compare，避免首句「如果租金是8000」被当成假设分析且不入 session。

    返回 (intent_name, score)。
    """
    if not text or not text.strip():
        return ("chitchat", 0.0)

    # 多子句: 按优先级选最具体的
    clauses = _split_clauses(text)
    if len(clauses) > 1:
        # 每段判意图，按 priority 排序
        scored = []
        for clause in clauses:
            intent, score = _score_intent(clause)
            scored.append((intent, score))
        # 按 priority 降序
        scored.sort(key=lambda x: -rules.intent_priority().get(x[0], 0))
        # 取第一个非 chitchat
        for intent, score in scored:
            if intent != "chitchat":
                return _finalize(intent, score, text, has_base=has_base)
        intent, score = (scored[0] if scored else ("chitchat", 0.0))
        return _finalize(intent, score, text, has_base=has_base)

    # 单子句
    intent, score = _score_intent(text)
    return _finalize(intent, score, text, has_base=has_base)


def detect_intent_safe(text: str, has_base: bool = False) -> str:
    """便捷版本：只返回意图名。"""
    intent, _ = detect_intent(text, has_base=has_base)
    return intent


def decide_type_of(text: str) -> str:
    """从决策类问句提取 L2 决策类型（供 web_server 子路由）。

    返回 turnaround / validate_first / go_no_go / continue_stop / runway / choose。
    兜底：无法归类时给 turnaround（默认高频）。

    标记词走规则层（locale 分桶）：英文部署若只剩中文词表，所有英文决策问句
    都会落回默认 turnaround（静默错配成错误的分析类型）。
    """
    if not text:
        return "turnaround"
    low = text.strip().lower()
    for subtype, markers in rules.decision_subtypes().items():
        for k in markers:
            if k.lower() in low:
                return subtype
    return "turnaround"
