"""
陷阱检测工具 — 创业者军师避坑引擎

内置创业常见硬伤规则库，覆盖定价、现金流、市场、团队、合规五大类。
自动触发检测，给出具体警告和可行对策。

设计说明：
- 核心逻辑提取为普通函数（_xxx），供 run_full_pitfall_scan 复用
- @tool 装饰的函数只做薄封装，调用对应的普通函数
- 避免 @tool 函数之间互相调用（会导致 'StructuredTool' object is not callable）
"""

import json
from typing import Optional
from langchain.tools import tool

from i18n import t
# 司法辖区数据（证照原名，永不翻译）—— 见 compliance_map.py 的说明
from tools.pitfall_markers import no_competitor_markers, tam_huge_markers
from tools.compliance_map import INDUSTRY_COMPLIANCE_MAP


# ─── 公共规则库 ──────────────────────────────────────────────────────────



def _check_compliance_keywords(description: str) -> list[str]:
    """从项目描述中匹配行业合规关键词"""
    found = []
    for keyword, licenses in INDUSTRY_COMPLIANCE_MAP.items():
        if keyword in description:
            found.extend(licenses)
    return list(set(found))


# ─── 核心逻辑（普通函数，可被 tool 和 run_full_pitfall_scan 复用）─────────

def _do_pricing_check(
    price: float,
    variable_cost: float,
    competitor_price: Optional[float] = None,
    industry_avg_margin: Optional[float] = None,
) -> dict:
    """定价陷阱检测核心逻辑"""
    pitfalls = []
    suggestions = []
    severity = "low"

    if price <= variable_cost:
        pitfalls.append({
            "code": "price_below_cost",
            "type": t("pt.price_below_cost.type"),
            "severity": "critical",
            "detail": t("pt.price_below_cost.detail", price=price, vc=variable_cost),
            "impact": t("pt.price_below_cost.impact"),
        })
        suggestions.append(t("pt.price_below_cost.action"))
        severity = "critical"

    margin = (price - variable_cost) / price * 100 if price > 0 else 0
    if margin < 20:
        pitfalls.append({
            "code": "low_margin",
            "type": t("pt.low_margin.type"),
            "severity": "high",
            "detail": t("pt.low_margin.detail", margin=f"{round(margin, 1)}"),
            "impact": t("pt.low_margin.impact"),
        })
        suggestions.append(t("pt.low_margin.action", target=f"{round(variable_cost / 0.5, 2)}"))
        if severity != "critical":
            severity = "high"

    if competitor_price and price < competitor_price * 0.5:
        pitfalls.append({
            "code": "price_far_below_competitor",
            "type": t("pt.price_far_below_competitor.type"),
            "severity": "medium",
            "detail": t("pt.price_far_below_competitor.detail", price=price, comp=competitor_price),
            "impact": t("pt.price_far_below_competitor.impact"),
        })
        suggestions.append(t("pt.price_far_below_competitor.action"))

    if industry_avg_margin and margin < industry_avg_margin * 0.7:
        pitfalls.append({
            "code": "margin_below_industry",
            "type": t("pt.margin_below_industry.type"),
            "severity": "medium",
            "detail": t("pt.margin_below_industry.detail", margin=f"{round(margin, 1)}",
                        avg=industry_avg_margin),
            "impact": t("pt.margin_below_industry.impact"),
        })
        suggestions.append(t("pt.margin_below_industry.action"))

    result = {
        "analyzed": {"price": price, "variable_cost": variable_cost, "gross_margin": f"{round(margin, 1)}%"},
        "pitfall_count": len(pitfalls),
        "severity": severity,
        "pitfalls": pitfalls,
        "suggestions": suggestions,
    }
    if not pitfalls:
        result["message"] = t("pt.msg.pricing_ok")
    return result


