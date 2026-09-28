"""
财务计算工具集 — 创业者军师核心计算引擎

提供 NPV、IRR、ROI、盈亏平衡、单位经济学、跑道等关键财务指标的计算。
所有计算函数为纯函数，不依赖外部状态。
"""

import json
from typing import Optional
from langchain.tools import tool

from i18n import t


# ─── 公共计算函数（非 tool，可被多个 tool 复用）─────────────────────────────


def _discount(rate: float, period: int) -> float:
    """折现因子"""
    return 1.0 / ((1.0 + rate) ** period)


def _npv_raw(rate: float, cashflows: list[float]) -> float:
    """纯 NPV 计算，rate 为小数（如 0.1 表示 10%）"""
    return sum(cf * _discount(rate, t) for t, cf in enumerate(cashflows))


def _irr_raw(cashflows: list[float], guess: float = 0.1, max_iter: int = 1000, tol: float = 1e-7) -> Optional[float]:
    """Newton-Raphson 求解 IRR"""
    rate = guess
    for _ in range(max_iter):
        npv_val = _npv_raw(rate, cashflows)
        if abs(npv_val) < tol:
            return rate
        # 导数
        d_npv = sum(-t * cf * _discount(rate, t + 1) for t, cf in enumerate(cashflows))
        if abs(d_npv) < 1e-12:
            return None
        rate = rate - npv_val / d_npv
        if rate <= -0.99:
            return None
    return rate if abs(_npv_raw(rate, cashflows)) < tol * 10 else None


# ─── Tool 定义 ─────────────────────────────────────────────────────────────


@tool
def calculate_npv(
    rate_percent: float,
    cashflows_json: str,
    initial_investment: Optional[float] = None,
) -> str:
    """
    计算净现值 (NPV)。

    参数:
        rate_percent: 折现率，百分比形式，如 10 表示 10%
        cashflows_json: 各期现金流 JSON 数组字符串，如 "[100000, 150000, 200000]"
        initial_investment: 初始投资额（可选），如提供则从第一期现金流中扣除

    返回: JSON 字符串，包含 npv、各期折现值、解释
    """
    try:
        cashflows: list[float] = json.loads(cashflows_json)
        if not cashflows:
            return json.dumps({"error": t("fc.common.empty_cashflows")}, ensure_ascii=False)

        rate = rate_percent / 100.0

        # 如果指定了初始投资，放入第 0 期
        flows = cashflows[:]
        if initial_investment is not None and initial_investment > 0:
            flows = [-initial_investment] + flows

        npv_val = round(_npv_raw(rate, flows), 2)
        pv_details = [round(cf * _discount(rate, t), 2) for t, cf in enumerate(flows)]

        interpretation = t("fc.npv.worth") if npv_val > 0 else (t("fc.npv.no_value") if npv_val < 0 else t("fc.npv.breakeven"))

        return json.dumps({
            "npv": npv_val,
            "discount_rate": f"{rate_percent}%",
            "periods": len(flows),
            "present_values": pv_details,
            "cashflows": flows,
            "interpretation": interpretation,
            "note": t("fc.npv.note")
        }, ensure_ascii=False, indent=2)

    except json.JSONDecodeError:
        return json.dumps({"error": t("fc.common.bad_cashflows_json")}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)


# ─── 公共纯函数（供 Workflow Engine 调用，非 @tool）────────────────────────

def _calc_breakeven(
    fixed_costs: float,
    price_per_unit: float,
    variable_cost_per_unit: float,
) -> dict:
    """返回纯 dict 的盈亏平衡计算"""
    if price_per_unit <= variable_cost_per_unit:
        return {"error": t("fc.be.must_gt")}
    cm = price_per_unit - variable_cost_per_unit
    bu = fixed_costs / cm
    br = bu * price_per_unit
    return {
        "breakeven_units": round(bu, 0),
        "breakeven_revenue": round(br, 2),
        "contribution_margin_per_unit": round(cm, 2),
        "contribution_margin_ratio": f"{round((cm / price_per_unit) * 100, 1)}%",
    }


