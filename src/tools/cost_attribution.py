"""成本归因拆解模块 — 月度成本结构分量分析。

职责单一：给定已填充的 params（来自 _fill_and_assess），拆解成本结构，
输出各分量的金额与占比。不做因果归因，只做结构分量。

设计原则：
- 复用 quick_scan 的 _fill_and_assess 产出，不重算
- 变动成本率缺失 → 返回 insufficient（利润/成本不可算）
- 总成本为 0 → 返回 insufficient（除零防御）
- 各分量：租金、人工、变动成本、水电、包装、提成、其他固定
"""

from i18n import t


def _build_cost_attribution(params: dict) -> dict:
    """从已填充的 params 中拆解成本结构分量。

    Args:
        params: 已经过 _fill_and_assess 的参数字典（含 monthly_fixed_cost 等派生值）

    Returns:
        {
            "total_monthly_cost": float,       # 月度总成本
            "fixed_cost": float,               # 月固定成本
            "variable_cost": float,            # 月变动成本
            "components": [                    # 各分量明细
                {"name": "租金", "amount": 8000, "percent": 0.27, "source": "[用户]"},
                {"name": "人工", "amount": 12000, "percent": 0.40, "source": "[默认]"},
                ...
            ],
            "top_component": {"name": "人工", "amount": 12000, "percent": 0.40},
            "fixed_ratio": 0.67,               # 固定成本占比
            "variable_ratio": 0.33,            # 变动成本占比
        }
        或 {"insufficient": true, "message": "...", "gaps": [...]}
    """
    # ── 防御：核心数据缺失 ──
    monthly_revenue = params.get("monthly_revenue")
    fixed_cost = params.get("monthly_fixed_cost")
    vc_ratio = params.get("variable_cost_ratio")

    gaps = []
    if monthly_revenue is None:
        gaps.append(t("ca.gap.monthly_revenue"))
    if fixed_cost is None:
        gaps.append(t("ca.gap.fixed_cost"))
    if vc_ratio is None:
        gaps.append(t("ca.gap.variable_cost_ratio"))
    if gaps:
        return {
            "insufficient": True,
            "message": t("ca.need_full_data"),
            "gaps": gaps,
        }

    # ── 计算各分量 ──
    monthly_rent = params.get("monthly_rent") or 0
    monthly_labor = params.get("monthly_labor") or 0
    utilities = params.get("utilities") or 0
    packaging = params.get("packaging") or 0
    commission = params.get("commission") or 0
    other_fixed = params.get("other_fixed") or 0

    variable_cost = monthly_revenue * vc_ratio
    total_cost = fixed_cost + variable_cost

    # ── 除零防御 ──
    if total_cost <= 0:
        return {
            "insufficient": True,
            "message": t("ca.zero_total"),
            "gaps": [t("ca.zero_total_reason")],
        }

    # ── 构建分量列表（只含非零分量）──
    # 获取参数来源标注（如有）
    src = params.get("_param_sources") or {}

    _COMPONENT_MAP = [
        (t("ca.comp.rent"), monthly_rent, src.get("monthly_rent", "")),
        (t("ca.comp.labor"), monthly_labor, src.get("monthly_labor", "")),
        (t("ca.comp.variable"), variable_cost, src.get("variable_cost_ratio", "")),
        (t("ca.comp.utilities"), utilities, src.get("utilities", "")),
        (t("ca.comp.packaging"), packaging, src.get("packaging", "")),
        (t("ca.comp.commission"), commission, src.get("commission", "")),
        (t("ca.comp.other"), other_fixed, src.get("other_fixed", "")),
    ]

    components = []
    for name, amount, source in _COMPONENT_MAP:
        if amount > 0:
            components.append({
                "name": name,
                "amount": round(amount, 0),
                "percent": round(amount / total_cost, 4),
                "source": source,
            })

    # 按金额降序排列
    components.sort(key=lambda c: -c["amount"])

    # ── 顶级分量 ──
    top = components[0] if components else {"name": t("ca.comp.none"), "amount": 0, "percent": 0}

    # ── 固定/变动比例 ──
    fixed_ratio = round(fixed_cost / total_cost, 4) if total_cost > 0 else 0
    variable_ratio = round(variable_cost / total_cost, 4) if total_cost > 0 else 0

    # ── 风险提示 ──
    warnings = []
    if top["percent"] > 0.5:
        warnings.append(t("ca.warn.top_component", name=top["name"], pct=f"{top['percent']:.0%}"))
    if variable_ratio > 0.6:
        warnings.append(t("ca.warn.variable_ratio", pct=f"{variable_ratio:.0%}", delta=f"{variable_ratio:.1f}"))
    if monthly_labor > 0 and monthly_labor / total_cost > 0.4:
        warnings.append(t("ca.warn.labor_ratio", pct=f"{monthly_labor / total_cost:.0%}"))

    return {
        "total_monthly_cost": round(total_cost, 0),
        "fixed_cost": round(fixed_cost, 0),
        "variable_cost": round(variable_cost, 0),
        "components": components,
        "top_component": top,
        "fixed_ratio": fixed_ratio,
        "variable_ratio": variable_ratio,
        "warnings": warnings,
    }