def _do_cashflow_check(
    current_cash: float,
    monthly_expense: float,
    monthly_revenue: float = 0,
    accounts_receivable_days: float = 0,
    accounts_payable_days: float = 0,
    inventory_turnover_days: float = 0,
) -> dict:
    """现金流陷阱检测核心逻辑"""
    pitfalls = []
    suggestions = []
    severity = "low"

    net_burn = (monthly_expense or 0) - (monthly_revenue or 0)
    current_cash = current_cash if current_cash is not None else 0
    if net_burn > 0:
        runway = current_cash / net_burn
        if runway < 6:
            pitfalls.append({
                "code": "runway_critical",
                "type": t("pt.runway_critical.type"),
                "severity": "critical",
                "detail": t("pt.runway_critical.detail", months=f"{round(runway, 1)}"),
                "impact": t("pt.runway_critical.impact"),
            })
            suggestions.append(t("pt.runway_critical.action"))
            severity = "critical"
        elif runway < 12:
            pitfalls.append({
                "code": "runway_tight",
                "type": t("pt.runway_tight.type"),
                "severity": "high",
                "detail": t("pt.runway_tight.detail", months=f"{round(runway, 1)}"),
                "impact": t("pt.runway_tight.impact"),
            })
            suggestions.append(t("pt.runway_tight.action"))
            if severity != "critical":
                severity = "high"

    if accounts_receivable_days > 0 and accounts_payable_days > 0:
        gap = accounts_receivable_days - accounts_payable_days
        if gap > 30:
            pitfalls.append({
                "code": "cash_gap",
                "type": t("pt.cash_gap.type"),
                "severity": "high",
                "detail": t("pt.cash_gap.detail", ar=accounts_receivable_days,
                            ap=accounts_payable_days, gap=f"{round(gap)}"),
                "impact": t("pt.cash_gap.impact"),
            })
            suggestions.append(t("pt.cash_gap.action", target=f"{round(accounts_payable_days)}"))

    if inventory_turnover_days > 90:
        pitfalls.append({
            "code": "slow_inventory",
            "type": t("pt.slow_inventory.type"),
            "severity": "medium",
            "detail": t("pt.slow_inventory.detail", days=inventory_turnover_days),
            "impact": t("pt.slow_inventory.impact"),
        })
        suggestions.append(t("pt.slow_inventory.action"))

    if monthly_revenue == 0 and monthly_expense > 0:
        pitfalls.append({
            "code": "zero_revenue_burn",
            "type": t("pt.zero_revenue_burn.type"),
            "severity": "high",
            "detail": t("pt.zero_revenue_burn.detail"),
            "impact": t("pt.zero_revenue_burn.impact"),
        })
        suggestions.append(t("pt.zero_revenue_burn.action"))
        if severity != "critical":
            severity = "high"

    result = {
        "analyzed": {
            "current_cash": current_cash,
            "monthly_expense": monthly_expense,
            "monthly_revenue": monthly_revenue,
            "net_monthly_burn": round(net_burn, 2),
                        # 「无限」是引擎靠等值比较识别的数据标记，直接进 analyzed 会让英文版漏中文
            "runway_months": (round(current_cash / net_burn, 1) if net_burn > 0
                              else t("fmt.common.infinite")),
        },
        "pitfall_count": len(pitfalls),
        "severity": severity,
        "pitfalls": pitfalls,
        "suggestions": suggestions,
    }
    if not pitfalls:
        result["message"] = t("pt.msg.cashflow_ok")
    return result


def _do_market_check(
    tam_description: str,
    team_size: int,
    has_competitor: bool = True,
    customer_concentration: Optional[float] = None,
    description: str = "",
) -> dict:
    """市场陷阱检测核心逻辑"""
    pitfalls = []
    suggestions = []
    severity = "low"

    # 输入层匹配词（含英文词表）——见 pitfall_markers.py：只留中文会让英文描述永不触发
    huge_market_keywords = tam_huge_markers()
    is_huge = any(kw in tam_description for kw in huge_market_keywords)
    if is_huge and team_size < 20:
        pitfalls.append({
            "code": "tam_too_broad",
            "type": t("pt.tam_too_broad.type"),
            "severity": "medium",
            "detail": t("pt.tam_too_broad.detail", team=team_size),
            "impact": t("pt.tam_too_broad.impact"),
        })
        suggestions.append(t("pt.tam_too_broad.action"))

    no_competitor_signals = no_competitor_markers()
    if not has_competitor or any(sig in description for sig in no_competitor_signals):
        pitfalls.append({
            "code": "claims_no_competitor",
            "type": t("pt.claims_no_competitor.type"),
            "severity": "high",
            "detail": t("pt.claims_no_competitor.detail"),
            "impact": t("pt.claims_no_competitor.impact"),
        })
        suggestions.append(t("pt.claims_no_competitor.action"))

    if customer_concentration is not None and customer_concentration > 40:
        pitfalls.append({
            "code": "customer_concentration",
            "type": t("pt.customer_concentration.type"),
            "severity": "high",
            "detail": t("pt.customer_concentration.detail", pct=customer_concentration),
            "impact": t("pt.customer_concentration.impact"),
        })
        suggestions.append(t("pt.customer_concentration.action"))

    result = {
        "analyzed": {
            "tam_description": tam_description[:100],
            "team_size": team_size,
            "has_competitor": has_competitor,
            "customer_concentration": (f"{customer_concentration}%" if customer_concentration
                                   else t("pt.analyzed.not_provided")),
        },
        "pitfall_count": len(pitfalls),
        "severity": severity,
        "pitfalls": pitfalls,
        "suggestions": suggestions,
    }
    if not pitfalls:
        result["message"] = t("pt.msg.market_ok")
    return result