def _calc_runway(
    current_cash: float,
    monthly_burn_rate: float,
    monthly_revenue: float = 0,
) -> dict:
    """返回纯 dict 的跑道计算"""
    if current_cash is None:
        return {"runway_months": None, "net_monthly_burn": None,
                "note": t("fc.runway.cash_missing")}
    net_burn = monthly_burn_rate - monthly_revenue
    if net_burn <= 0:
        return {"runway_months": "无限", "net_monthly_burn": net_burn, "congratulations": True}
    r = round(current_cash / net_burn, 1)
    return {"runway_months": r, "net_monthly_burn": round(net_burn, 2)}


def _calc_unit_economics(
    cac: float, ltv: float, gross_margin_percent: float
) -> dict:
    """返回纯 dict 的单位经济学"""
    if cac <= 0:
        return {"error": t("fc.ue.cac_gt0")}
    ratio = round(ltv / cac, 2)
    adj_ratio = round((ltv * gross_margin_percent / 100) / cac, 2)
    return {
        "ltv_cac_ratio": ratio,
        "adjusted_ratio": adj_ratio,
        "ltv": ltv,
        "cac": cac,
        "health": t("fc.ue.healthy") if ratio >= 3 else (t("fc.ue.acceptable") if ratio >= 1 else t("fc.ue.danger")),
    }


def _calc_revenue_projection(
    base_revenue: float,
    monthly_growth_rate_percent: float,
    months: int = 12,
    churn_rate_percent: float = 0,
) -> dict:
    """返回纯 dict 的收入预测"""
    growth = monthly_growth_rate_percent / 100.0
    churn = churn_rate_percent / 100.0
    cur = base_revenue
    total = 0.0
    for _ in range(months):
        cur = cur * (1 + growth - churn)
        total += cur
    return {
        "final_month_revenue": round(cur, 2),
        "total_revenue": round(total, 2),
        "months": months,
    }


def _calc_sensitivity(
    base_revenue: float,
    fixed_cost: float,
    variable_cost: float,
    revenue_range_percent: float = 20,
    cost_range_percent: float = 20,
    steps: int = 3,
) -> dict:
    """返回纯 dict 的敏感性分析。

    口径（显式声明，经 CALCULATION_PHILOSOPHY 定夺）：
    - 营收变动 → 变动成本**同比例联动**（量变则食材/包装/佣金等比例增减）；
    - 固定成本独立波动（租金/薪资等冲击与客流量无关）；
    - 因此总成本 = fixed_cost×(1+固定成本波动) + variable_cost×(1+营收波动)。

    这是管理会计标准做法：边际贡献（营收-变动成本）随量同比例变动，
    固定成本是经营杠杆不随量变化。若把总成本与营收做独立变量，
    会导致 worst case 双重打击（营收-20% 同时成本+20%），
    把本来盈利的项目算成亏损，误导风险判断。
    """
    base_cost = fixed_cost + variable_cost
    base_profit = base_revenue - base_cost
    scenarios = []
    min_p, max_p = float("inf"), float("-inf")

    for i in range(steps):
        rp = -revenue_range_percent + (2 * revenue_range_percent * i / (steps - 1)) if steps > 1 else 0
        for j in range(steps):
            cp = -cost_range_percent + (2 * cost_range_percent * j / (steps - 1)) if steps > 1 else 0
            rev = base_revenue * (1 + rp / 100)
            # F3 修复：变动成本随营收联动，固定成本独立波动
            var_c = variable_cost * (1 + rp / 100)
            fix_c = fixed_cost * (1 + cp / 100)
            cost = fix_c + var_c
            p = round(rev - cost, 2)
            # 命名三个关键场景
            if rp == -revenue_range_percent and cp == cost_range_percent:
                label = t("fc.sens.pessimistic")
            elif rp == 0 and cp == 0:
                label = t("fc.sens.neutral")
            elif rp == revenue_range_percent and cp == -cost_range_percent:
                label = t("fc.sens.optimistic")
            else:
                label = None
            scenario = {
                "revenue_change": f"{round(rp,1)}%",
                "cost_change": f"{round(cp,1)}%",
                "profit": p,
            }
            if label:
                scenario["scenario"] = label
            scenarios.append(scenario)
            if p < min_p: min_p = p
            if p > max_p: max_p = p

    return {
        "base_profit": base_profit,
        "scenarios": scenarios,
        "worst_profit": min_p,
        "best_profit": max_p,
        "profit_range": round(max_p - min_p, 2),
        "note": t("fc.sens.note"),
    }


