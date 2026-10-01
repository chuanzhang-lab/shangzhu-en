"""模板格式化器 — 工具结果→人类可读 Markdown

每个工具一种模板。LLM 拿到这个后能润色但不能改关键数字。

文案外置：所有展示文案统一走 `src/i18n` 的 `t()`，本文件不再硬编码中文
（护栏：tests/test_i18n_guard.py 断言本文件无残留中文字面量）。

⚠️ 已知耦合（M4 改造 workflow_engine 时必须一并处理，见 docs/PLAN_I18N_EN.md）：
- `cash_status` / `param_sources` 里带 `[缺失]` 标记与「变动成本」字样，是**引擎产出的
  数据**，本层用中文字符串去匹配它们。等引擎侧文案也外置后，这里必须改为状态码匹配，
  否则英文环境下判定会静默失效。
"""

import json
import re
from typing import Any, Dict, List, Optional

from i18n import has, industry_name, t

# 引擎侧写死的数据标记（非文案）——M4 引擎 i18n 后需改为状态码。
# 这三个是 formatter.py 里仅有的中文字面量，护栏测试按白名单放行。
_ENGINE_MISSING_MARK = "[缺失]"
_ENGINE_VC_HINT = "变动成本"
_ENGINE_INFINITE_MARK = "无限"


def _fmt_benchmark_lines(bench: Dict, industry_key: str = "") -> List[str]:
    """行业基准的**结构化渲染**。

    旧实现把 bench 整个 dict 原样倒出来（`- key_warning: 新店前 3 个月…`），
    两个问题：
    1. 值是中文内容 → 英文版每次扫描都漏中文；
    2. 连 `profit_margin_min` / `traffic_min` 这些**引擎内部字段**一起倒给用户，
       两种语言下都是噪音。

    这里只渲染四项对人有用的（客流区间 / 利润率 / 回本周期 / 核心风险），
    文案走 i18n，按行业取（基准是**每行业不同**的内容，不是通用 UI 文案）。
    """
    # 「自定义 / 其他」没有基准条目：走中性提示，绝不倒原始 dict。
    # 倒 dict 会把 industry_templates.yaml 的兜底值（"未知"/"无数据"…）漏给用户 ——
    # 英文版每次扫描都漏中文（M-07 护栏实测抓到）；且原始 dict 还含
    # profit_margin_min 这类引擎内部字段，两种语言下都是噪音。
    if not industry_key or not has(f"bench.traffic.{industry_key}"):
        return [f"- {t('bench.no_benchmark')}"]
    return [
        f"- {t('bench.label.traffic_range')}: {t(f'bench.traffic.{industry_key}')}",
        f"- {t('bench.label.profit_margin')}: {t(f'bench.margin.{industry_key}')}",
        f"- {t('bench.label.breakeven')}: {t(f'bench.breakeven.{industry_key}')}",
        f"- {t('bench.label.key_warning')}: {t(f'bench.warning.{industry_key}')}",
        f"- {t('bench.note')}",
    ]


def _traffic_unit(benchmark: Optional[Dict] = None, params: Optional[Dict] = None,
                  industry_key: str = "") -> str:
    """客流单位：用户口中的量词 > 行业 benchmark 默认 > 中性兜底。

    行业默认：从 benchmark 的 daily_traffic_range 取（「80-250 杯」→「杯/天」）。
    旧实现把「杯/天」写死在模板里 —— 面馆会看到「盈亏平衡客流 46 杯/天」。

    D8：行业模板只到「餐饮」粒度（默认「杯」），但用户说的是「每天卖 100 碗」。
    用户自己给的量词是最权威的口径，优先于行业默认；取不到才回退行业值。
    参数 `_traffic_unit` 由抽取器写入（`_` 前缀内部键，不参与业务计算）。
    """
    user_unit = (params or {}).get("_traffic_unit")
    if user_unit:
        return f"{user_unit}{t('fmt.traffic_unit.per_day')}"
    # 行业默认：优先从**本地化后**的基准串取单位。
    # 中文「80-250 杯」能正则出「杯」；英文 "80-250 servings/day" 里没有 CJK，
    # 正则自然不命中 → 落到下面 fmt.traffic_unit.fallback（"units/day"）。
    # 若仍读原始 dict，英文版就会漏出「63 杯/day」这种混血串。
    rng = ""
    if industry_key and has(f"bench.traffic.{industry_key}"):
        rng = t(f"bench.traffic.{industry_key}")
    else:
        rng = (benchmark or {}).get("daily_traffic_range") or ""
    m = re.search(r"\d+\s*[-~－—]\s*\d+\s*([\u4e00-\u9fa5]{1,2})\s*$", rng)
    if m:
        return f"{m.group(1)}{t('fmt.traffic_unit.per_day')}"
    return t("fmt.traffic_unit.fallback")


