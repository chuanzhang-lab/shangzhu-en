"""
参数调整建议器 — 工具侧智能

不做 LLM 心算，所有数字都从当前参数 + benchmark 推算。
LLM 调用此工具拿到硬数据后，可以叠加自己的判断和补充。

工作流：
1. 检测问题参数（亏损、跑道短、毛利率低、客单价异常等）
2. 对每个问题参数生成调整方向（用反推公式：达到盈亏平衡需要什么）
3. 计算调整后预估利润改善
"""

import json

from i18n import t
from langchain.tools import tool

from tools.workflow_engine import _fill_params, _resolve_industry, INDUSTRY_TEMPLATES, FALLBACK_TEMPLATE, _parse_tool_input
# 时间口径唯一出处（F1）：不要在本文件再写 `* 30`。
from field_model import DAYS_PER_MONTH


# ─── 行业典型客单价区间（USD / 单件）───────────────────────────────────────
# 唯一出处：既用于「客单价偏低」判定，也用于提价建议的目标区间。
# 旧实现把「低于 15 元」硬编码在 _check_issues 里、把 (20,35) 硬编码在
# _suggest_price_up 里 —— 两处各写一遍金额，改币种时必然只改一处、另一处悄悄失真。
#
# 口径：美国市场，2026-10 设定。不是人民币折算—— ¥20-35 ≈ $2.8-4.9，
# 在美国餐饮语境下是荒谬的低价；同理零售/宠物的数值在美元下反而基本可用。
TYPICAL_UNIT_PRICE_RANGES = {
    "餐饮": (12, 22),   # 快餐/休闲餐一份主食（$12-22）
    "零售": (30, 80),   # 专营店平均客单（$30-80）
    "宠物": (50, 150),  # 美容/基础诊疗一次（$50-150）
}
# 「保守提价」的最小步长（USD）。旧值 +5 是按人民币写的，
# 对一份 $12 的餐意味着一次涨 42%，与「保守」名不符实。
_CONSERVATIVE_PRICE_STEP = 2


# ─── 建议规则 ────────────────────────────────────────────────────────────────

def _check_issues(params: dict) -> list[dict]:
    """
    检查参数中的问题。

    返回问题列表，每项包含：
    - code: 问题代码
    - severity: critical / high / medium
    - target_param: 需要调整的参数
    - message: 简短描述
    """
    issues = []
    industry = params.get("industry_name", "")
    tpl = _resolve_industry(industry)
    benchmark_gm = tpl.get("gross_margin", 50)

    # ── 1. 亏损 ──
    if params["monthly_profit"] < 0:
        issues.append({
            "code": "negative_profit",
            "severity": "critical",
            "target_param": "multiple",
            "message": t("pa.issue.negative_profit",
                         loss=f"{abs(round(params['monthly_profit'])):,}"),
        })

    # ── 2. 跑道不足 ──
    if params.get("available_cash", 0) > 0:
        monthly_burn = params["monthly_fixed_cost"] + params["monthly_variable_cost"] - params["monthly_revenue"]
        if monthly_burn > 0:
            runway = params["available_cash"] / monthly_burn
            if runway < 6:
                issues.append({
                    "code": "short_runway",
                    "severity": "critical",
                    "target_param": "monthly_fixed_cost",
                    "message": t("pa.issue.short_runway", runway=f"{runway:.1f}"),
                })
            elif runway < 12:
                issues.append({
                    "code": "tight_runway",
                    "severity": "high",
                    "target_param": "monthly_fixed_cost",
                    "message": t("pa.issue.tight_runway", runway=f"{runway:.1f}"),
                })

    # ── 3. 客单价异常低（vs 行业 benchmark）──
    price = params.get("price_per_unit", 0)
    if price > 0 and industry in INDUSTRY_TEMPLATES:
        # 阈值不写死金额，直接取「行业典型区间下限」——金额只在
        # TYPICAL_UNIT_PRICE_RANGES 里出现一次，改币种不会漏改。
        pr = TYPICAL_UNIT_PRICE_RANGES.get(industry)
        if pr and price < pr[0]:
            issues.append({
                "code": "low_price",
                "severity": "high",
                "target_param": "price_per_unit",
                "message": t("pa.issue.low_price", price=price),
            })

    # ── 4. 毛利率偏离 ──
    actual_gm = (1 - params["variable_cost_ratio"]) * 100
    if actual_gm < benchmark_gm - 10:
        issues.append({
            "code": "low_margin",
            "severity": "high",
            "target_param": "variable_cost_ratio",
            "message": t("pa.issue.low_margin", gm=f"{actual_gm:.0f}", bench=benchmark_gm),
        })

    # ── 5. 月租占比过高 ──
    monthly_rev = params.get("monthly_revenue", 0)
    monthly_rent = params.get("monthly_rent", 0)
    if monthly_rev > 0 and monthly_rent > 0:
        rent_ratio = monthly_rent / monthly_rev
        if rent_ratio > 0.25:
            issues.append({
                "code": "high_rent",
                "severity": "high",
                "target_param": "monthly_rent",
                "message": t("pa.issue.high_rent", ratio=f"{rent_ratio*100:.0f}"),
            })

    # ── 6. 客流量低于盈亏平衡 ──
    if params.get("daily_traffic", 0) > 0 and industry in INDUSTRY_TEMPLATES:
        # 反推盈亏平衡客流：固定成本+变动成本 = 收入
        # traffic * price * 30 * (1-vc) = 固定成本
        # traffic = 固定成本 / (price * 30 * (1-vc))
        if price > 0 and params["variable_cost_ratio"] < 1:
            breakeven_traffic = params["monthly_fixed_cost"] / (
                price * DAYS_PER_MONTH * (1 - params["variable_cost_ratio"])
            ) / DAYS_PER_MONTH
            if params["daily_traffic"] < breakeven_traffic * 0.8:
                issues.append({
                    "code": "low_traffic",
                    "severity": "high",
                    "target_param": "daily_traffic",
                    "message": t("pa.issue.low_traffic",
                         pct=f"{params['daily_traffic']/breakeven_traffic*100:.0f}"),
                })

    return issues