def _calc_cashflow_schedule(
    opening_cash: Optional[float],
    monthly_revenue: Optional[float],
    monthly_revenue_lag: Optional[float] = None,
    monthly_expenses: Optional[dict] = None,
    one_time_expenses: Optional[list] = None,
    payment_rhythm: Optional[dict] = None,
    months: int = 12,
) -> dict:
    """纯 dict 的月现金流明细表（档 B，无默认值）。

    现金流与 P&L 解耦：只反映「实际到账 / 实际支出」，不混入利润里的账期/预付。

    参数：
    - opening_cash: 期初现金（None → insufficient，不猜）
    - monthly_revenue: 月营收（None → 当月 inflow 不可算）
    - monthly_revenue_lag: 收入到账延迟（月；None → 缺，视为当月到账、但标注缺）
    - monthly_expenses: {"fixed": float, "variable": float}（缺失分量按 0，但不虚构）
    - one_time_expenses: [{"month": int(1-indexed), "amount": float, "label": str}]
    - payment_rhythm: {"rent": "quarterly"|"monthly", "deposit": {"month","amount"} ...}
      仅识别 rent: quarterly/monthly；其余忽略（不做 ERP）

    返回：
    - schedule: [{month, inflow, outflow, net, opening, closing, cumulative_shortfall}...]
    - zero_cash_month: int|None（None=未耗尽；无法算时也为 None）
    - max_shortfall: float|None（累计最大资金缺口；None=不可算）
    - insufficient: bool（opening_cash / 关键输入缺失）
    - gaps: [缺少的输入说明]
    """
    gaps = []
    if opening_cash is None:
        gaps.append(t("fc.cashflow.opening_cash"))
    if monthly_revenue is None:
        gaps.append(t("fc.cashflow.revenue"))
    if gaps:
        return {
            "schedule": [], "zero_cash_month": None, "max_shortfall": None,
            "insufficient": True, "gaps": gaps,
        }
    if opening_cash < 0:
        opening_cash = 0.0

    expenses = monthly_expenses or {}
    fixed = expenses.get("fixed") or 0.0
    variable = expenses.get("variable") or 0.0
    one_time = {}
    for e in (one_time_expenses or []):
        if isinstance(e, dict) and e.get("month") is not None and e.get("amount") is not None:
            one_time[int(e["month"])] = one_time.get(int(e["month"]), 0.0) + float(e["amount"])
    rhythm = payment_rhythm or {}
    rent_rhythm = rhythm.get("rent", "monthly")

    schedule = []
    cash = opening_cash
    cum_shortfall = 0.0
    max_shortfall = 0.0
    zero_cash_month = None
    # 到账延迟：应收池（本月收入 N 个月后才入账）
    arrears_pool = 0.0
    if monthly_revenue_lag:
        lag = max(0, int(monthly_revenue_lag))
    else:
        lag = 0

    for m in range(1, months + 1):
        opening = round(cash, 2)
        # 收入：本期应收 + 归集前序池（简化：到账延迟=收入顺延入账）
        inflow = 0.0
        if lag == 0:
            inflow = monthly_revenue
        else:
            arrears_pool += monthly_revenue
            if m > lag:
                inflow = arrears_pool
                arrears_pool = 0.0

        # 支出：固定（按支付节奏：季度/月）+ 变动 + 一次性
        outflow = 0.0
        if rent_rhythm == "quarterly" and m % 3 == 0:
            outflow += fixed * 3
        else:
            outflow += fixed
        outflow += variable
        outflow += one_time.get(m, 0.0)

        net = inflow - outflow
        cash += net
        closing = round(cash, 2)
        if closing < 0 and zero_cash_month is None:
            zero_cash_month = m
            cum_shortfall = -closing
            max_shortfall = max(max_shortfall, cum_shortfall)
        schedule.append({
            "month": m,
            "inflow": round(inflow, 2),
            "outflow": round(outflow, 2),
            "net": round(net, 2),
            "opening": opening,
            "closing": closing,
            "cumulative_shortfall": round(max_shortfall, 2),
        })

    return {
        "schedule": schedule,
        "zero_cash_month": zero_cash_month,
        "max_shortfall": round(max_shortfall, 2) if max_shortfall else None,
        "insufficient": False,
        "gaps": [],
    }