def _do_team_check(
    founder_count: int,
    has_tech_cofounder: bool = True,
    is_tech_project: bool = False,
    has_domain_expert: bool = True,
    industry: str = "",
) -> dict:
    """团队陷阱检测核心逻辑"""
    pitfalls = []
    suggestions = []
    # 无默认值：创始人信息缺失（None）→ 不虚构团队结构，返回空结果。
    if founder_count is None:
        return {
            "analyzed": {"founder_count": None, "has_tech_cofounder": has_tech_cofounder,
                         "is_tech_project": is_tech_project, "has_domain_expert": has_domain_expert},
            "pitfall_count": 0, "severity": "low",
            "pitfalls": [], "suggestions": [],
            "message": t("pt.msg.team_skipped"),
        }
    severity = "low"

    if founder_count < 2:
        pitfalls.append({
            "code": "solo_founder",
            "type": t("pt.solo_founder.type"),
            "severity": "high",
            "detail": t("pt.solo_founder.detail"),
            "impact": t("pt.solo_founder.impact"),
        })
        suggestions.append(t("pt.solo_founder.action"))

    if is_tech_project and not has_tech_cofounder:
        pitfalls.append({
            "code": "no_tech_cofounder",
            "type": t("pt.no_tech_cofounder.type"),
            "severity": "critical",
            "detail": t("pt.no_tech_cofounder.detail"),
            "impact": t("pt.no_tech_cofounder.impact"),
        })
        suggestions.append(t("pt.no_tech_cofounder.action"))
        severity = "critical"

    if not has_domain_expert:
        pitfalls.append({
            "code": "no_domain_expert",
            "type": t("pt.no_domain_expert.type"),
            "severity": "medium",
            "detail": t("pt.no_domain_expert.detail"),
            "impact": t("pt.no_domain_expert.impact"),
        })
        suggestions.append(t("pt.no_domain_expert.action"))

    result = {
        "analyzed": {
            "founder_count": founder_count,
            "has_tech_cofounder": has_tech_cofounder,
            "is_tech_project": is_tech_project,
            "has_domain_expert": has_domain_expert,
        },
        "pitfall_count": len(pitfalls),
        "severity": severity,
        "pitfalls": pitfalls,
        "suggestions": suggestions,
    }
    if not pitfalls:
        result["message"] = t("pt.msg.team_ok")
    return result


def _do_compliance_check(project_description: str, industry: str = "") -> dict:
    """合规陷阱检测核心逻辑"""
    text = project_description + industry
    licenses = _check_compliance_keywords(text)

    result = {
        "analyzed_industry_keywords": [kw for kw in INDUSTRY_COMPLIANCE_MAP if kw in text],
        "required_licenses": licenses,
        "pitfall_count": len(licenses),
    }

    if licenses:
        result["severity"] = "high"
        result["warning"] = t("pt.license_required.warning", n=len(licenses))
        result["suggestion"] = t("pt.license_required.action")
    else:
        result["severity"] = "low"
        result["message"] = t("pt.license_none.message")
        result["suggestion"] = t("pt.license_none.action")

    return result


# ─── @tool 薄封装（每个 tool 调用对应的 _do_xxx 函数）────────────────────