def _gen_suggestions(params: dict, issues: list[dict]) -> list[dict]:
    """
    针对每个问题生成具体调整建议。

    每条建议：
    - target_param: 参数名
    - current: 当前值
    - suggested: 建议值
    - direction: 调整方向（"降低" / "提高"）
    - rationale: 调整理由（含计算）
    - expected_profit_delta: 预估月利润变化
    """
    suggestions = []
    seen_params = set()

    for issue in issues:
        if issue["code"] == "negative_profit" and "multiple" not in seen_params:
            seen_params.add("multiple")
            suggestions.extend(_solve_negative_profit(params))

        elif issue["code"] == "short_runway" and "monthly_fixed_cost" not in seen_params:
            seen_params.add("monthly_fixed_cost")
            suggestions.append(_suggest_cut_fixed_cost(params, target_runway=12))

        elif issue["code"] == "tight_runway" and "monthly_fixed_cost" not in seen_params:
            seen_params.add("monthly_fixed_cost")
            suggestions.append(_suggest_cut_fixed_cost(params, target_runway=18))

        elif issue["code"] == "low_price" and "price_per_unit" not in seen_params:
            seen_params.add("price_per_unit")
            suggestions.extend(_suggest_raise_price(params))

        elif issue["code"] == "low_margin" and "variable_cost_ratio" not in seen_params:
            seen_params.add("variable_cost_ratio")
            suggestions.append(_suggest_cut_vc(params))

        elif issue["code"] == "high_rent" and "monthly_rent" not in seen_params:
            seen_params.add("monthly_rent")
            suggestions.append(_suggest_cut_rent(params))

        elif issue["code"] == "low_traffic" and "daily_traffic" not in seen_params:
            seen_params.add("daily_traffic")
            suggestions.append(_suggest_raise_traffic(params))

    return suggestions


def _solve_negative_profit(params: dict) -> list[dict]:
    """亏损时给出组合调整建议（影响最大的两个方向）"""
    loss = abs(params["monthly_profit"])
    suggestions = []

    # 方向 1: 提价覆盖亏损
    price = params.get("price_per_unit", 0)
    traffic = params.get("daily_traffic", 0)
    vc_ratio = params["variable_cost_ratio"]
    if price > 0 and traffic > 0:
        # 需要月增收入 = loss + 当前亏损
        # 提价 delta → 月增收入 = traffic * 30 * (1-vc) * delta_price
        # 即 delta_price = loss / (traffic * 30 * (1 - vc))
        if (1 - vc_ratio) > 0:
            delta = loss / (traffic * DAYS_PER_MONTH * (1 - vc_ratio))
            new_price = round(price + delta, 1)
            suggestions.append({
                "target_param": "price_per_unit",
                "current": price,
                "suggested": new_price,
                "direction": t("pa.dir.up"),
                "rationale": t("pa.sug.raise_price_cover", delta=f"{delta:.1f}",
                               loss=f"{round(loss):,}"),
                "expected_profit_delta": round(loss, 0),
            })

    # 方向 2: 砍固定成本
    fixed = params["monthly_fixed_cost"]
    if fixed > 0:
        new_fixed = max(fixed - loss, fixed * 0.5)
        cut = round(fixed - new_fixed)
        suggestions.append({
            "target_param": "monthly_fixed_cost",
            "current": fixed,
            "suggested": round(new_fixed),
            "direction": t("pa.dir.down"),
            "rationale": t("pa.sug.cut_fixed", cut=f"{cut:,}", cur=f"{fixed:,.0f}",
                               new=f"{round(new_fixed):,}"),
            "expected_profit_delta": round(cut, 0),
        })

    return suggestions