@tool
def calculate_irr(cashflows_json: str, initial_investment: Optional[float] = None) -> str:
    """
    计算内部收益率 (IRR)。

    参数:
        cashflows_json: 各期现金流 JSON 数组字符串，第一期通常为负（初始投资）
        initial_investment: 初始投资额（可选），如提供则自动放入第一期

    返回: JSON 字符串，包含 irr、解释
    """
    try:
        cashflows: list[float] = json.loads(cashflows_json)
        if not cashflows:
            return json.dumps({"error": t("fc.common.empty_cashflows")}, ensure_ascii=False)

        flows = cashflows[:]
        if initial_investment is not None and initial_investment > 0:
            flows = [-initial_investment] + flows

        irr_val = _irr_raw(flows)
        if irr_val is None:
            return json.dumps({"error": t("fc.irr.unconverged")}, ensure_ascii=False)

        irr_percent = round(irr_val * 100, 2)

        interpretation = (
            t("fc.irr.excellent") if irr_percent > 25 else
            t("fc.irr.good") if irr_percent > 15 else
            t("fc.irr.fair") if irr_percent > 8 else
            t("fc.irr.low")
        )

        return json.dumps({
            "irr": f"{irr_percent}%",
            "irr_decimal": round(irr_val, 6),
            "periods": len(flows),
            "cashflows": flows,
            "interpretation": interpretation,
            "note": t("fc.irr.note")
        }, ensure_ascii=False, indent=2)

    except json.JSONDecodeError:
        return json.dumps({"error": t("fc.common.bad_cashflows_json")}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)