@tool
def detect_pricing_pitfalls(
    price: float,
    variable_cost: float,
    competitor_price: Optional[float] = None,
    industry_avg_margin: Optional[float] = None,
) -> str:
    """检测定价陷阱。参数: price(定价), variable_cost(变动成本), competitor_price(竞品价格,可选), industry_avg_margin(行业平均毛利率%,可选)"""
    return json.dumps(_do_pricing_check(price, variable_cost, competitor_price, industry_avg_margin), ensure_ascii=False, indent=2)


@tool
def detect_cashflow_pitfalls(
    current_cash: float,
    monthly_expense: float,
    monthly_revenue: float = 0,
    accounts_receivable_days: float = 0,
    accounts_payable_days: float = 0,
    inventory_turnover_days: float = 0,
) -> str:
    """检测现金流陷阱。参数: current_cash(现金余额), monthly_expense(月支出), monthly_revenue(月收入), accounts_receivable_days, accounts_payable_days, inventory_turnover_days"""
    return json.dumps(_do_cashflow_check(current_cash, monthly_expense, monthly_revenue, accounts_receivable_days, accounts_payable_days, inventory_turnover_days), ensure_ascii=False, indent=2)


@tool
def detect_market_pitfalls(
    tam_description: str,
    team_size: int,
    has_competitor: bool = True,
    customer_concentration: Optional[float] = None,
    description: str = "",
) -> str:
    """检测市场陷阱。参数: tam_description(TAM描述), team_size(团队人数), has_competitor, customer_concentration(最大客户占比%), description(项目描述)"""
    return json.dumps(_do_market_check(tam_description, team_size, has_competitor, customer_concentration, description), ensure_ascii=False, indent=2)


@tool
def detect_team_pitfalls(
    founder_count: int,
    has_tech_cofounder: bool = True,
    is_tech_project: bool = False,
    has_domain_expert: bool = True,
    industry: str = "",
) -> str:
    """检测团队陷阱。参数: founder_count(创始人数量), has_tech_cofounder, is_tech_project, has_domain_expert, industry"""
    return json.dumps(_do_team_check(founder_count, has_tech_cofounder, is_tech_project, has_domain_expert, industry), ensure_ascii=False, indent=2)


@tool
def detect_compliance_pitfalls(project_description: str, industry: str = "") -> str:
    """检测合规陷阱。参数: project_description(项目描述), industry(行业)"""
    return json.dumps(_do_compliance_check(project_description, industry), ensure_ascii=False, indent=2)


@tool
def run_full_pitfall_scan(
    price: Optional[float] = None,
    variable_cost: Optional[float] = None,
    current_cash: Optional[float] = None,
    monthly_expense: Optional[float] = None,
    monthly_revenue: float = 0,
    team_size: int = 1,
    founder_count: int = 1,
    has_tech_cofounder: bool = True,
    is_tech_project: bool = False,
    has_domain_expert: bool = True,
    project_description: str = "",
    industry: str = "",
    tam_description: str = "",
    customer_concentration: Optional[float] = None,
) -> str:
    """
    一键全维度陷阱扫描。传入所有已知参数，自动检测所有维度的陷阱。

    这会让 Agent 在分析任何项目时都能自动检测所有维度的陷阱，
    无需逐个调用上述单个检测工具。
    """
    all_pitfalls = []
    all_suggestions = []
    total_count = 0
    max_severity = "low"

    severity_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    def _update(result_dict: dict, category: str):
        nonlocal total_count, max_severity
        if "pitfalls" in result_dict and result_dict["pitfalls"]:
            for p in result_dict["pitfalls"]:
                p["category"] = category
                all_pitfalls.append(p)
                total_count += 1
        if "suggestions" in result_dict and result_dict["suggestions"]:
            all_suggestions.extend(result_dict["suggestions"])
        if result_dict.get("severity", "low") in severity_order:
            if severity_order[result_dict["severity"]] > severity_order[max_severity]:
                max_severity = result_dict["severity"]

    # 调用普通函数（而非 @tool 函数），避免 'StructuredTool' object is not callable
    if price is not None and variable_cost is not None:
        _update(_do_pricing_check(price=price, variable_cost=variable_cost), t("pt.cat.pricing"))

    if current_cash is not None and monthly_expense is not None:
        _update(_do_cashflow_check(current_cash=current_cash, monthly_expense=monthly_expense, monthly_revenue=monthly_revenue), t("pt.cat.cashflow"))

    if tam_description:
        _update(_do_market_check(tam_description=tam_description, team_size=team_size, customer_concentration=customer_concentration, description=project_description), t("pt.cat.market"))

    _update(_do_team_check(founder_count=founder_count, has_tech_cofounder=has_tech_cofounder, is_tech_project=is_tech_project, has_domain_expert=has_domain_expert, industry=industry), t("pt.cat.team"))

    if project_description or industry:
        _update(_do_compliance_check(project_description, industry), t("pt.cat.compliance"))

    result = {
        "total_pitfalls": total_count,
        "overall_severity": max_severity,
        "pitfalls_by_category": {},
    }

    for p in all_pitfalls:
        cat = p.pop("category", t("pt.cat.other"))
        if cat not in result["pitfalls_by_category"]:
            result["pitfalls_by_category"][cat] = []
        result["pitfalls_by_category"][cat].append(p)

    if all_suggestions:
        result["action_items"] = all_suggestions[:5]

    if total_count == 0:
        result["message"] = t("pt.msg.none_watch")

    return json.dumps(result, ensure_ascii=False, indent=2)


