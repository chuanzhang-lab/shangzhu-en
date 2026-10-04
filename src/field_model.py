"""声明式财务字段模型（Field Model）— P&L 主干公式的唯一出处。

设计目标（结构性重构，根治「公式散落/重复实现」）：
1. 每个「派生字段」只在此声明一次：公式、依赖、标签、单位、来源语义。
2. `derive()` 是通用求值器：给定「输入字段集」（含缺失 None），沿依赖拓扑
   把能精确推出的派生字段全部算出；缺依赖 → 该字段 missing。
3. `consistency_issues()` 声明「可互相校验的关系」（如月营收 vs 客流×单价），
   由求值后自动检查，死代码问题消失。
4. 应用层（workflow_engine / formatter / decision）一律消费模型，不再手写公式。

与「默认值」的分界：本模型只做**精确推导**（给够输入即出精确值），
绝不填任何猜测性默认——输入缺失即 missing，由上层决定如何呈现。

骨架完整性（D13 改进）：
- INPUT_SPECS：输入字段声明（label_key/unit_key/type），与 DERIVED_SPECS 统一
- derive()：始终尝试公式（不做 deps 硬门控），返回 (values, meta)
- monthly_fixed_cost：真公式（sum of present components）
- variable_cost_ratio：多路推导（user > unit_var÷price > 1−gm > None）
- _topo_order()：循环依赖检测
- 所有公式用 .get() 容错

文案外置：`label` / `unit` 只存**键**（label_key / unit_key），展示时经 `t()` 解析，
因此切换 locale 无需重建模型。⚠️ `aliases` 是抽取器的中文别名，属输入层（P2），
**不参与外置**，改动它会直接破坏参数抽取。
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from i18n import t

import source_tags

# 注：原 `_USER_SOURCE_MARK = "[用户]"` 已删除——它是拿**展示文案**当状态码，
# en 下恒不匹配。来源判定统一走 `source_tags.code_of_display()`。


def _L(suffix: str) -> str:
    """字段标签键 → 文案。"""
    return t(f"field.label.{suffix}")


def _U(unit_key: str) -> str:
    """单位键 → 文案（空键返回空串，不是缺失）。"""
    return t(f"field.unit.{unit_key}") if unit_key else ""


# ── 输入字段声明 ─────────────────────────────────────────────────────────
# 用户可提供的字段：label_key/unit_key/type
INPUT_SPECS: Dict[str, Dict[str, Any]] = {
    "total_investment": {"label_key": "total_investment", "unit_key": "yuan", "type": "float"},
    "monthly_rent": {"label_key": "monthly_rent", "unit_key": "yuan_per_month", "type": "float"},
    "daily_traffic": {"label_key": "daily_traffic", "unit_key": "person_per_day", "type": "float"},
    "price_per_unit": {"label_key": "price_per_unit", "unit_key": "yuan", "type": "float"},
    "employee_count": {"label_key": "employee_count", "unit_key": "person", "type": "float"},
    "avg_salary": {"label_key": "avg_salary", "unit_key": "yuan_per_month", "type": "float"},
    "labor_burden": {"label_key": "labor_burden", "unit_key": "", "type": "float"},
    "variable_cost_ratio": {"label_key": "variable_cost_ratio", "unit_key": "ratio", "type": "float"},
    "unit_variable_cost": {"label_key": "unit_variable_cost", "unit_key": "yuan_per_unit", "type": "float"},
    # S3（2026-09-12）：统一为 0~1 口径 —— 与 param_guard 的归一化、
    # 与 variable_cost_ratio 的 "0~1" 对齐。旧声明 "%" 与公式里的 ÷100 自相矛盾，
    # 导致 derive({"gross_margin": 0.6}) 算出 vcr=0.994（应为 0.4）。
    "gross_margin": {"label_key": "gross_margin", "unit_key": "ratio", "type": "float"},
    "utilities": {"label_key": "utilities", "unit_key": "yuan_per_month", "type": "float"},
    "packaging": {"label_key": "packaging", "unit_key": "yuan_per_month", "type": "float"},
    "commission": {"label_key": "commission", "unit_key": "yuan_per_month", "type": "float"},
    "other_fixed": {"label_key": "other_fixed", "unit_key": "yuan_per_month", "type": "float"},
    "equipment_ratio": {"label_key": "equipment_ratio", "unit_key": "ratio", "type": "float"},
    "monthly_revenue": {"label_key": "monthly_revenue", "unit_key": "yuan_per_month", "type": "float"},
    "monthly_profit": {"label_key": "monthly_profit", "unit_key": "yuan_per_month", "type": "float"},
    "monthly_expense": {"label_key": "monthly_expense", "unit_key": "yuan_per_month", "type": "float"},
    "stage": {"label_key": "stage", "unit_key": "", "type": "str"},
    "industry": {"label_key": "industry", "unit_key": "", "type": "str"},
    "founder_count": {"label_key": "founder_count", "unit_key": "person", "type": "int"},
    "monthly_growth_rate": {"label_key": "monthly_growth_rate", "unit_key": "ratio", "type": "float"},
    "seasonal_factor": {"label_key": "seasonal_factor", "unit_key": "", "type": "float"},
    "analysis_months": {"label_key": "analysis_months", "unit_key": "month", "type": "int"},
}

# ── 派生字段声明 ─────────────────────────────────────────────────────────
# 每个字段：
#   deps    : 依赖字段（用于拓扑排序，不用于硬门控）
#   formula : 计算公式（lambda params -> value；用 .get() 容错；返回 None 即 missing）
#   label_key: 显示名键（经 t() 解析）
#   unit_key: 单位键（经 t() 解析）
#   kind    : "derived"（纯公式推） | "override"（用户可覆盖，优先用用户值）
#   user_direct_ok: 是否允许显示「用户直接给出」（仅 monthly_revenue/monthly_profit）
#   describe: 返回「带实际数字的公式说明」字符串
#   _hidden : 中间量，不单独展示

# ── 时间口径常量（F1）──────────────────────────────────────────────────
# 「一个月按多少天算」是**全系统唯一**的口径，月营收、保本客流、年化成本
# 必须共用它。旧实现里月营收写死 30、保本客流却除 365（=30.42 天/月），
# 两个口径并存在同一份报表里，保本客流被系统性低估约 1.4%，
# 按系统给的保本客流经营实际是亏的。此处定为唯一出处。
DAYS_PER_MONTH = 30
MONTHS_PER_YEAR = 12
DAYS_PER_YEAR = DAYS_PER_MONTH * MONTHS_PER_YEAR   # 360，不是 365


def monthly_revenue_from_traffic(daily_traffic, price_per_unit):
    """月营收 = 日均客流 × 客单价 × DAYS_PER_MONTH —— **唯一出处**。

    审计发现这个公式曾在 4 处各写一遍（field_model ×2 / param_guard / web_server），
    其中两处是裸 `30`、保本侧还出现过 `365` 口径。改口径时必然只改到一部分，
    F1（营收按 360 天、保本按 365 天）就是这么来的。其余一律调用本函数。

    依赖缺失返回 None（与 _prod 同语义）；结果为 0 就是 0，不是缺失。
    """
    if daily_traffic is None or price_per_unit is None:
        return None
    return daily_traffic * price_per_unit * DAYS_PER_MONTH


def _prod(vals):
    """全部依赖非 None 才相乘（**结果可以是 0**）；任一依赖缺失则返回 None。

    F9 修复：不能写成 `a * b or None` —— 0 是合法的计算结果
    （变动成本率 0 ⇒ 月变动成本 0；无雇员 ⇒ 月人工 0），
    而 `or None` 会把 0 当成 falsy 转成 None，于是「能算但算成 0」
    被呈现成「算不出来 / 未知」。财务语境里这两者天差地别。
    """
    if any(v is None for v in vals):
        return None
    out = 1
    for v in vals:
        out = out * v
    return out


# 月固定成本的构成组件（F9：求和前要判断「是否全缺失」，不能靠 `or None`）
_FIXED_COST_PARTS = ("monthly_rent", "monthly_labor", "utilities",
                     "packaging", "commission", "other_fixed")

DERIVED_SPECS: Dict[str, Dict[str, Any]] = {
    # 月营收：用户直接给 或 客流×单价×30
    "monthly_revenue": {
        "deps": ["daily_traffic", "price_per_unit"],
        "formula": lambda p: _prod([p.get("daily_traffic"), p.get("price_per_unit"),
                                    DAYS_PER_MONTH]),
        "label_key": "monthly_revenue", "unit_key": "yuan_per_month", "kind": "override", "user_direct_ok": True,
        "describe": lambda p: t("field.desc.monthly_revenue",
                                traffic=f"{p.get('daily_traffic', 0):g}",
                                price=f"{p.get('price_per_unit', 0):g}",
                                days=DAYS_PER_MONTH),
    },
    # 变动成本率：多路推导（用户 > unit_var÷price > 1−gm > None）
    # S3：gm 为 0~1 口径，故此处是 `1 − gm`（不再是 `1 − gm/100`）。
    "variable_cost_ratio": {
        "deps": ["price_per_unit"],
        "formula": lambda p: (
            (p.get("unit_variable_cost") / p["price_per_unit"])
            if p.get("unit_variable_cost") is not None and p.get("price_per_unit")
            else (1 - p.get("gross_margin", 0))
            if p.get("gross_margin") is not None
            else None
        ),
        "label_key": "variable_cost_ratio", "unit_key": "ratio", "kind": "override", "user_direct_ok": True,
        "display_percent": True,   # 内部 0~1，展示为百分数
        "describe": lambda p: (
            t("field.desc.vcr_from_unit_cost",
              uvc=f"{p.get('unit_variable_cost', 0):g}",
              price=f"{p.get('price_per_unit', 0):g}")
            if p.get("unit_variable_cost") is not None
            else t("field.desc.vcr_from_gross_margin", gm=f"{p.get('gross_margin', 0):.0%}")
            if p.get("gross_margin") is not None
            else t("field.src.derived")
        ),
    },
    # 月人工现金：人数 × 人均薪资（中间量）
    "monthly_labor_cash": {
        "deps": ["employee_count", "avg_salary"],
        # F9：明确「0 个员工」（夫妻店/无人值守）时人工就是 0，不要求再报人均薪资
        # —— 用户说「我不请人」时不会顺带说薪资，按缺失处理会让成本归因显示"未知"。
        "formula": lambda p: (0 if p.get("employee_count") == 0
                              else _prod([p.get("employee_count"), p.get("avg_salary")])),
        "label_key": "monthly_labor", "unit_key": "yuan_per_month", "kind": "derived", "_hidden": True,
        "describe": lambda p: t("field.desc.monthly_labor",
                                count=f"{p.get('employee_count', 0):g}",
                                salary=f"{p.get('avg_salary', 0):g}",
                                yuan=_U("yuan")),
    },
    # 月人工（含负担）：裸薪 × (1+负担率)
    "monthly_labor": {
        "deps": ["monthly_labor_cash", "labor_burden"],
        # F9：labor_burden 缺失时按 0 计（不是缺失），但 monthly_labor_cash 缺失就真缺失。
        "formula": lambda p: (None if p.get("monthly_labor_cash") is None
                              else p["monthly_labor_cash"] * (1 + (p.get("labor_burden") or 0))),
        "label_key": "monthly_labor", "unit_key": "yuan_per_month", "kind": "derived",
        "describe": lambda p: (
            t("field.desc.monthly_labor",
              count=f"{p.get('employee_count', 0):g}",
              salary=f"{p.get('avg_salary', 0):g}",
              yuan=_U("yuan"))
            + (t("field.desc.labor_burden_tail", rate=f"{p.get('labor_burden_rate', 0):.0%}")
               if p.get("labor_burden_rate") else "")
        ),
    },
    # 月固定成本：组件求和（有值组件之和，无值跳过，全 None → None）
    "monthly_fixed_cost": {
        "deps": ["monthly_rent", "monthly_labor"],
        # F9：旧写法 `sum(...) or None` 在组件全为 None 时正确返回 None，
        # 但组件有值而和为 0（月租 0、无雇员）时也会把 0 吞成 None。
        # 改为先判「是否全缺失」，再求和。
        "formula": lambda p: (
            None if all(p.get(k) is None for k in _FIXED_COST_PARTS)
            else sum((p.get(k) or 0) for k in _FIXED_COST_PARTS)
        ),
        "label_key": "monthly_fixed_cost", "unit_key": "yuan_per_month", "kind": "override",
        "describe": lambda p: _describe_fixed_cost(p),
    },
    # 月变动成本：月营收 × 变动成本率
    "monthly_variable_cost": {
        "deps": ["monthly_revenue", "variable_cost_ratio"],
        "formula": lambda p: _prod([p.get("monthly_revenue"), p.get("variable_cost_ratio")]),
        "label_key": "monthly_variable_cost", "unit_key": "yuan_per_month", "kind": "derived",
        "describe": lambda p: t("field.desc.monthly_variable_cost",
                                rev=f"{p.get('monthly_revenue', 0):g}",
                                vcr=f"{p.get('variable_cost_ratio', 0):.0%}"),
    },
    # 月利润：营收 - 固定 - 变动
    "monthly_profit": {
        "deps": ["monthly_revenue", "monthly_fixed_cost", "monthly_variable_cost"],
        "formula": lambda p: (
            (p.get("monthly_revenue") or 0) - (p.get("monthly_fixed_cost") or 0)
            - (p.get("monthly_variable_cost") or 0)
            if all(p.get(d) is not None for d in ("monthly_revenue", "monthly_fixed_cost", "monthly_variable_cost"))
            else None
        ),
        "label_key": "monthly_profit", "unit_key": "yuan_per_month", "kind": "override", "user_direct_ok": True,
        "describe": lambda p: t("field.desc.monthly_profit",
                                rev=f"{p.get('monthly_revenue', 0):g}",
                                fixed=f"{p.get('monthly_fixed_cost', 0):g}",
                                variable=f"{p.get('monthly_variable_cost', 0):g}"),
    },
    # 单位变动成本：客单价 × 变动成本率
    "variable_cost_per_unit": {
        "deps": ["price_per_unit", "variable_cost_ratio"],
        "formula": lambda p: _prod([p.get("price_per_unit"), p.get("variable_cost_ratio")]),
        "label_key": "unit_variable_cost", "unit_key": "yuan_per_unit", "kind": "derived",
        "describe": lambda p: t("field.desc.variable_cost_per_unit",
                                price=f"{p.get('price_per_unit', 0):g}",
                                vcr=f"{p.get('variable_cost_ratio', 0):.0%}"),
    },
    # 年固定成本：月固定 × 12
    "annual_fixed_cost": {
        "deps": ["monthly_fixed_cost"],
        "formula": lambda p: _prod([p.get("monthly_fixed_cost"), MONTHS_PER_YEAR]),
        "label_key": "annual_fixed_cost", "unit_key": "yuan_per_year", "kind": "derived",
        "describe": lambda p: t("field.desc.annual_fixed_cost",
                                fixed=f"{p.get('monthly_fixed_cost', 0):g}",
                                months=MONTHS_PER_YEAR),
    },
    # 毛利率：1 - 变动成本率（S3：统一 0~1 口径，展示时 ×100）
    "gross_margin": {
        "deps": ["variable_cost_ratio"],
        "formula": lambda p: round(1 - p.get("variable_cost_ratio", 0), 4) if p.get("variable_cost_ratio") is not None else None,
        "label_key": "gross_margin", "unit_key": "ratio", "kind": "derived",
        "display_percent": True,   # 内部 0~1，展示为百分数
        "describe": lambda p: t("field.desc.gross_margin", vcr=f"{p.get('variable_cost_ratio', 0):.0%}"),
    },
    # 可用现金：总投资（设备占比可选扣减）
    "available_cash": {
        "deps": ["total_investment"],
        "formula": lambda p: (
            p.get("total_investment") * (1 - p.get("equipment_ratio", 0))
            if p.get("equipment_ratio") is not None
            else p.get("total_investment")
        ) if p.get("total_investment") is not None else None,
        "label_key": "available_cash", "unit_key": "yuan", "kind": "derived",
        "describe": lambda p: (
            t("field.desc.available_cash_with_ratio",
              investment=f"{p.get('total_investment', 0):g}",
              ratio=f"{p.get('equipment_ratio', 0):.0%}")
            if p.get("equipment_ratio") is not None
            else t("field.desc.available_cash", investment=f"{p.get('total_investment', 0):g}")
        ),
    },
}


# ── 字段分类体系 ────────────────────────────────────────────────────────
# A 类（纯输入）：用户直接给，无公式 → INPUT_SPECS 中的字段
# B 类（可覆盖）：用户可直接给，也可公式推 → DERIVED_SPECS 中 kind="override"
# C 类（纯派生）：只能公式推 → DERIVED_SPECS 中 kind="derived"

PURE_INPUT_FIELDS: set = set(INPUT_SPECS.keys())
OVERRIDABLE_FIELDS: set = {name for name, spec in DERIVED_SPECS.items() if spec.get("kind") == "override"}
PURE_DERIVED_FIELDS: set = {name for name, spec in DERIVED_SPECS.items() if spec.get("kind") == "derived"}

# ── 统一字段注册表 ──────────────────────────────────────────────────────

def field_label(name: str) -> str:
    """字段显示名（随 locale）：先查派生表，再查输入表，兜底字段名本身。"""
    if name in DERIVED_SPECS:
        return _L(DERIVED_SPECS[name]["label_key"])
    spec = INPUT_SPECS.get(name)
    return _L(spec["label_key"]) if spec else name


def field_unit(name: str) -> str:
    """字段显示单位（随 locale）。"""
    spec = DERIVED_SPECS.get(name) or INPUT_SPECS.get(name)
    return _U(spec["unit_key"]) if spec else ""


# ── 固定成本组件展示 ────────────────────────────────────────────────────

# (标签键所在命名空间, 字段名)：租金/人工 用组件专属文案，其余复用字段标签
_FIXED_COST_COMPONENTS = [
    ("field.comp.rent", "monthly_rent"),
    ("field.comp.labor", "monthly_labor"),
    ("field.label.utilities", "utilities"),
    ("field.label.packaging", "packaging"),
    ("field.label.commission", "commission"),
    ("field.label.other_fixed", "other_fixed"),
]


def _describe_fixed_cost(p: Dict[str, Any]) -> str:
    """动态列出实际存在的固定成本组件。"""
    has_labor = p.get("employee_count") is not None and p.get("avg_salary") is not None
    parts: List[str] = []
    for key, field in _FIXED_COST_COMPONENTS:
        if field == "monthly_labor":
            if has_labor:
                parts.append(t("field.desc.fixed_cost_labor",
                               count=f"{p.get('employee_count', 0):g}",
                               salary=f"{p.get('avg_salary', 0):g}"))
            continue
        if p.get(field) is not None:
            parts.append(t(key))
    return " + ".join(parts) if parts else t("field.desc.fixed_cost_fallback")


# ── 通用求值器（拓扑求值 + 循环检测）────────────────────────────────────

def _topo_order() -> List[str]:
    """按依赖拓扑排序派生字段。检测循环依赖。"""
    order: List[str] = []
    in_progress: set = set()
    done: set = set()

    def visit(name: str, path: tuple = ()):
        if name in done:
            return
        if name in in_progress:
            cycle = " → ".join(path + (name,))
            raise ValueError(t("field.msg.cycle_dependency", cycle=cycle))
        in_progress.add(name)
        for dep in DERIVED_SPECS[name]["deps"]:
            if dep in DERIVED_SPECS:
                visit(dep, path + (name,))
        in_progress.discard(name)
        done.add(name)
        order.append(name)

    for name in DERIVED_SPECS:
        visit(name)
    return order


_DERIVED_ORDER = _topo_order()


def derive(params: Dict[str, Any], user_overrides: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Optional[float]], Dict[str, Dict[str, Any]]]:
    """求值所有派生字段。

    返回 (values, meta):
    - values: {field: value 或 None}
    - meta:   {field: {source: "user"|"derived"|"missing", formula: str(带数字)}}
      source="user"  → 用户直接给了该值（来自 user_overrides 或 params）
      source="derived" → 公式精确推出
      source="missing" → 缺依赖，无法算

    核心逻辑：
    - B 类字段（kind="override"）：优先查 user_overrides → 有值用用户值；无值 → 公式
    - C 类字段（kind="derived"）：始终公式推
    - 公式返回 None 即 missing
    """
    work: Dict[str, Any] = dict(params)
    values: Dict[str, Optional[float]] = {}
    meta: Dict[str, Dict[str, Any]] = {}
    user_overrides = user_overrides or {}

    for name in _DERIVED_ORDER:
        spec = DERIVED_SPECS[name]
        # ① B 类字段：优先查 user_overrides
        if spec.get("kind") == "override" and name in user_overrides and user_overrides[name] is not None:
            val = user_overrides[name]
            values[name] = val
            work[name] = val
            formula_desc = t("field.src.user_direct")
            if spec.get("describe"):
                try:
                    formula_desc = spec["describe"](work)
                except (KeyError, TypeError, ValueError):
                    pass
            meta[name] = {"source": "user", "formula": formula_desc}
            continue
        # ② 用户已直接给（override 语义，兼容旧逻辑）→ 用用户值
        if name in params and params.get(name) is not None:
            val = params[name]
            values[name] = val
            work[name] = val
            # 仍然算 describe（带数字公式），「用户直接给出」由 derived_values 靠 src 判定
            formula_desc = t("field.src.user_direct")
            if spec.get("describe"):
                try:
                    formula_desc = spec["describe"](work)
                except (KeyError, TypeError, ValueError):
                    pass
            meta[name] = {"source": "user", "formula": formula_desc}
            continue
        # ③ 尝试公式求值
        try:
            val = spec["formula"](work)
        except (TypeError, KeyError, ZeroDivisionError):
            val = None
        if val is not None:
            work[name] = val
            values[name] = val
            # 公式说明
            formula_desc = t("field.src.derived")
            if spec.get("describe"):
                try:
                    formula_desc = spec["describe"](work)
                except (KeyError, TypeError, ValueError):
                    pass
            meta[name] = {"source": "derived", "formula": formula_desc}
        else:
            values[name] = None
            meta[name] = {"source": "missing", "formula": ""}
    return values, meta


# ── 一致性规则 ──────────────────────────────────────────────────────────

def _rule_revenue_vs_traffic_price(params: Dict[str, Any]) -> Optional[str]:
    rev = params.get("monthly_revenue")
    traffic = params.get("daily_traffic")
    price = params.get("price_per_unit")
    if all(isinstance(x, (int, float)) and x > 0 for x in (rev, traffic, price)):
        implied = monthly_revenue_from_traffic(traffic, price)
        if abs(implied - rev) / rev > 0.5:
            return t("field.rule.revenue_vs_traffic",
                     rev=f"{rev:,.0f}", traffic=f"{traffic:g}", price=f"{price:g}",
                     days=DAYS_PER_MONTH, implied=f"{implied:,.0f}")
    return None


def _rule_cost_structure(params: Dict[str, Any]) -> Optional[str]:
    rev = params.get("monthly_revenue")
    vc_ratio = params.get("variable_cost_ratio")
    fixed = params.get("monthly_fixed_cost")
    if all(isinstance(x, (int, float)) for x in (rev, vc_ratio, fixed)):
        if isinstance(rev, (int, float)) and rev > 0:
            total_cost = fixed + rev * vc_ratio
            if total_cost > rev * 10:
                return t("field.rule.cost_unsustainable",
                         total=f"{total_cost:,.0f}", rev=f"{rev:,.0f}")
    return None


def _rule_vcr_vs_gross_margin(params: Dict[str, Any]) -> Optional[str]:
    """用户同时显式给出 variable_cost_ratio 与 gross_margin，且二者不满足恒等式。

    恒等式：gross_margin = 1 − variable_cost_ratio。

    旧行为（缺陷）：显式 vcr 优先，用户给的 gm 被**静默丢弃**——
    「变动成本率40% + 毛利率50%」最终输出毛利率 60%，用户说的 50% 凭空消失且无任何提示。
    这违背产品核心承诺「说清我们知道什么」，属于静默篡改用户输入。
    本规则只负责**指出矛盾**，不改数（改数交给冲突处置 ops 让用户确认）。
    """
    vcr = params.get("variable_cost_ratio")
    # 优先用**用户原始的**毛利率：derive() 之后 gross_margin 已被 1−vcr 覆盖，
    # 直接读它只会得到「永远一致」的假象（这正是旧行为静默丢弃用户输入的成因）。
    # `_user_gross_margin` 由 _fill_params 在覆盖前保留（无该键时退回当前值）。
    gm = params.get("_user_gross_margin")
    if gm is None:
        gm = params.get("gross_margin")
    if not all(isinstance(x, (int, float)) for x in (vcr, gm)):
        return None
    implied = 1.0 - float(vcr)
    # 容差 2 个百分点：避免浮点噪声与四舍五入（如 vcr=0.4 → gm=0.6 精确相等时不得误报）
    if abs(implied - float(gm)) > 0.02:
        return t("field.rule.vcr_vs_gross_margin",
                 gm=f"{float(gm) * 100:.0f}", vcr=f"{float(vcr) * 100:.0f}",
                 implied=f"{implied * 100:.0f}")
    return None


def _rule_vcr_vs_unit_cost(params: Dict[str, Any]) -> Optional[str]:
    """用户同时给出「每份成本 ÷ 客单价」与「变动成本率」，两者不一致时提示。

    与 `_rule_vcr_vs_gross_margin` 是同一类问题：变动成本率有三个可互相校验的
    口径（物理量除法、1−毛利率、用户直述），此前只校验了「率 vs 毛利率」这一对，
    「每份成本12元 + 客单价15（⇒80%）」与「率75%」并存的矛盾完全静默——
    引擎悄悄选了率，用户以为两个数都被采纳了。

    不会误报：率本身是从 unit_var÷price 推出来时两者恒等，差值恒为 0。
    只提示，不改值（改值交给 conflict_resolution_ops 让用户确认）。
    """
    vcr = params.get("variable_cost_ratio")
    uvc = params.get("unit_variable_cost")
    price = params.get("price_per_unit")
    if not all(isinstance(x, (int, float)) for x in (vcr, uvc, price)):
        return None
    if not price:
        return None
    implied = float(uvc) / float(price)
    # 容差 2 个百分点（与 _rule_vcr_vs_gross_margin 同口径），避开浮点噪声
    if abs(implied - float(vcr)) > 0.02:
        return t("field.rule.vcr_vs_unit_cost",
                 vcr=f"{float(vcr) * 100:.0f}", uvc=f"{float(uvc):g}",
                 price=f"{float(price):g}", implied=f"{implied * 100:.0f}")
    return None


CONSISTENCY_RULES: List[Callable[[Dict[str, Any]], Optional[str]]] = [
    _rule_revenue_vs_traffic_price,
    _rule_cost_structure,
    _rule_vcr_vs_gross_margin,
    _rule_vcr_vs_unit_cost,
]


def consistency_issues(params: Dict[str, Any]) -> List[Dict[str, str]]:
    """求值后跑一致性规则，返回 [{field, message}]。"""
    issues: List[Dict[str, str]] = []
    for rule in CONSISTENCY_RULES:
        msg = rule(params)
        if msg:
            issues.append({"field": "consistency", "message": msg})
    return issues


def conflict_resolution_ops(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """当一致性规则检出冲突时，自动生成「口径对齐 ops」让用户一键确认。

    每个 op 是 {propose, label, changes, reason}，走现有「应用X」确认流。
    不靠 LLM 猜——规则层自己给出两个修正方向。
    """
    ops: List[Dict[str, Any]] = []
    rev = params.get("monthly_revenue")
    traffic = params.get("daily_traffic")
    price = params.get("price_per_unit")
    if all(isinstance(x, (int, float)) and x > 0 for x in (rev, traffic, price)):
        implied = monthly_revenue_from_traffic(traffic, price)
        if abs(implied - rev) / rev > 0.5:
            # 方案A：按客流×单价×30 修正月营收
            ops.append({
                "propose": "set",
                "label": t("field.op.set_revenue", value=f"{implied:,.0f}", days=DAYS_PER_MONTH),
                "changes": {"monthly_revenue": implied},
                "reason": t("field.op.set_revenue_reason", traffic=f"{traffic:g}",
                            price=f"{price:g}", days=DAYS_PER_MONTH, implied=f"{implied:,.0f}"),
                "hypothesis": None,
            })
            # 方案B：按月营收反推客流
            implied_traffic = round(rev / (price * DAYS_PER_MONTH), 0)
            ops.append({
                "propose": "set",
                "label": t("field.op.set_traffic", value=f"{implied_traffic:.0f}"),
                "changes": {"daily_traffic": implied_traffic},
                "reason": t("field.op.set_traffic_reason", rev=f"{rev:,.0f}",
                            price=f"{price:g}", days=DAYS_PER_MONTH,
                            implied=f"{implied_traffic:.0f}"),
                "hypothesis": None,
            })

    # vcr 与 gross_margin 矛盾：给出两个口径对齐方向（与上面同类，规则层自己给选项，不靠 LLM 猜）
    vcr = params.get("variable_cost_ratio")
    gm = params.get("_user_gross_margin")      # 同 _rule_vcr_vs_gross_margin：用用户原始值
    if gm is None:
        gm = params.get("gross_margin")
    if all(isinstance(x, (int, float)) for x in (vcr, gm)):
        implied_gm = 1.0 - float(vcr)      # 以变动成本率为准
        implied_vcr = 1.0 - float(gm)      # 以毛利率为准
        if abs(implied_gm - float(gm)) > 0.02:
            ops.append({
                "propose": "set",
                "label": t("field.op.set_gross_margin", value=f"{implied_gm * 100:.0f}"),
                "changes": {"gross_margin": round(implied_gm, 4)},
                "reason": t("field.op.set_gross_margin_reason", vcr=f"{float(vcr) * 100:.0f}",
                            value=f"{implied_gm * 100:.0f}"),
                "hypothesis": None,
            })
            ops.append({
                "propose": "set",
                "label": t("field.op.set_vcr", value=f"{implied_vcr * 100:.0f}"),
                "changes": {"variable_cost_ratio": round(implied_vcr, 4)},
                "reason": t("field.op.set_vcr_reason", gm=f"{float(gm) * 100:.0f}",
                            value=f"{implied_vcr * 100:.0f}"),
                "hypothesis": None,
            })
    return ops


# ── missing 根因追溯 ────────────────────────────────────────────────────

def _missing_root_causes(field: str, params: Dict[str, Any],
                          values: Dict[str, Optional[float]],
                          _seen: Optional[set] = None) -> List[str]:
    """追溯某派生字段缺算的根因（到底缺哪些用户输入）。"""
    if _seen is None:
        _seen = set()
    causes: List[str] = []
    spec = DERIVED_SPECS.get(field)
    if not spec:
        return causes
    # override 字段：用户没给、也没推导出 → 它自己就是根因（用户该给这个）
    if spec.get("kind") == "override" and params.get(field) is None and values.get(field) is None:
        causes.append(field_label(field))
    for d in spec["deps"]:
        if d in _seen:
            continue
        _seen.add(d)
        if d in DERIVED_SPECS:
            if values.get(d) is None:
                causes.extend(_missing_root_causes(d, params, values, _seen))
        else:
            if params.get(d) is None:
                causes.append(field_label(d))
    return causes


# ── 精确推算层清单（供 formatter / decision 消费）────────────────────────

def derived_values(params: Dict[str, Any], src: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """生成「精确推算层」清单。

    每个派生字段：{field, label, value, unit, unit_prefix, formula(带数字),
    status(ok/missing), missing(缺什么)}。公式与 derive() 同源，不再手写第二份。

    unit_prefix 是**前置**的货币符号（英文 "$"，中文空串）。它必须单独成字段而不是
    并进 unit：消费方（报表 / 前端）拿到的 value 仍是数字，才能各自决定千分位与精度；
    若把符号塞进 value 变成字符串，前端 formatNum 会直接失效。
    """
    src = src or {}
    out: List[Dict[str, Any]] = []
    values, meta = derive(params)

    for name in _DERIVED_ORDER:
        spec = DERIVED_SPECS[name]
        if spec.get("_hidden"):
            continue
        val = values.get(name)
        m = meta.get(name, {})
        # 展示口径：内部 0~1 的比例类字段（毛利率 / 变动成本率）在展示层 ×100 成百分数。
        # 既保持与旧版一致的视觉，也修掉「0.4 0~1」被 f"{v:,.0f}" 渲染成「0 0~1」的错。
        disp_val, disp_unit = val, _U(spec["unit_key"])
        if spec.get("display_percent") and isinstance(val, (int, float)):
            disp_val, disp_unit = round(val * 100, 1), "%"
        # 货币符号必须**前置**：英文写「$57,600/month」，不是「57,600 $/month」。
        # 判据是「渲染后的单位串是否以货币符号开头」——中文单位是「美元/月」，
        # 不以 $ 开头 → 走不进这个分支，输出与改动前**逐字相同**（中文侧零改动）。
        disp_prefix = ""
        if disp_unit.startswith("$"):
            disp_prefix, disp_unit = "$", disp_unit[1:]
        item: Dict[str, Any] = {
            "field": name, "label": _L(spec["label_key"]), "unit": disp_unit,
            "unit_prefix": disp_prefix,
            "status": "ok" if val is not None else "missing",
        }
        if val is not None:
            item["value"] = round(disp_val, 2) if isinstance(disp_val, float) else disp_val
            # 公式说明：仅「用户可直接给」且来源确为 [用户]（状态码 user）时写「用户直接给出」
            # 注意用 src 整体取码：引擎自产的 src 带 `_codes`，语义权威在那儿。
            if source_tags.code_of(src, name) == source_tags.USER and spec.get("user_direct_ok"):
                item["formula"] = t("field.src.user_direct")
            else:
                item["formula"] = m.get("formula", t("field.src.derived"))
        else:
            item["value"] = None
            item["formula"] = ""
            causes = _missing_root_causes(name, params, values)
            item["missing"] = t("llm.brief.sep").join(dict.fromkeys(causes)) if causes else t("field.msg.missing_deps")
        out.append(item)
    return out