# ─── 工具: quick_scan ─────────────────────────────────────────────────────
def _fmt_insufficient(data: Dict) -> str:
    """参数不足骨架的渲染：展示缺口 + 模型框架 + 当前假设，不输出误报结论。"""
    lines = []
    lines.append(t("fmt.insufficient.title"))
    lines.append("")
    lines.append(data.get("message", t("fmt.insufficient.default_message")))
    lines.append("")

    gaps = data.get("gaps", [])
    if gaps:
        lines.append(t("fmt.insufficient.gaps_header"))
        for g in gaps:
            lines.append(f"- ❓ {g}")
        lines.append("")

    cov = data.get("coverage")
    if cov is not None:
        lines.append(t("fmt.insufficient.coverage", pct=int(cov * 100)))
        lines.append("")

    fw = data.get("framework", {})
    if fw:
        lines.append(t("fmt.insufficient.framework_header"))
        lines.append(t("fmt.insufficient.revenue", v=fw.get("revenue_model", "")))
        lines.append(t("fmt.insufficient.cost", v=fw.get("cost_model", "")))
        lines.append(t("fmt.insufficient.cash", v=fw.get("cash_model", "")))
        lines.append("")

    # 精确推算层（骨架也展示：能算的照算 + 缺什么）
    lines.extend(_fmt_derived(data.get("derived")))
    if data.get("derived"):
        lines.append("")

    assumptions = data.get("assumptions", [])
    if assumptions:
        lines.append(t("fmt.common.assumptions_header"))
        lines.append(t("fmt.common.assumptions_table_header"))
        lines.append("|------|--------|------|")
        for a in assumptions:
            v = a.get("value")
            if v is None:
                v_str = t("fmt.common.unknown_pending")
            elif isinstance(v, (int, float)) and abs(v) >= 1000:
                v_str = f"{v:,.0f}"
            else:
                v_str = str(v)
            lines.append(f"| {a['field']} | {v_str} | {a['source']} |")
        lines.append("")

    lines.append("---")
    lines.append(data.get("next_step", t("fmt.insufficient.next_step_default")))
    return "\n".join(lines)


def _fmt_derived(derived) -> list:
    """精确推算层渲染：由用户输入 + 确定公式推出的关联参数，逐项带公式标注。

    无「默认值」——每项要么已精确算出（ok），要么缺输入（missing，附缺什么）。
    返回 markdown 行列表。
    """
    derived = derived or []
    if not derived:
        return []
    ok_items = [d for d in derived if d.get("status") == "ok"]
    miss_items = [d for d in derived if d.get("status") == "missing"]
    out = [t("fmt.derived.title"), ""]
    if ok_items:
        out.append(t("fmt.derived.table_header"))
        out.append("|------|--------|------|")
        for d in ok_items:
            val = d["value"]
            vs = f"{val:,.0f} {d['unit']}" if isinstance(val, (int, float)) else str(val)
            out.append(f"| {d['label']} | {vs} | `{d['formula']}` |")
        out.append("")
    if miss_items:
        out.append(t("fmt.derived.cannot_derive"))
        out.append("")
        for d in miss_items:
            # missing 项的 formula 为空串 —— 不要渲染成一对空反引号「``」
            fx = f"（`{d['formula']}`）" if d.get("formula") else ""
            out.append(
                t(
                    "fmt.derived.missing_item",
                    label=d["label"],
                    missing=d.get("missing") or t("fmt.common.unknown"),
                    fx=fx,
                )
            )
        out.append("")
    return out