# ─── 纯函数版本（供 Workflow Engine 调用，返回 dict）─────────────────────

def _do_full_scan(
    price: Optional[float] = None,
    variable_cost: Optional[float] = None,
    current_cash: Optional[float] = None,
    monthly_expense: Optional[float] = None,
    monthly_revenue: float = 0,
    team_size: int = 1,
    founder_count: int = 1,
    has_tech_cofounder: bool = True,
    is_tech_project: bool = False,
    has_domain_expert: bool = True,
    project_description: str = "",
    industry: str = "",
    tam_description: str = "",
    customer_concentration: Optional[float] = None,
) -> dict:
    """纯函数版本的全维度陷阱扫描，返回 dict（而非 JSON 字符串）"""
    all_pitfalls = []
    all_suggestions = []
    total_count = 0
    max_severity = "low"
    severity_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}

    def _update(result_dict: dict, category: str):
        nonlocal total_count, max_severity
        if "pitfalls" in result_dict and result_dict["pitfalls"]:
            for p in result_dict["pitfalls"]:
                p["category"] = category
                all_pitfalls.append(p)
                total_count += 1
        if "suggestions" in result_dict and result_dict["suggestions"]:
            all_suggestions.extend(result_dict["suggestions"])
        if result_dict.get("severity", "low") in severity_order:
            if severity_order[result_dict["severity"]] > severity_order[max_severity]:
                max_severity = result_dict["severity"]

    if price is not None and variable_cost is not None:
        _update(_do_pricing_check(price=price, variable_cost=variable_cost), t("pt.cat.pricing"))

    if current_cash is not None and monthly_expense is not None:
        _update(_do_cashflow_check(current_cash=current_cash, monthly_expense=monthly_expense, monthly_revenue=monthly_revenue), t("pt.cat.cashflow"))

    if tam_description:
        _update(_do_market_check(tam_description=tam_description, team_size=team_size, customer_concentration=customer_concentration, description=project_description), t("pt.cat.market"))

    _update(_do_team_check(founder_count=founder_count, has_tech_cofounder=has_tech_cofounder, is_tech_project=is_tech_project, has_domain_expert=has_domain_expert, industry=industry), t("pt.cat.team"))

    if project_description or industry:
        _update(_do_compliance_check(project_description, industry), t("pt.cat.compliance"))

    result = {
        "total_pitfalls": total_count,
        "overall_severity": max_severity,
        "pitfalls_by_category": {},
    }

    for p in all_pitfalls:
        cat = p.pop("category", t("pt.cat.other"))
        if cat not in result["pitfalls_by_category"]:
            result["pitfalls_by_category"][cat] = []
        result["pitfalls_by_category"][cat].append(p)

    if all_suggestions:
        result["action_items"] = all_suggestions[:5]

    if total_count == 0:
        result["message"] = t("pt.msg.none")

    return result