def _suggest_cut_fixed_cost(params: dict, target_runway: int) -> dict:
    """为达到目标跑道月数，需要削减的固定成本"""
    available = params["available_cash"]
    monthly_burn = params["monthly_fixed_cost"] + params["monthly_variable_cost"] - params["monthly_revenue"]
    if monthly_burn <= 0:
        return {
            "target_param": "monthly_fixed_cost",
            "current": params["monthly_fixed_cost"],
            "suggested": params["monthly_fixed_cost"],
            "direction": t("pa.dir.none"),
            "rationale": t("pa.sug.cashflow_positive"),
            "expected_profit_delta": 0,
        }

    target_burn = available / target_runway
    cut = round(monthly_burn - target_burn)
    new_fixed = max(params["monthly_fixed_cost"] - cut, 0)
    return {
        "target_param": "monthly_fixed_cost",
        "current": params["monthly_fixed_cost"],
        "suggested": new_fixed,
        "direction": t("pa.dir.down"),
        "rationale": t("pa.sug.runway_target", months=target_runway, cut=f"{cut:,}"),
        "expected_profit_delta": cut,
    }


def _suggest_raise_price(params: dict) -> list[dict]:
    """提价建议"""
    price = params.get("price_per_unit", 0)
    if price <= 0:
        return []

    industry = params.get("industry_name", "")
    tpl = _resolve_industry(industry)
    benchmark = tpl.get("benchmark", {})
    traffic = params.get("daily_traffic", 0)
    vc_ratio = params["variable_cost_ratio"]

    range_info = TYPICAL_UNIT_PRICE_RANGES.get(industry, (price * 1.2, price * 1.5))

    suggestions = []

    # 保守提价：到区间下限
    conservative = max(price + _CONSERVATIVE_PRICE_STEP, range_info[0])
    delta = conservative - price
    if traffic > 0 and (1 - vc_ratio) > 0:
        profit_gain = traffic * DAYS_PER_MONTH * (1 - vc_ratio) * delta
        suggestions.append({
            "target_param": "price_per_unit",
            "current": price,
            "suggested": round(conservative, 1),
            "direction": t("pa.dir.up"),
            "rationale": t("pa.sug.price_typical", lo=range_info[0], hi=range_info[1],
                               target=f"{round(conservative):,}"),
            "expected_profit_delta": round(profit_gain, 0),
        })

    # 激进提价：到区间中位
    aggressive = (range_info[0] + range_info[1]) / 2
    if aggressive > conservative:
        delta2 = aggressive - price
        profit_gain2 = traffic * DAYS_PER_MONTH * (1 - vc_ratio) * delta2
        suggestions.append({
            "target_param": "price_per_unit",
            "current": price,
            "suggested": round(aggressive, 1),
            "direction": t("pa.dir.up"),
            "rationale": t("pa.sug.price_aggressive", mid=f"{round(aggressive):,}"),
            "expected_profit_delta": round(profit_gain2, 0),
        })

    return suggestions


def _suggest_cut_vc(params: dict) -> dict:
    """降低变动成本率建议"""
    vc = params["variable_cost_ratio"]
    industry = params.get("industry_name", "")
    tpl = _resolve_industry(industry)
    benchmark_gm = tpl.get("gross_margin", 50)
    target_vc = round(1 - benchmark_gm / 100, 2)

    if target_vc >= vc:
        return {
            "target_param": "variable_cost_ratio",
            "current": vc,
            "suggested": vc,
            "direction": t("pa.dir.none"),
            "rationale": t("pa.sug.vc_at_benchmark", gm=benchmark_gm),
            "expected_profit_delta": 0,
        }

    # 计算利润改善
    revenue = params["monthly_revenue"]
    delta_vc = vc - target_vc
    profit_gain = revenue * delta_vc

    return {
        "target_param": "variable_cost_ratio",
        "current": f"{vc*100:.0f}%",
        "suggested": f"{target_vc*100:.0f}%",
        "direction": t("pa.dir.down"),
        "rationale": t("pa.sug.vc_improve", cur=f"{(1-vc)*100:.0f}", target=benchmark_gm),
        "expected_profit_delta": round(profit_gain, 0),
    }