def _fmt_scan(data: Dict) -> str:
    if "error" in data:
        return t("fmt.scan.error", error=data["error"])

    # 参数不足骨架（决策B：门禁拦下时的呈现）
    if data.get("insufficient"):
        return _fmt_insufficient(data)

    core = data.get("core_metrics", {})
    status = data.get("status", {})
    params_src = data.get("param_sources", {})
    sensitivity = data.get("sensitivity", {}).get("scenarios", [])
    pitfalls = data.get("pitfalls", {}).get("pitfalls", [])
    bench = data.get("benchmark", {})
    # 基准是**按行业取**的，需要行业数据键（project_type 就是它，未经展示名映射）
    industry_key = data.get("project_type") or ""

    lines = []

    # 标题 + 项目类型
    # 括号内只呈现**已知**信息：stage/template_mode 缺失时不得渲染成字面量「None」
    # project_type 是**行业数据键**，展示前映射成展示名（键本身永不变）
    project_type = (industry_name(data.get("project_type"))
                    or t("fmt.common.project_type_default"))
    _quals = [s for s in (data.get("stage"), data.get("template_mode")) if s]
    # 连括号都是文案：中文用全角「（）」，英文用半角 " ()"，写死就混血
    _suffix = (t("fmt.common.qual_suffix", s=" · ".join(str(q) for q in _quals))
               if _quals else "")
    lines.append(t("fmt.scan.title", project_type=project_type, suffix=_suffix))
    lines.append("")

    # 数据冲突（派生一致性）：规则层先发现，前置高亮，不依赖 LLM
    derived_issues = data.get("derived_issues") or []
    if derived_issues:
        lines.append(t("fmt.scan.conflict_header"))
        for i in derived_issues:
            lines.append(f"> {i.get('message', '')}")
        lines.append("")

    # 核心指标
    lines.append(t("fmt.scan.core_header"))
    lines.append(t("fmt.scan.core_table_header"))
    lines.append("|------|------|------|")

    # D2：变动成本率缺失 → 利润/毛利率「还不能定」，先给出缺什么再展示其余
    vc_gap = core.get("monthly_profit") is None
    if vc_gap:
        lines.append(t("fmt.scan.vc_gap_note1"))
        lines.append(t("fmt.scan.vc_gap_note2"))
        lines.append("")

    monthly_profit = core.get("monthly_profit", 0)
    if monthly_profit is None:
        profit_status = t("fmt.scan.profit_unknown")
    else:
        profit_status = t("fmt.scan.profit_up") if monthly_profit > 0 else t("fmt.scan.profit_down")
    profit_value = "—" if monthly_profit is None else t("fmt.common.money", v=f"{monthly_profit:,.0f}")
    lines.append(t("fmt.scan.profit_row", value=profit_value, status=profit_status))

    monthly_revenue = core.get("monthly_revenue")
    rev_str = t("fmt.common.money", v=f"{monthly_revenue:,.0f}") if isinstance(monthly_revenue, (int, float)) else "—"
    lines.append(t("fmt.scan.revenue_row", value=rev_str))

    daily_breakeven = core.get("daily_breakeven")
    if daily_breakeven:
        lines.append(
            t(
                "fmt.scan.breakeven_row",
                value=f"{daily_breakeven:.0f}",
                unit=_traffic_unit(data.get("benchmark"), data.get("params"),
                                   industry_key),
            )
        )

    gross_margin = core.get("gross_margin_percent")
    if gross_margin:
        lines.append(t("fmt.scan.margin_row", value=gross_margin))

    runway = core.get("runway_months")
    cash_status = status.get("cash", "")
    if _ENGINE_VC_HINT in str(cash_status):
        lines.append(t("fmt.scan.runway_unknown_vc", status=cash_status))
    elif runway is not None and runway != _ENGINE_INFINITE_MARK:
        lines.append(t("fmt.scan.runway_months", value=runway, status=cash_status or "—"))
    elif runway is None:
        lines.append(t("fmt.scan.runway_unknown_invest", status=cash_status or "—"))

    lines.append("")

    # 精确推算层：把「由你输入用公式推出」的关联参数逐项列出（带公式，无猜测）
    lines.extend(_fmt_derived(data.get("derived")))

    lines.append("")

    # 风险聚焦（⑤ 叙事>判决：把杠杆点交还用户，而非只给一个 🔴危险）
    narrative = data.get("narrative")
    if narrative:
        lines.append(t("fmt.scan.narrative_header"))
        lines.append(f"> {narrative}")
        lines.append("")

    # 参数来源
    params_data = data.get("params", {})
    if params_src:
        lines.append(t("fmt.scan.params_header"))
        lines.append(t("fmt.scan.params_table_header"))
        lines.append("|------|-----|------|")
        for k, src in params_src.items():
            if src and not k.startswith("_"):
                val = params_data.get(k, "")
                if val == "" or val is None:
                    if src.startswith(_ENGINE_MISSING_MARK):
                        val_str = t("fmt.common.unknown_pending")  # [缺失] 字段明确标出，而非静默跳过
                    else:
                        continue
                elif isinstance(val, (int, float)):
                    val_str = f"{val:,.0f}" if val >= 1000 else str(val)
                else:
                    val_str = str(val)
                lines.append(f"| {k} | {val_str} | {src} |")
        lines.append("")

    # 当前假设清单（决策A③：把默认/缺失假设前置可见）
    assumptions = data.get("assumptions")
    if assumptions:
        lines.append(t("fmt.common.assumptions_header"))
        lines.append(t("fmt.common.assumptions_table_header"))
        lines.append("|------|--------|------|")
        for a in assumptions:
            v = a.get("value")
            if v is None:
                v_str = t("fmt.common.unknown_pending")
            elif isinstance(v, (int, float)) and abs(v) >= 1000:
                v_str = f"{v:,.0f}"
            else:
                v_str = str(v)
            lines.append(f"| {a['field']} | {v_str} | {a['source']} |")
        lines.append("")

    # 情景分析（③⑥ 输出范围而非单点；把"未知"变成结论的弹性）
    scenarios = data.get("scenarios")
    if scenarios and scenarios.get("has_uncertainty"):
        sp = scenarios.get("monthly_profit", {})
        rw = scenarios.get("runway", {})

        def _fmt_num(v):
            if v is None:
                return t("fmt.common.unknown")
            if isinstance(v, (int, float)):
                return f"{v:,.0f}"
            # 「无限」是引擎的**数据标记**（靠等值比较识别），不是文案。
            # 直接 str(v) 会让英文版漏出中文，必须先翻译成展示文案。
            if str(v) == _ENGINE_INFINITE_MARK:
                return t("fmt.common.infinite")
            return str(v)

        lines.append(t("fmt.scan.scenarios_header"))
        lines.append(t("fmt.scan.scenarios_note"))
        lines.append("")
        lines.append(t("fmt.scan.scenarios_table_header"))
        lines.append("|------|------|------|------|")
        lines.append(
            t(
                "fmt.scan.scenarios_profit_row",
                worst=_fmt_num(sp.get("worst")),
                base=_fmt_num(sp.get("base")),
                best=_fmt_num(sp.get("best")),
            )
        )
        lines.append(
            t(
                "fmt.scan.scenarios_runway_row",
                worst=_fmt_num(rw.get("worst")),
                base=_fmt_num(rw.get("base")),
                best=_fmt_num(rw.get("best")),
            )
        )
        drivers = scenarios.get("drivers", [])
        if drivers:
            lines.append("")
            lines.append(
                t(
                    "fmt.scan.scenarios_drivers",
                    # 分隔符与引号是**渲染标点**，不能硬编码中文（英文版会漏「」、）。
                    items=", ".join(f'"{d}"' for d in drivers),
                )
            )
        lines.append("")

    # 敏感性分析
    # 只呈现**带标签**的三档情景。_calc_sensitivity 返回 steps×steps 网格
    # （默认 3×3=9 条），标签落在索引 2/4/6；按 [:3] 切片会取到无标签行，
    # 渲染成「?」——按标签筛才与 steps 解耦。
    labeled_sens = [s for s in sensitivity if s.get("scenario")]
    if labeled_sens:
        lines.append(t("fmt.scan.sensitivity_header"))
        lines.append(t("fmt.scan.sensitivity_table_header"))
        lines.append("|------|------|------|--------|")
        for s in labeled_sens:
            rev = s.get("revenue_change", "0%")
            cost = s.get("cost_change", "0%")
            profit = s.get("profit", 0)
            lines.append(f"| {s['scenario']} | {rev} | {cost} | {profit:,.0f} |")
        lines.append("")

    # 风险
    if pitfalls:
        lines.append(t("fmt.scan.risk_header"))
        lines.append(t("fmt.scan.risk_table_header"))
        lines.append("|------|------|")
        severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
        for p in pitfalls[:5]:
            sev = p.get("severity", "medium")
            emoji = severity_emoji.get(sev, "⚪")
            title = p.get("title", "")
            lines.append(f"| {emoji} {sev} | {title} |")
        lines.append("")

    # Benchmark
    if bench and isinstance(bench, dict) and bench:
        lines.append(t("fmt.scan.benchmark_header"))
        lines.extend(_fmt_benchmark_lines(bench, industry_key))
        lines.append("")

    # 操作提示
    lines.append("---")
    lines.append(t("fmt.scan.ops_hint"))

    return "\n".join(lines)