@tool
def calculate_roi(total_return: float, total_investment: float) -> str:
    """
    计算投资回报率 (ROI)。

    参数:
        total_return: 总回报金额
        total_investment: 总投资金额

    返回: JSON 字符串，包含 roi、解释
    """
    try:
        if total_investment <= 0:
            return json.dumps({"error": t("fc.roi.invest_gt0")}, ensure_ascii=False)

        roi_val = ((total_return - total_investment) / total_investment) * 100
        roi_val = round(roi_val, 2)

        interpretation = (
            t("fc.roi.excellent") if roi_val > 100 else
            t("fc.roi.good") if roi_val > 50 else
            t("fc.roi.fair") if roi_val > 20 else
            t("fc.roi.poor") if roi_val > 0 else
            t("fc.roi.loss")
        )

        return json.dumps({
            "roi": f"{roi_val}%",
            "total_return": total_return,
            "total_investment": total_investment,
            "net_profit": round(total_return - total_investment, 2),
            "interpretation": interpretation
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)


@tool
def calculate_breakeven(
    fixed_costs: float,
    price_per_unit: float,
    variable_cost_per_unit: float,
) -> str:
    """
    计算盈亏平衡点。

    参数:
        fixed_costs: 固定成本总额（月/年）
        price_per_unit: 单位售价
        variable_cost_per_unit: 单位变动成本

    返回: JSON 字符串，包含盈亏平衡点数量、金额、安全边际分析
    """
    try:
        if price_per_unit <= variable_cost_per_unit:
            return json.dumps({
                "error": t("fc.be.must_gt_full"),
                "suggestion": t("fc.be.suggestion", price=price_per_unit, vc=variable_cost_per_unit)
            }, ensure_ascii=False)

        contribution_margin = price_per_unit - variable_cost_per_unit
        breakeven_units = fixed_costs / contribution_margin
        breakeven_revenue = breakeven_units * price_per_unit
        contribution_margin_ratio = (contribution_margin / price_per_unit) * 100

        return json.dumps({
            "breakeven_units": round(breakeven_units, 0),
            "breakeven_revenue": round(breakeven_revenue, 2),
            "contribution_margin_per_unit": round(contribution_margin, 2),
            "contribution_margin_ratio": f"{round(contribution_margin_ratio, 1)}%",
            "formula": t("fc.be.formula", fc=fixed_costs, price=price_per_unit, vc=variable_cost_per_unit),
            "interpretation": t("fc.be.interpretation", units=round(breakeven_units, 0), revenue=round(breakeven_revenue, 2))
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)


@tool
def calculate_unit_economics(
    customer_acquisition_cost: float,
    customer_lifetime_value: float,
    gross_margin_percent: float,
) -> str:
    """
    计算单位经济学指标 (LTV/CAC)。

    参数:
        customer_acquisition_cost: 获客成本 (CAC)
        customer_lifetime_value: 客户生命周期价值 (LTV)
        gross_margin_percent: 毛利率百分比，如 60 表示 60%

    返回: JSON 字符串，包含 LTV/CAC 比率、解释与预警
    """
    try:
        if customer_acquisition_cost <= 0:
            return json.dumps({"error": t("fc.ue.acquisition_gt0")}, ensure_ascii=False)

        ltv_cac_ratio = round(customer_lifetime_value / customer_acquisition_cost, 2)
        gross_margin_adjusted_ltv = customer_lifetime_value * (gross_margin_percent / 100)
        adjusted_ratio = round(gross_margin_adjusted_ltv / customer_acquisition_cost, 2)

        # 判断
        if ltv_cac_ratio >= 3:
            health = t("fc.ue.healthy")
            detail = t("fc.ue.detail_healthy")
        elif ltv_cac_ratio >= 1:
            health = t("fc.ue.acceptable")
            detail = t("fc.ue.detail_acceptable")
        else:
            health = t("fc.ue.danger")
            detail = t("fc.ue.detail_danger")

        # 陷阱预警
        warnings = []
        if customer_acquisition_cost > customer_lifetime_value * 0.5:
            warnings.append(t("fc.ue.warn_cac_ratio", cac=customer_acquisition_cost, ltv=customer_lifetime_value))
        if gross_margin_percent < 40:
            warnings.append(t("fc.ue.warn_gm", gm=gross_margin_percent))

        result = {
            "ltv_cac_ratio": ltv_cac_ratio,
            "gross_margin_adjusted_ltv_cac": adjusted_ratio,
            "ltv": customer_lifetime_value,
            "cac": customer_acquisition_cost,
            "gross_margin": f"{gross_margin_percent}%",
            "health": health,
            "detail": detail,
            "benchmark": t("fc.ue.benchmark"),
        }

        if warnings:
            result["warnings"] = warnings

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)


@tool
def calculate_runway(
    current_cash: float,
    monthly_burn_rate: float,
    monthly_revenue: float = 0,
) -> str:
    """
    计算现金流跑道 (Runway)——公司还能撑多少个月。

    参数:
        current_cash: 当前现金余额
        monthly_burn_rate: 月支出（不含收入）
        monthly_revenue: 月收入（可选，默认 0）

    返回: JSON 字符串，包含跑道月数、建议
    """
    try:
        if current_cash is None:
            return json.dumps({"runway_months": None, "note": t("fc.runway.cash_missing")},
                               ensure_ascii=False, indent=2)
        net_burn = monthly_burn_rate - monthly_revenue
        if net_burn <= 0:
            return json.dumps({
                "runway_months": "无限",
                "net_monthly_burn": net_burn,
                "interpretation": t("fc.runway.positive"),
                "congratulations": True
            }, ensure_ascii=False, indent=2)

        runway = round(current_cash / net_burn, 1)

        if runway > 18:
            advice = t("fc.runway.advice_plenty")
            urgency = "low"
        elif runway > 12:
            advice = t("fc.runway.advice_ok")
            urgency = "medium"
        elif runway > 6:
            advice = t("fc.runway.advice_tight")
            urgency = "high"
        else:
            advice = t("fc.runway.advice_critical")
            urgency = "critical"

        return json.dumps({
            "runway_months": runway,
            "current_cash": current_cash,
            "monthly_burn_rate": monthly_burn_rate,
            "monthly_revenue": monthly_revenue,
            "net_monthly_burn": round(net_burn, 2),
            "advice": advice,
            "urgency": urgency,
            "note": t("fc.runway.note", burn=round(net_burn, 2), months=runway)
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)


@tool
def build_revenue_projection(
    base_revenue: float,
    monthly_growth_rate_percent: float,
    months: int = 12,
    churn_rate_percent: float = 0,
) -> str:
    """
    构建收入预测模型。

    参数:
        base_revenue: 基准月收入
        monthly_growth_rate_percent: 月增长率百分比，如 5 表示 5%
        months: 预测月数，默认 12
        churn_rate_percent: 月流失率百分比（订阅制业务需关注），默认 0

    返回: JSON 字符串，包含逐月预测、累计收入
    """
    try:
        if months <= 0 or months > 60:
            return json.dumps({"error": t("fc.proj.months_range")}, ensure_ascii=False)

        growth_rate = monthly_growth_rate_percent / 100.0
        churn_rate = churn_rate_percent / 100.0

        projections = []
        cumulative = 0.0
        current = base_revenue

        for m in range(1, months + 1):
            current = current * (1 + growth_rate - churn_rate)
            cumulative += current
            projections.append({
                "month": m,
                "revenue": round(current, 2),
                "cumulative": round(cumulative, 2)
            })

        total = round(cumulative, 2)
        final_month = round(projections[-1]["revenue"], 2) if projections else 0
        growth_multiple = round(final_month / base_revenue, 2) if base_revenue > 0 else 0

        return json.dumps({
            "base_monthly_revenue": base_revenue,
            "monthly_growth_rate": f"{monthly_growth_rate_percent}%",
            "churn_rate": f"{churn_rate_percent}%",
            "projection_months": months,
            "final_month_revenue": final_month,
            "total_revenue": total,
            "growth_multiple": f"{growth_multiple}x",
            "monthly_detail": projections,
            "note": t("fc.proj.note")
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)


@tool
def build_cost_structure(
    fixed_costs_json: str,
    variable_costs_json: str,
    projected_revenue: float,
) -> str:
    """
    构建成本结构分析。

    参数:
        fixed_costs_json: 固定成本明细 JSON，如 '[{"name":"房租","amount":15000},{"name":"工资","amount":50000}]'
        variable_costs_json: 变动成本明细 JSON，同上格式
        projected_revenue: 预期收入

    返回: JSON 字符串，包含成本结构、毛利率、盈亏分析
    """
    try:
        fixed: list[dict] = json.loads(fixed_costs_json)
        variable: list[dict] = json.loads(variable_costs_json)

        total_fixed = sum(item["amount"] for item in fixed)
        total_variable = sum(item["amount"] for item in variable)
        total_cost = total_fixed + total_variable
        gross_profit = projected_revenue - total_variable
        net_profit = projected_revenue - total_cost
        gross_margin = round((gross_profit / projected_revenue) * 100, 1) if projected_revenue > 0 else 0
        net_margin = round((net_profit / projected_revenue) * 100, 1) if projected_revenue > 0 else 0

        # 风险检测
        warnings = []
        if total_fixed > projected_revenue * 0.7:
            warnings.append(t("fc.cs.warn_fixed_ratio", ratio=round(total_fixed/total_cost*100, 1)))
        if net_margin < 10:
            warnings.append(t("fc.cs.warn_net_margin", nm=net_margin))
        if gross_margin < 30:
            warnings.append(t("fc.cs.warn_gross_margin", gm=gross_margin))

        result = {
            "total_fixed_cost": total_fixed,
            "total_variable_cost": total_variable,
            "total_cost": total_cost,
            "projected_revenue": projected_revenue,
            "gross_profit": gross_profit,
            "gross_margin": f"{gross_margin}%",
            "net_profit": net_profit,
            "net_margin": f"{net_margin}%",
            "fixed_cost_ratio": f"{round(total_fixed/total_cost*100, 1)}%",
            "fixed_cost_detail": fixed,
            "variable_cost_detail": variable,
        }

        if warnings:
            result["warnings"] = warnings

        return json.dumps(result, ensure_ascii=False, indent=2)

    except json.JSONDecodeError:
        return json.dumps({"error": t("fc.cs.bad_json")}, ensure_ascii=False)
    except KeyError as e:
        return json.dumps({"error": t("fc.cs.missing_field", e=e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)


@tool
def sensitivity_analysis(
    base_revenue: float,
    fixed_cost: float,
    variable_cost: float,
    revenue_range_percent: float = 20,
    cost_range_percent: float = 20,
    steps: int = 3,
) -> str:
    """
    敏感性分析：收入与成本在不同变化幅度下的利润矩阵。

    口径：变动成本随营收同比例联动（边际贡献口径），固定成本独立波动。

    参数:
        base_revenue: 基准收入
        fixed_cost: 固定成本（租金/薪资等，不随量变化）
        variable_cost: 变动成本（食材/包装/佣金等，随量同比例变化）
        revenue_range_percent: 收入波动范围百分比，默认 20
        cost_range_percent: 固定成本波动范围百分比，默认 20
        steps: 步数，默认 3（悲观/中性/乐观）

    返回: JSON 字符串，包含利润矩阵、最坏/最好情况
    """
    try:
        base_cost = fixed_cost + variable_cost
        base_profit = base_revenue - base_cost
        matrix = []
        min_profit = float("inf")
        max_profit = float("-inf")
        worst_case = ""
        best_case = ""

        for i in range(steps):
            rev_pct = -revenue_range_percent + (2 * revenue_range_percent * i / (steps - 1)) if steps > 1 else 0
            row = []
            for j in range(steps):
                fix_pct = -cost_range_percent + (2 * cost_range_percent * j / (steps - 1)) if steps > 1 else 0
                rev = base_revenue * (1 + rev_pct / 100)
                # F3 修复：变动成本随营收联动，固定成本独立波动
                var_c = variable_cost * (1 + rev_pct / 100)
                fix_c = fixed_cost * (1 + fix_pct / 100)
                cost = fix_c + var_c
                profit = round(rev - cost, 2)
                row.append({
                    "revenue_change": f"{round(rev_pct, 1)}%",
                    "fixed_cost_change": f"{round(fix_pct, 1)}%",
                    "revenue": round(rev, 2),
                    "cost": round(cost, 2),
                    "profit": profit
                })
                if profit < min_profit:
                    min_profit = profit
                    worst_case = t("fc.sens.scenario", rev=round(rev_pct,1), fix=round(fix_pct,1))
                if profit > max_profit:
                    max_profit = profit
                    best_case = t("fc.sens.scenario", rev=round(rev_pct,1), fix=round(fix_pct,1))
            matrix.append(row)

        return json.dumps({
            "base_profit": base_profit,
            "base_revenue": base_revenue,
            "base_fixed_cost": fixed_cost,
            "base_variable_cost": variable_cost,
            "base_cost": base_cost,
            "sensitivity_matrix": matrix,
            "worst_case": {"profit": min_profit, "scenario": worst_case},
            "best_case": {"profit": max_profit, "scenario": best_case},
            "profit_range": round(max_profit - min_profit, 2),
            "note": t("fc.sens.note_full")
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("fc.common.calc_fail", e=str(e))}, ensure_ascii=False)