def _suggest_cut_rent(params: dict) -> dict:
    """降租建议（到营收的 15%）"""
    revenue = params["monthly_revenue"]
    current_rent = params["monthly_rent"]
    target_rent = round(revenue * 0.15)
    cut = current_rent - target_rent

    return {
        "target_param": "monthly_rent",
        "current": current_rent,
        "suggested": target_rent,
        "direction": t("pa.dir.down"),
        "rationale": t("pa.sug.rent_cut", target=f"{target_rent:,}", cut=f"{cut:,}"),
        "expected_profit_delta": cut,
    }


def _suggest_raise_traffic(params: dict) -> dict:
    """提客流建议（达到盈亏平衡）"""
    price = params.get("price_per_unit", 0)
    vc = params["variable_cost_ratio"]
    fixed = params["monthly_fixed_cost"]
    current_traffic = params.get("daily_traffic", 0)

    if price <= 0 or (1 - vc) <= 0:
        return {
            "target_param": "daily_traffic",
            "current": current_traffic,
            "suggested": current_traffic,
            "direction": t("pa.dir.none"),
            "rationale": t("pa.sug.traffic_insufficient"),
            "expected_profit_delta": 0,
        }

    breakeven_traffic = (fixed / 30) / (price * (1 - vc))
    delta = round(breakeven_traffic - current_traffic)

    return {
        "target_param": "daily_traffic",
        "current": current_traffic,
        "suggested": round(breakeven_traffic),
        "direction": t("pa.dir.up"),
        "rationale": t("pa.sug.traffic_target", be=f"{round(breakeven_traffic):,}",
                               delta=f"{delta:+,}"),
        "expected_profit_delta": 0,  # 客流改善难精确估算
    }


# ─── 工具 ──────────────────────────────────────────────────────────────────


@tool
def suggest_params(params_json: str) -> str:
    """
    【专项工具】参数调整建议器。

    适用场景（必读）：当用户问"怎么调参数"、"如何扭亏为盈"、"改什么数值好"、"建议改什么"、
    "参数优化"、"如何扭亏"、"调整方案"、"改进方向"时调用此工具。

    与 quick_scan 的区别：
    - quick_scan：输出"现状"（指标/风险/敏感性），不给出"调什么改多少"
    - suggest_params：输出"行动"（每个问题参数的具体调整值 + 调整后利润改善）

    参数:
        params_json: 与 quick_scan 相同的参数格式（自然语言或 JSON）

    返回: JSON 字符串，包含：
        - issues: 检测到的问题列表（严重度+描述）
        - suggestions: 具体调整建议，每个含 target_param（参数名）、current（当前值）、
                      suggested（建议值）、direction（提高/降低）、rationale（理由）、
                      expected_profit_delta（预估月利润改善）
        - summary: 整体调整预期（当前月利润 → 调整后月利润）
    """
    try:
        # 解析输入（兼容 JSON 和自然语言）
        raw, raw_text = _parse_tool_input(params_json)
        raw["_raw_text"] = raw_text
        params, _, mixed_warning = _fill_params(raw)

        # 检测问题
        issues = _check_issues(params)

        if not issues:
            result = {
                "status": "healthy",
                "message": t("pa.summary.healthy"),
                "current_metrics": {
                    "monthly_profit": round(params["monthly_profit"], 0),
                    "monthly_revenue": round(params["monthly_revenue"], 0),
                    "gross_margin": f"{(1-params['variable_cost_ratio'])*100:.0f}%",
                },
                "issues": [],
                "suggestions": [],
            }
            return json.dumps(result, ensure_ascii=False, indent=2)

        # 生成建议
        suggestions = _gen_suggestions(params, issues)

        # 计算总改善
        total_improvement = sum(s.get("expected_profit_delta", 0) for s in suggestions)
        new_profit = params["monthly_profit"] + total_improvement

        result = {
            "status": "needs_adjustment",
            "issues_count": len(issues),
            "issues": issues,
            "suggestions": suggestions,
            "summary": {
                "current_monthly_profit": round(params["monthly_profit"], 0),
                "total_expected_improvement": round(total_improvement, 0),
                "projected_monthly_profit": round(new_profit, 0),
                "verdict": t("pa.summary.turnaround")
                          if new_profit > 0 and params["monthly_profit"] <= 0
                          else t("pa.summary.improve", delta=f"{round(total_improvement):,}")
                          if total_improvement > 0
                          else t("pa.summary.need_more"),
            },
            "note": t("pa.summary.note"),
        }

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("pa.summary.error", err=e)}, ensure_ascii=False)