# ─── 工具: trend_projection ──────────────────────────────────────────────
def _fmt_trend(data: Dict) -> str:
    if "error" in data:
        return t("fmt.trend.error", error=data["error"])

    # ④：稀疏输入（无月营收）下走骨架渲染，不静默展示全负值误报趋势
    if data.get("insufficient"):
        return _fmt_insufficient(data)

    months = data.get("months", [])
    summary = data.get("summary", {})

    lines = []
    lines.append(t("fmt.trend.title"))
    lines.append("")

    # 季节系数来源
    seasonal_source = data.get("seasonal_source", "")
    if seasonal_source:
        lines.append(t("fmt.trend.seasonal_source", source=seasonal_source))
        lines.append("")

    # 摘要
    if summary:
        lines.append(t("fmt.trend.summary_header"))
        lines.append(t("fmt.trend.summary_table_header"))
        lines.append("|------|------|")
        for k, v in summary.items():
            v_str = f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)
            lines.append(f"| {k} | {v_str} |")
        lines.append("")

    # 月度数据
    if months:
        lines.append(t("fmt.trend.monthly_header"))
        lines.append(t("fmt.trend.monthly_table_header"))
        lines.append("|----|------|----------|----------|------|------|")
        for m in months:
            lines.append(
                f"| {m.get('month', '?')} | "
                f"{m.get('revenue', 0):,.0f} | "
                f"{m.get('fixed_cost', 0):,.0f} | "
                f"{m.get('variable_cost', 0):,.0f} | "
                f"{m.get('profit', 0):,.0f} | "
                f"{m.get('cumulative_profit', 0):,.0f} |"
            )
        lines.append("")

    lines.append("---")
    lines.append(t("fmt.trend.ops_hint"))

    return "\n".join(lines)


# ─── 工具: compare_scenarios ─────────────────────────────────────────────
def _fmt_compare(data: Dict) -> str:
    if "error" in data:
        return t("fmt.compare.error", error=data["error"])

    # ④：任一方方案缺月营收时走骨架渲染
    if data.get("insufficient"):
        return _fmt_insufficient(data)

    base = data.get("base_scenario", {})
    alt = data.get("alt_scenario", {})
    diff = data.get("diff", {})

    lines = []
    lines.append(t("fmt.compare.title"))
    lines.append("")

    # 并排
    lines.append(t("fmt.compare.table_header"))
    lines.append("|------|-------|-------|------|")

    # 利润
    base_profit = base.get("monthly_profit", 0)
    alt_profit = alt.get("monthly_profit", 0)
    lines.append(t("fmt.compare.profit_row", base=f"{base_profit:,.0f}", alt=f"{alt_profit:,.0f}", diff=f"{alt_profit - base_profit:+,.0f}"))

    # 营收
    base_rev = base.get("monthly_revenue", 0)
    alt_rev = alt.get("monthly_revenue", 0)
    lines.append(t("fmt.compare.revenue_row", base=f"{base_rev:,.0f}", alt=f"{alt_rev:,.0f}", diff=f"{alt_rev - base_rev:+,.0f}"))

    # 固定成本
    base_fix = base.get("monthly_fixed_cost", 0)
    alt_fix = alt.get("monthly_fixed_cost", 0)
    lines.append(t("fmt.compare.fixed_row", base=f"{base_fix:,.0f}", alt=f"{alt_fix:,.0f}", diff=f"{alt_fix - base_fix:+,.0f}"))

    lines.append("")

    # 结论
    if diff.get("verdict"):
        lines.append(t("fmt.compare.verdict", verdict=diff["verdict"]))
        if "profit" in diff:
            lines.append(t("fmt.compare.profit_diff", value=f"{diff['profit']:+,.0f}"))
        if "revenue" in diff:
            lines.append(t("fmt.compare.revenue_diff", value=f"{diff['revenue']:+,.0f}"))
        lines.append("")

    lines.append("---")
    lines.append(t("fmt.compare.ops_hint"))

    return "\n".join(lines)


# ─── 工具: suggest_params ────────────────────────────────────────────────
def _fmt_suggest(data: Dict) -> str:
    if "error" in data:
        return t("fmt.suggest.error", error=data["error"])

    issues = data.get("issues", [])
    suggestions = data.get("suggestions", [])
    summary = data.get("summary", {})

    lines = []
    lines.append(t("fmt.suggest.title"))
    lines.append("")

    # 核心指标
    if summary:
        cur = summary.get("current_monthly_profit", 0)
        new = summary.get("projected_monthly_profit", 0)
        improvement = summary.get("total_expected_improvement", 0)
        verdict = summary.get("verdict", "")

        lines.append(t("fmt.suggest.expectation_header"))
        lines.append(t("fmt.suggest.current_profit", value=f"{cur:,.0f}"))
        lines.append(t("fmt.suggest.projected_profit", value=f"{new:,.0f}"))
        lines.append(t("fmt.suggest.total_improvement", value=f"{improvement:,.0f}"))
        if verdict:
            lines.append(t("fmt.suggest.verdict", verdict=verdict))
        lines.append("")

    # 问题
    if issues:
        lines.append(t("fmt.suggest.issues_header"))
        lines.append(t("fmt.suggest.issues_table_header"))
        lines.append("|--------|------|")
        severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡"}
        for issue in issues:
            sev = issue.get("severity", "medium")
            emoji = severity_emoji.get(sev, "⚪")
            lines.append(f"| {emoji} {sev} | {issue.get('message', '')} |")
        lines.append("")

    # 建议
    if suggestions:
        lines.append(t("fmt.suggest.suggestions_header"))
        lines.append(t("fmt.suggest.suggestions_table_header"))
        lines.append("|------|------|------|------|------|------------|")
        for s in suggestions:
            target = s.get("target_param", "")
            current = s.get("current", "")
            suggested = s.get("suggested", "")
            direction = s.get("direction", "")
            rationale = s.get("rationale", "")
            delta = s.get("expected_profit_delta", 0)
            delta_str = t("fmt.common.delta_money", v=f"{delta:,.0f}") if delta > 0 else "—"
            # 转 int 显示更整洁
            if isinstance(current, float) and current.is_integer():
                current = int(current)
            if isinstance(suggested, float) and suggested.is_integer():
                suggested = int(suggested)
            lines.append(f"| {target} | {current} | {suggested} | {direction} | {rationale} | {delta_str} |")
        lines.append("")

    lines.append("---")
    lines.append(t("fmt.suggest.ops_hint"))
    lines.append("")
    lines.append(t("fmt.suggest.ops_tip"))

    return "\n".join(lines)


# ─── 工具: report ─────────────────────────────────────────────────────────
def _fmt_report(data: Dict) -> str:
    if "error" in data:
        return t("fmt.report.error", error=data["error"])

    lines = []
    lines.append(t("fmt.report.title"))
    lines.append("")

    file_path = data.get("file_path") or data.get("url") or data.get("path")
    if file_path:
        lines.append(t("fmt.report.generated_with_path", path=file_path))
    else:
        lines.append(t("fmt.report.generated"))

    # 摘要
    if data.get("summary"):
        lines.append("")
        lines.append(str(data["summary"]))

    return "\n".join(lines)


# ─── 工具: benchmark ──────────────────────────────────────────────────────
def _fmt_benchmark(data: Dict) -> str:
    if "error" in data:
        return t("fmt.benchmark.error", error=data["error"])

    lines = []
    lines.append(t("fmt.benchmark.title"))
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(data, ensure_ascii=False, indent=2))
    lines.append("```")

    return "\n".join(lines)


# ─── 成本归因拆解 ────────────────────────────────────────────────────────

def _fmt_attribution(data: Dict) -> str:
    """成本归因拆解渲染：各分量金额+占比+风险提示。"""
    if "error" in data:
        return t("fmt.attribution.error", error=data["error"])
    if data.get("insufficient"):
        md = [t("fmt.attribution.cannot_title"), ""]
        md.append(data.get("message", t("fmt.attribution.cannot_message")))
        for g in data.get("gaps", []):
            md.append(t("fmt.common.supplement", item=g))
        return "\n".join(md)

    lines = []
    lines.append(t("fmt.attribution.title"))
    lines.append("")

    total = data.get("total_monthly_cost", 0)
    fixed = data.get("fixed_cost", 0)
    variable = data.get("variable_cost", 0)
    lines.append(t("fmt.attribution.total_cost", total=f"{total:,.0f}", fixed=f"{fixed:,.0f}", variable=f"{variable:,.0f}"))
    lines.append(t("fmt.attribution.ratio", fixed=data.get("fixed_ratio", 0), variable=data.get("variable_ratio", 0)))
    lines.append("")

    components = data.get("components", [])
    if components:
        lines.append(t("fmt.attribution.components_header"))
        lines.append("")
        lines.append(t("fmt.attribution.components_table_header"))
        lines.append("|------|------|------|------|")
        for c in components:
            pct = f"{c['percent']:.0%}"
            lines.append(t("fmt.attribution.component_row", name=c["name"], amount=f"{c['amount']:,.0f}", pct=pct, source=c.get("source", "")))
        lines.append("")

    # 可视化条形（文本版）
    if components:
        lines.append(t("fmt.attribution.chart_header"))
        lines.append("")
        for c in components[:5]:
            bar_len = int(c["percent"] * 30)
            bar = "█" * bar_len + "░" * (30 - bar_len)
            lines.append(f"  {c['name']:　<5} {bar} {c['percent']:.0%}（{c['amount']:,.0f}）")
        lines.append("")

    top = data.get("top_component", {})
    if top.get("name"):
        lines.append(t("fmt.attribution.top_component", name=top["name"], amount=f"{top['amount']:,.0f}", pct=f"{top['percent']:.0%}"))
        lines.append("")

    warnings = data.get("warnings", [])
    if warnings:
        lines.append(t("fmt.attribution.warnings_header"))
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append("---")
    lines.append(t("fmt.attribution.ops_hint"))

    return "\n".join(lines)


# ─── 敏感度分析（单一变量弹性）────────────────────────────────────────────

def _fmt_sensitivity(data: Dict) -> str:
    """单一变量弹性分析渲染：盈亏平衡点 + 安全边际 + 曲线。"""
    if "error" in data:
        return t("fmt.sensitivity.error", error=data["error"])
    if data.get("insufficient"):
        md = [t("fmt.sensitivity.cannot_title"), ""]
        md.append(data.get("message", t("fmt.sensitivity.cannot_message")))
        for g in data.get("gaps", []):
            md.append(t("fmt.common.supplement", item=g))
        return "\n".join(md)

    lines = []
    lines.append(t("fmt.sensitivity.title"))
    lines.append("")

    label = data.get("variable_label", "")
    current = data.get("current_value", 0)
    breakeven = data.get("breakeven_value")

    if breakeven is None:
        lines.append(t("fmt.sensitivity.variable", label=label))
        lines.append(t("fmt.sensitivity.current", value=f"{current:g}"))
        lines.append("")
        lines.append(data.get("interpretation", t("fmt.sensitivity.no_breakeven")))
        return "\n".join(lines)

    margin = data.get("margin", 0)
    margin_pct = data.get("margin_pct", 0)
    direction = data.get("direction", "")

    lines.append(t("fmt.sensitivity.variable", label=label))
    lines.append(t("fmt.sensitivity.current", value=f"{current:g}"))
    lines.append(t("fmt.sensitivity.breakeven", value=f"{breakeven:g}"))
    lines.append(t("fmt.sensitivity.margin", value=f"{margin:g}", pct=f"{margin_pct:.0%}", direction=direction))
    lines.append("")

    # 解读
    lines.append(f"> {data.get('interpretation', '')}")
    lines.append("")

    # 敏感度曲线
    curve = data.get("sensitivity_curve", [])
    if curve:
        lines.append(t("fmt.sensitivity.curve_header"))
        lines.append("")
        lines.append(t("fmt.sensitivity.curve_table_header", label=label))
        lines.append("|------|--------|------|")
        for point in curve:
            v = point["value"]
            p = point["profit"]
            status = "🟢" if p > 0 else ("🔴" if p < 0 else "⚪")
            lines.append(t("fmt.sensitivity.curve_row", value=f"{v:g}", profit=f"{p:,.0f}", status=status))
        lines.append("")

    lines.append("---")
    lines.append(t("fmt.sensitivity.ops_hint"))

    return "\n".join(lines)


# ─── L2 决策 ────────────────────────────────────────────────────────────

def _fmt_decision(data: Dict) -> str:
    """L2 决策结果渲染（决策输出规范 4.3：客观结构 + 一次性验证建议，无倾向）。"""
    from decision_engine import render_decision
    return render_decision(data)


# ─── 现金流明细表（档 B）────────────────────────────────────────────────

def _fmt_cashflow(data: Dict) -> str:
    """月现金流明细表渲染。

    缺期初现金/营收 → 「还不能定」+ 补什么；否则 12 行明细 + 归零月/累计缺口。
    """
    if "error" in data:
        return t("fmt.cashflow.error", error=data["error"])
    if data.get("insufficient"):
        md = [t("fmt.cashflow.cannot_title"), ""]
        md.append(t("fmt.cashflow.cannot_message"))
        for g in data.get("gaps", []):
            md.append(t("fmt.common.supplement", item=g))
        return "\n".join(md)

    md = [t("fmt.cashflow.title"), ""]
    project_type = (industry_name(data.get("project_type"))
                    or t("fmt.common.project_type_default"))
    md.append(t("fmt.cashflow.opening", project_type=project_type, value=_fmt_num_cf(data.get("opening_now"))))
    if data.get("notes"):
        md.append("")
        md.extend(f"- {n}" for n in data["notes"])
    md.append("")
    md.append(t("fmt.cashflow.table_header"))
    md.append("|----|---------|------|------|---------|")
    schedule = data.get("schedule", [])
    for row in schedule:
        md.append(
            f"| {row['month']} | {_fmt_num_cf(row['inflow'])} | {_fmt_num_cf(row['outflow'])} "
            f"| {_fmt_num_cf(row['net'])} | {_fmt_num_cf(row['closing'])} |"
        )
    md.append("")

    zc = data.get("zero_cash_month")
    if zc is not None:
        md.append(t("fmt.cashflow.zero_cash", month=zc))
    else:
        md.append(t("fmt.cashflow.no_zero_cash"))
    ms = data.get("max_shortfall")
    if ms is not None:
        md.append(t("fmt.cashflow.max_shortfall", value=f"{ms:,.0f}"))
    md.append("")
    md.append(t("fmt.cashflow.note"))
    return "\n".join(md)


def _fmt_num_cf(v) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}" if isinstance(v, (int, float)) else str(v)


# ─── 路由 ────────────────────────────────────────────────────────────────
_FORMATTERS = {
    "quick_scan": _fmt_scan,
    "trend": _fmt_trend,
    "compare": _fmt_compare,
    "suggest": _fmt_suggest,
    "decide": _fmt_decision,
    "cashflow": _fmt_cashflow,
    "attribution": _fmt_attribution,
    "sensitivity": _fmt_sensitivity,
    "report_pdf": _fmt_report,
    "report_excel": _fmt_report,
    "benchmark": _fmt_benchmark,
    "market": _fmt_benchmark,  # 临时复用
}


def format_response(intent: str, tool_data: Dict) -> str:
    """根据意图和工具数据，返回 Markdown 字符串。"""
    formatter = _FORMATTERS.get(intent)
    if formatter is None:
        return json.dumps(tool_data, ensure_ascii=False, indent=2)
    return formatter(tool_data)
