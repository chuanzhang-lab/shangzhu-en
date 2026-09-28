"""
工作流引擎 v2 — 创业者工作台的计算调度层

v2 改进：
- 行业模板从 7 个扩展到 12 个，含 benchmark 数据
- 拆分为 3 个专注工具：quick_scan / trend_projection / compare_scenarios
- 增加 12 个月趋势预测
- 状态标记含 benchmark 对比
- 参数支持更丰富（阶段、融资、团队角色等）

文案外置（M4）：所有展示文案经 `t("wf.*")` 取自 src/i18n/{zh,en}.yaml；
**来源标注拆成两条通道** —— 展示串（人读）照旧按字段存，
状态码（机器读，语言无关）存 `src["_codes"]`，见 src/source_tags.py。
本模块内**禁止**再用 `startswith("[用户]")` 之类的中文标记匹配做语义判断，
一律走 `source_tags.code_of()`：否则英文环境下会静默走错分支。
"""

import json
import math
import os
from functools import lru_cache
from typing import Optional
import yaml
from langchain.tools import tool

from i18n import t
import source_tags as st
from source_tags import CANDIDATE, CONFLICT, DERIVED, MISSING, USER

from tools.financial_calculator import (
    _calc_breakeven,
    _calc_runway,
    _calc_sensitivity,
    _calc_cashflow_schedule,
)
from tools.pitfall_detector import _do_full_scan
from router.param_extractor import extract_params

# F1：时间口径必须与 field_model 同源（月营收 30 天/月 ⇒ 保本客流也按 360 天/年）。
# 分开写死就会出现「营收按 360 天、保本按 365 天」的双重口径。
#
# 注意：**不要**给这个 import 加 try/except 兜底。field_model 是同项目的
# 必选依赖，导入失败就是真错误；静默 fallback 成 30 会让「改了口径常量却
# 只有一半公式生效」这种最难查的故障再次发生（本项目已因此出过 F1）。
from field_model import DAYS_PER_MONTH, DAYS_PER_YEAR


# ─── 输入解析（兼容 JSON dict 和自然语言字符串）────────────────────────────

def _parse_tool_input(params_json) -> tuple[dict, str]:
    """解析工具输入。JSON dict 直接用；自然语言字符串用 extract_params 解析。

    返回 (参数字典, 原始文本)。原始文本供 _fill_params 做混合业态检测用。
    """
    raw_text = ""
    raw = {}
    try:
        if isinstance(params_json, str):
            parsed = json.loads(params_json)
            if isinstance(parsed, str):
                raw_text = parsed
            else:
                raw = parsed
        elif isinstance(params_json, dict):
            raw = params_json
    except (json.JSONDecodeError, TypeError):
        raw_text = str(params_json) if not isinstance(params_json, dict) else ""
        raw = {}
    if raw_text and not raw:
        raw = extract_params(raw_text)
    return raw, raw_text


# ─── 行业模板（12 个行业 + benchmark）──────────────────────────────────────

# 行业默认参数、基准数据、别名从 config/industry_templates.yaml 加载（运营可
# 直接改 YAML，无需动代码）。加载结果带缓存；改 YAML 后需重启进程生效。
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "industry_templates.yaml",
)


@lru_cache(maxsize=1)
def _load_templates() -> dict:
    """从 YAML 读取行业模板配置，返回 {industry_templates, fallback_template, industry_aliases}。"""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# 通用季节系数（无行业模板时的 fallback）
_DEFAULT_SEASON_MAP = {1: 0.85, 2: 0.95, 3: 1.0, 4: 1.05, 5: 1.05, 6: 1.1,
                       7: 1.1, 8: 1.05, 9: 1.0, 10: 1.0, 11: 0.95, 12: 0.9}


def _get_industry_seasonal_profile(industry_name: str) -> dict:
    """获取行业季节系数：优先读行业模板的 seasonal_profile，fallback 通用 map。

    Returns:
        {1: 系数, 2: 系数, ..., 12: 系数}
    """
    if not industry_name:
        return dict(_DEFAULT_SEASON_MAP)

    templates = _load_templates()
    industry_templates = templates.get("industry_templates", {})
    aliases = templates.get("industry_aliases", {})

    # 解析行业名（含别名）
    resolved = aliases.get(industry_name, industry_name)
    tpl = industry_templates.get(resolved)

    if tpl and "seasonal_profile" in tpl:
        profile = tpl["seasonal_profile"]
        if isinstance(profile, list) and len(profile) == 12:
            return {i + 1: float(v) for i, v in enumerate(profile)}

    return dict(_DEFAULT_SEASON_MAP)


def _get_templates() -> dict:
    return _load_templates()


def _flatten_template(entry: dict) -> dict:
    """把 YAML 的 {hypotheses, benchmark} 展平为旧式扁平模板 {**hypotheses, benchmark}。

    兼容旧代码（引擎各处用 tpl["variable_cost_ratio"] 等），同时 expose
    tpl["hypotheses"] 与 tpl["benchmark"] 两个区。词汇表见 D4。
    """
    hyp = dict(entry.get("hypotheses") or {})
    bench = dict(entry.get("benchmark") or {})
    tpl = dict(hyp)
    tpl["hypotheses"] = hyp
    tpl["benchmark"] = bench
    return tpl


def _get_industry_templates() -> dict:
    raw = _get_templates().get("industry_templates", {})
    return {k: _flatten_template(v) for k, v in raw.items()}


def _get_fallback_template() -> dict:
    return _flatten_template(_get_templates().get("fallback_template", {}))


def _get_industry_aliases() -> dict:
    return _get_templates().get("industry_aliases", {})


def _get_hypothesis_fields() -> list:
    """输入伪造型默认字段清单：用户没给时不得静默进计算，作候选待确认。"""
    return _get_templates().get("hypothesis_fields", [])


# 兼容旧引用：模块顶层仍暴露同名常量，值是惰性加载（首次访问时读 YAML）。
INDUSTRY_TEMPLATES: dict = _get_industry_templates()
FALLBACK_TEMPLATE: dict = _get_fallback_template()
INDUSTRY_ALIASES: dict = _get_industry_aliases()


def _resolve_industry(industry: str) -> dict:
    """解析行业名，返回模板字典"""
    key = _get_industry_aliases().get(industry.lower(), industry)
    return _get_industry_templates().get(key, _get_fallback_template())


# ─── 混合业态检测 ──────────────────────────────────────────────────────────


# ⚠️ 关键词是**输入层匹配数据**（与 field_model.aliases 同类），不是文案：
# 改了会直接破坏混合业态识别，因此不做外置，由 i18n 护栏按白名单放行。
MIXED_INDUSTRY_PAIRS = [
    # (关键词集合, 说明文案键)
    ({"咖啡", "宠物"}, "coffee_pet"),
    ({"养老", "医疗"}, "elderly_medical"),
    ({"教育", "SaaS"}, "education_saas"),
    ({"电商", "内容"}, "ecommerce_content"),
    ({"餐饮", "零售"}, "food_retail"),
    ({"制造", "电商"}, "manufacturing_ecommerce"),
]


def _detect_mixed_industry(user_text: str, matched_industry: str) -> Optional[str]:
    """检测混合业态，返回提示信息或 None"""
    text_lower = user_text.lower()
    for kw_set, _note_key in MIXED_INDUSTRY_PAIRS:
        if all(k in text_lower for k in kw_set):
            return t("wf.mixed.prefix", industry=matched_industry)
    return None


# ─── 参数填充 v3（来源标注 + 跳过模板 + 混合检测）───────────────────────


def _fill_params(raw_params: dict, _skip_guard: bool = False) -> tuple[dict, dict, Optional[str]]:
    """
    从原始参数填充默认值，返回 (完整参数字典, 来源标注, 混合业态提示)。

    v3 改进：
    - 每个参数标注来源：[用户] / [候选] / [推算]
    - 用户给够核心参数（月收入+月支出+总投资）→ 跳过行业模板
    - 检测混合业态并给出提示
    """
    # ── 守门层（引擎入口最后一道关）：清洗归一化，防止荒谬值进入计算 ──
    if not _skip_guard:
        from param_guard import guard_extracted
        raw_params, _engine_guard = guard_extracted(
            raw_params, industry=raw_params.get("industry")
        )
    else:
        _engine_guard = {}

    # 收集用户原始文本用于混合检测
    user_text = raw_params.get("_raw_text", "")
    industry = raw_params.get("industry", "")

    # 判断是否跳过模板：用户给够核心参数
    has_revenue = raw_params.get("monthly_revenue", 0) or raw_params.get("initial_monthly_revenue", 0)
    has_expense = raw_params.get("monthly_expense", 0)
    has_investment = raw_params.get("total_investment", 0)
    has_traffic_price = raw_params.get("daily_traffic", 0) and raw_params.get("price_per_unit", 0)
    has_vc_ratio = raw_params.get("variable_cost_rate") or raw_params.get("variable_cost_ratio")

    skip_template = bool(has_investment and (has_revenue or has_traffic_price) and has_expense)

    # 解析行业模板
    if skip_template:
        tpl = _get_fallback_template().copy()
        tpl["_mode"] = t("wf.mode.custom")
        effective_industry = industry or "自定义"
    else:
        effective_industry = industry or "其他"
        tpl = _resolve_industry(effective_industry)
        tpl["_mode"] = t("wf.mode.industry", industry=effective_industry)

    # 混合业态检测：检查原始文本或用户填写的行业名
    check_text = user_text or raw_params.get("industry", "")
    mixed_warning = _detect_mixed_industry(check_text, effective_industry) if check_text else None

    # 来源追踪
    # 两条通道：src[字段]=展示文案（人读）；src["_codes"][字段]=状态码（机器读）。
    src = {}

    def _set(key, value, code, copy_key, **kwargs):
        """设置参数值并记录来源（状态码 + 展示文案）。"""
        st.set_tag(src, key, code, copy_key, **kwargs)
        return value

    p = {}

    # ── 财务参数（无默认：没给即 [缺失]，不填 0）──
    p["total_investment"] = _set(
        "total_investment", raw_params.get("total_investment"),
        USER if raw_params.get("total_investment") is not None else MISSING,
        "user.bare" if raw_params.get("total_investment") is not None else "missing.not_provided",
    )

    if raw_params.get("monthly_rent") is None:
        _rent_code, _rent_key = MISSING, "missing.not_provided"
    elif raw_params.get("_rent_from_annual"):
        _rent_code, _rent_key = DERIVED, "derived.rent_from_annual"
    else:
        _rent_code, _rent_key = USER, "user.bare"
    p["monthly_rent"] = _set(
        "monthly_rent", raw_params.get("monthly_rent"), _rent_code, _rent_key
    )

    p["daily_traffic"] = _set(
        "daily_traffic", raw_params.get("daily_traffic"),
        USER if raw_params.get("daily_traffic") is not None else MISSING,
        "user.bare" if raw_params.get("daily_traffic") is not None else "missing.not_provided",
    )

    p["price_per_unit"] = _set(
        "price_per_unit", raw_params.get("price_per_unit"),
        USER if raw_params.get("price_per_unit") is not None else MISSING,
        "user.bare" if raw_params.get("price_per_unit") is not None else "missing.not_provided",
    )

    # ── 变动成本率：公式唯一出处 = field_model.derive()（S4 收敛）──
    # 本处只做两件事：① 原始输入解析（单位归一）；② 来源标注。
    # 旧实现手写了**第二份**四路推导（user > unit_var÷price > 1−gm > None），
    # 与 field_model.DERIVED_SPECS["variable_cost_ratio"] 的公式重复、必须人工同步，
    # 是「公式唯一出处」契约名存实亡的根因。
    # variable_cost_rate 是通用字段，可能误抓相邻客流数字（如「日售50杯变动成本率55%」）。
    user_vc = raw_params.get("variable_cost_ratio")
    if user_vc is None:
        user_vc = raw_params.get("variable_cost_rate")
    user_gm = raw_params.get("gross_margin")
    user_unit_var = raw_params.get("unit_variable_cost")

    # D2（本轮）：保留用户**原始**毛利率，供一致性规则比对。
    # 原因：用户同时给 vcr 与 gm 时，derive() 会把 gm 覆盖成 1−vcr，
    # 等 consistency_issues 运行时矛盾已被销毁、规则永远看不到 —— 用户的 50% 就此静默消失。
    # 与 _revenue_series 同例：以 `_` 前缀标记的内部键，不参与业务计算。
    if user_gm is not None:
        p["_user_gross_margin"] = float(user_gm)

    # D8：用户口中的客流单位（「每天卖100碗」→「碗」）。行业模板只到「餐饮」
    # 粒度（默认「杯」），面馆会看到「保本客流 22 杯/天」。用户口径优先。
    # 同 `_` 前缀内部键，不参与业务计算，只供呈现层取用。
    if raw_params.get("_traffic_unit"):
        p["_traffic_unit"] = raw_params["_traffic_unit"]

    if user_vc is not None:
        vc = float(user_vc)
        # 输入解析：兼容 40(%) 与 0.4(比例) 两种写法（单位归一，不是公式）
        p["variable_cost_ratio"] = vc / 100 if vc > 1 else vc
        st.set_tag(src, "variable_cost_ratio", USER, "user.bare")
    else:
        # D2：取消「输入伪造型默认」。没给/推不出 → 缺失，不静默用行业模板填进
        # 计算图（会撑起假硬利润）；作假设候选待确认。
        p["variable_cost_ratio"] = None
        st.set_tag(src, "variable_cost_ratio", MISSING, "missing.vcr_unknown")
        # 把模型的推导依赖放进 p，交给 derive() 统一求值。
        # 用户已直给 vcr 时不放，沿用旧的优先级语义（显式值不被推导依赖干扰）。
        if user_unit_var is not None:
            p["unit_variable_cost"] = float(user_unit_var)
        if user_gm is not None:
            p["gross_margin"] = float(user_gm)

    # ── 人员参数（无默认：人数/薪资没给即 [缺失]，不填 0）──
    user_staff = raw_params.get("employee_count", raw_params.get("staff_count"))
    if user_staff is not None:
        p["employee_count"] = user_staff
        st.set_tag(src, "employee_count", USER, "user.bare")
    else:
        p["employee_count"] = None
        st.set_tag(src, "employee_count", MISSING, "missing.not_provided")

    user_salary = raw_params.get("avg_salary")
    if user_salary is not None:
        p["avg_salary"] = user_salary
        st.set_tag(src, "avg_salary", USER, "user.bare")
    else:
        p["avg_salary"] = None
        st.set_tag(src, "avg_salary", MISSING, "missing.not_provided")

    # ── 时间参数（无默认：没给即 [缺失]，不填 验证期/12）──
    p["stage"] = _set(
        "stage", raw_params.get("stage"),
        USER if raw_params.get("stage") is not None else MISSING,
        "user.bare" if raw_params.get("stage") is not None else "missing.not_provided",
    )

    p["analysis_months"] = _set(
        "analysis_months", raw_params.get("analysis_months"),
        USER if raw_params.get("analysis_months") is not None else MISSING,
        "user.bare" if raw_params.get("analysis_months") is not None else "missing.not_provided",
    )

    # ── 增长参数（无默认：没给即 [缺失]，不填 5%/1.0）──
    p["monthly_growth_rate"] = _set(
        "monthly_growth_rate", raw_params.get("monthly_growth_rate"),
        USER if raw_params.get("monthly_growth_rate") is not None else MISSING,
        "user.bare" if raw_params.get("monthly_growth_rate") is not None else "missing.not_provided",
    )

    p["seasonal_factor"] = _set(
        "seasonal_factor", raw_params.get("seasonal_factor"),
        USER if raw_params.get("seasonal_factor") is not None else MISSING,
        "user.bare" if raw_params.get("seasonal_factor") is not None else "missing.not_provided",
    )

    # ── 团队参数（无默认：没给即 [缺失]，不填 1 人/False）──
    p["founder_count"] = _set(
        "founder_count", raw_params.get("founder_count"),
        USER if raw_params.get("founder_count") is not None else MISSING,
        "user.bare" if raw_params.get("founder_count") is not None else "missing.not_provided",
    )

    p["has_tech_cofounder"] = _set(
        "has_tech_cofounder", raw_params.get("has_tech_cofounder"),
        USER if raw_params.get("has_tech_cofounder") is not None else MISSING,
        "user.bare" if raw_params.get("has_tech_cofounder") is not None else "missing.not_provided",
    )

    p["has_market_cofounder"] = _set(
        "has_market_cofounder", raw_params.get("has_market_cofounder"),
        USER if raw_params.get("has_market_cofounder") is not None else MISSING,
        "user.bare" if raw_params.get("has_market_cofounder") is not None else "missing.not_provided",
    )

    p["has_ops_cofounder"] = _set(
        "has_ops_cofounder", raw_params.get("has_ops_cofounder"),
        USER if raw_params.get("has_ops_cofounder") is not None else MISSING,
        "user.bare" if raw_params.get("has_ops_cofounder") is not None else "missing.not_provided",
    )

    # ── 融资参数（无默认）──
    p["has_financing"] = raw_params.get("has_financing")
    p["funding_round"] = raw_params.get("funding_round")
    p["funding_amount"] = raw_params.get("funding_amount")

    # ── 市场参数（无默认）──
    p["tam_description"] = raw_params.get("tam_description")
    p["competitor_count"] = raw_params.get("competitor_count")
    p["city"] = raw_params.get("city")
    p["location_type"] = raw_params.get("location_type")

    # ── 派生计算（公式唯一出处：field_model.derive()）──
    # _fill_params 负责两件事：① 输入解析（上面已完成）；② 业务规则
    #   （劳动合理性门禁、固定成本组件求和+矛盾、利润反推）。
    # 派生字段的**数值**由 field_model.derive() 统一算出（公式唯一出处）。
    # src 标注（[用户]/[推算]/[缺失]）仍由 _fill_params 生成——它是「来源语义」，
    # 不是公式，模型层不管。

    # 月营收：优先用户直接给的数值（支持数组：多期收入序列）
    revenue_override = raw_params.get("monthly_revenue") or raw_params.get("initial_monthly_revenue")
    _daily_src = raw_params.get("_daily_revenue_src")  # F3 边界：日营业额换算
    if revenue_override:
        if isinstance(revenue_override, (list, tuple)) and len(revenue_override) > 0:
            # 多期收入序列：保留为数组，derive() 用第一个值做单期推算
            p["monthly_revenue"] = revenue_override
            p["_revenue_series"] = list(revenue_override)  # 备份原始序列
            st.set_tag(src, "monthly_revenue", USER, "user.revenue_series",
                       n=len(revenue_override))
        else:
            p["monthly_revenue"] = revenue_override
            # 日营业额换算的来源标注：告诉用户这是从日值得出的月值
            if _daily_src:
                st.set_tag(src, "monthly_revenue", DERIVED, "derived.daily_revenue",
                           src=_daily_src)
            else:
                st.set_tag(src, "monthly_revenue", USER, "user.bare")
    # 否则让 derive() 从 traffic×price×30 算（src 在下面统一标注）

    # 固定成本组件字段（水电/包装/提成/其他固定）——用户给的输入
    for fld in ("utilities", "packaging", "commission", "other_fixed"):
        raw = raw_params.get(fld)
        if raw is not None:
            p[fld] = _set(fld, raw, USER, "user.bare")

    # ── 业务规则：劳动合理性门禁（>200 人 → 标矛盾，不参与计算）──
    labor_present = all(
        k in raw_params for k in ("employee_count", "avg_salary")
    ) or ("staff_count" in raw_params and "avg_salary" in raw_params)
    labor_burden = raw_params.get("labor_burden", 0.0)
    staff_n = float(p.get("employee_count") or 0)
    labor_blocked = False
    if labor_present and staff_n > 200:
        st.set_tag(src, "employee_count", CONFLICT, "conflict.staff_out_of_range",
                   n=f"{staff_n:g}")
        labor_present = False
        labor_blocked = True

    # 把 labor_burden 也放进 p，供 derive() 用
    if labor_burden:
        p["labor_burden"] = labor_burden
    p["labor_burden_rate"] = labor_burden
    st.set_tag(
        src, "labor_burden_rate",
        USER if "labor_burden" in raw_params else MISSING,
        "user.bare" if "labor_burden" in raw_params else "missing.labor_burden_zero",
    )

    # ── 业务规则：固定成本组件求和 + 最弱环标注 + 显式总数矛盾 ──
    # 先让 derive() 算出 monthly_labor_cash / monthly_labor（纯公式），
    # 但这里用业务规则覆盖（劳动门禁 / 组件求和 / 矛盾标注）。
    from field_model import derive as _model_derive

    # 提取 B 类字段用户覆盖（从 raw_params 中分离）
    from field_model import OVERRIDABLE_FIELDS
    _user_overrides = {k: v for k, v in raw_params.items()
                       if k in OVERRIDABLE_FIELDS and v is not None}

    # 先调 derive() 算出所有派生字段（公式唯一出处），传入 user_overrides
    _derived_values, _derived_meta = _model_derive(p, user_overrides=_user_overrides)

    # 把 derive() 算出的值合并回 p（确保所有派生字段存在；有值时优先保留非 None）
    for dk, dv in _derived_values.items():
        if dk not in p or p.get(dk) is None:
            p[dk] = dv

    # ── 月营收 src 标注 ──
    if "monthly_revenue" not in src:
        if p.get("monthly_revenue") is not None:
            st.set_tag(src, "monthly_revenue", DERIVED, "derived.revenue_from_traffic",
                       days=DAYS_PER_MONTH)
        else:
            st.set_tag(src, "monthly_revenue", MISSING, "missing.revenue_none")

    # ── 月人工 src 标注 ──
    if labor_blocked:
        st.set_tag(src, "monthly_labor_cash", MISSING, "missing.labor_abnormal")
        st.set_tag(src, "monthly_labor", MISSING, "missing.labor_abnormal")
    elif labor_present:
        labor_cash = staff_n * float(p.get("avg_salary") or 0)
        monthly_labor = labor_cash * (1 + labor_burden)
        # 语义判断走状态码，不碰展示文案（英文下 startswith("[用户]") 会静默失配）
        count_user = st.code_of(src, "employee_count") == USER
        sal_user = st.code_of(src, "avg_salary") == USER
        if count_user and sal_user:
            labor_code = USER
        elif count_user or sal_user:
            labor_code = DERIVED
        else:
            labor_code = CANDIDATE
        if labor_burden > 0 and labor_code == USER:
            labor_code = DERIVED
        p["monthly_labor_cash"] = labor_cash
        p["monthly_labor"] = monthly_labor
        _salary_txt = f"{float(p.get('avg_salary') or 0):g}"
        st.set_tag(src, "monthly_labor_cash", labor_code, "labor_cash",
                   mark=st.mark(labor_code), n=f"{staff_n:g}", salary=_salary_txt)
        st.set_tag(src, "monthly_labor", labor_code, "labor_burdened",
                   mark=st.mark(labor_code), cash=f"{labor_cash:g}",
                   burden=f"{labor_burden:.0%}")
    else:
        st.set_tag(src, "monthly_labor_cash", MISSING, "missing.not_provided")
        st.set_tag(src, "monthly_labor", MISSING, "missing.not_provided")

    # ── 固定成本业务规则：组件求和 + 最弱环 + 显式总数矛盾 + 利润反推 ──
    explicit_total = raw_params.get("monthly_expense", 0)
    # (组件名文案键, 值, 状态码)；值为 None 或状态码为空 → 该组件不参与求和
    component_specs = [
        ("rent", p.get("monthly_rent") if st.code_of(src, "monthly_rent") == USER else None,
         USER if st.code_of(src, "monthly_rent") == USER else ""),
        ("labor", p.get("monthly_labor") if labor_present else None,
         st.code_of(src, "monthly_labor") if labor_present else ""),
        ("utilities", p.get("utilities"), USER if p.get("utilities") is not None else ""),
        ("packaging", p.get("packaging"), USER if p.get("packaging") is not None else ""),
        ("commission", p.get("commission"), USER if p.get("commission") is not None else ""),
        ("other", p.get("other_fixed"), USER if p.get("other_fixed") is not None else ""),
    ]
    present_specs = [(k, v, c) for k, v, c in component_specs if v is not None and c]
    if present_specs:
        comp_sum = sum(v for _, v, _ in present_specs)
        comp_src = " + ".join(
            f"{t(f'src.comp.{k}')}{st.mark(c)}" for k, _, c in present_specs
        )
        codes = {c for _, _, c in present_specs}
        if CANDIDATE in codes or DERIVED in codes:
            total_code = DERIVED
        else:
            total_code = USER
        if explicit_total and explicit_total > 0:
            if abs(comp_sum - explicit_total) <= 1:
                p["monthly_fixed_cost"] = comp_sum
                st.set_tag(src, "monthly_fixed_cost", total_code, "fixed_cost_sum",
                           mark=st.mark(total_code), comp=comp_src)
            else:
                p["monthly_fixed_cost"] = comp_sum
                st.set_tag(src, "monthly_fixed_cost", total_code, "fixed_cost_sum_conflict",
                           mark=st.mark(total_code), comp=comp_src,
                           total=f"{explicit_total:g}")
        else:
            p["monthly_fixed_cost"] = comp_sum
            st.set_tag(src, "monthly_fixed_cost", total_code, "fixed_cost_sum",
                       mark=st.mark(total_code), comp=comp_src)
    elif explicit_total and explicit_total > 0:
        p["monthly_fixed_cost"] = explicit_total
        st.set_tag(src, "monthly_fixed_cost", USER, "user.explicit_total")
    else:
        user_profit = raw_params.get("monthly_profit", 0)
        if user_profit and p.get("monthly_revenue") and p.get("variable_cost_ratio") is not None:
            p["monthly_fixed_cost"] = (
                p["monthly_revenue"] - user_profit
                - p["monthly_revenue"] * p["variable_cost_ratio"]
            )
            st.set_tag(src, "monthly_fixed_cost", DERIVED, "derived.from_profit")
        else:
            p["monthly_fixed_cost"] = None
            st.set_tag(src, "monthly_fixed_cost", MISSING, "missing.not_invented")

    # ── 可用现金 src 标注（值已由 derive() 算好，只补标注）──
    if p.get("total_investment"):
        eq = raw_params.get("equipment_ratio")
        if eq is not None:
            p["available_cash"] = p["total_investment"] * (1 - float(eq))
            st.set_tag(src, "available_cash", DERIVED, "derived.cash_from_invest")
        else:
            p["available_cash"] = p["total_investment"]
            st.set_tag(src, "available_cash", DERIVED, "derived.cash_no_equip_ratio")
    else:
        p["available_cash"] = None
        st.set_tag(src, "available_cash", MISSING, "missing.investment_none")

    # ── 剩余派生字段 src 标注（值由 derive() 算好，只补来源标签）──
    # (状态码, 有值时的文案键, 缺失时的文案键 or None)
    _src_map = {
        "monthly_variable_cost": (DERIVED, "derived.variable_cost", "missing.variable_cost"),
        "monthly_profit": (DERIVED, "derived.profit", None),
        "variable_cost_per_unit": (DERIVED, "derived.unit_var_cost", "missing.unit_var_cost"),
        "annual_fixed_cost": (DERIVED, "derived.annual_fixed", "missing.annual_fixed"),
        "gross_margin": (DERIVED, "derived.gross_margin", None),
    }
    # 月利润特殊：用户可直给
    user_monthly_profit = raw_params.get("monthly_profit")
    if user_monthly_profit:
        p["monthly_profit"] = user_monthly_profit
        st.set_tag(src, "monthly_profit", USER, "user.bare")
    elif p.get("monthly_profit") is None:
        if p.get("monthly_fixed_cost") is None and p.get("monthly_revenue") is not None and p.get("variable_cost_ratio") is not None:
            st.set_tag(src, "monthly_profit", MISSING, "missing.profit_no_fixed")
        else:
            st.set_tag(src, "monthly_profit", MISSING, "missing.profit_incomplete")
    else:
        st.set_tag(src, "monthly_profit", DERIVED, "derived.profit")

    for fld, (code, ok_key, miss_key) in _src_map.items():
        if fld == "monthly_profit":
            continue  # 上面已处理
        if fld == "gross_margin" and user_vc is None and user_gm is not None:
            # S4：用户直给毛利率时它是输入项，不是「1−变动成本率」的推算结果
            st.set_tag(src, fld, USER, "user.bare")
        elif p.get(fld) is not None:
            st.set_tag(src, fld, code, ok_key)
        elif miss_key:
            st.set_tag(src, fld, MISSING, miss_key)
        else:
            st.set_tag(src, fld, MISSING, "missing.not_provided")

    # ── 变动成本率 src 标注（值已由 derive() 按公式唯一出处算出）──
    # 敏感度区间逻辑（见 _build_scenarios）按状态码 DERIVED 识别，
    # 不再依赖 "[推算]" 文案前缀 —— 否则英文下会静默改变 what-if 区间行为。
    if st.code_of(src, "variable_cost_ratio") != USER:
        if p.get("variable_cost_ratio") is None:
            st.set_tag(src, "variable_cost_ratio", MISSING, "missing.vcr_unknown")
        elif user_unit_var is not None and p.get("price_per_unit"):
            st.set_tag(src, "variable_cost_ratio", DERIVED, "derived.vcr_from_unit_cost")
        elif user_gm is not None:
            st.set_tag(src, "variable_cost_ratio", DERIVED, "derived.vcr_from_gm")
        else:
            st.set_tag(src, "variable_cost_ratio", DERIVED, "derived.vcr_formula")

    p["benchmark"] = tpl["benchmark"]
    # 行业典型成本结构占比（租金/包装/营销/人工/其他），用于差异化参考
    p["cost_structure"] = tpl.get("cost_structure", {})
    p["industry_name"] = effective_industry
    p["_template_mode"] = tpl.get("_mode", "")
    p["_skip_template"] = skip_template

    # 守门信息挂到来源 dict，供输出层/上层读取
    if _engine_guard.get("issues") or _engine_guard.get("needs_confirmation"):
        src["_guard"] = _engine_guard
    return p, src, mixed_warning


# ─── benchmark 真实对比（把模板数值区间变成可计算的超界告警）───────────────


def _benchmark_check(params: dict, benchmark: dict) -> list:
    """将用户实际指标与行业 benchmark 数值区间对比，返回告警列表（空=符合行业常态）。

    对比维度：净利率区间（benchmark 的 profit_margin 指【净利润率】，须用
    月利润/月营收 计算，不能用毛利率）、回本月数区间、日均客流区间（仅适用行业）。
    缺失数据则跳过对应维度（不误报）。
    """
    warnings = []
    if not isinstance(benchmark, dict):
        return warnings

    # 1) 净利率对比（用 月利润 / 月营收，避免把毛利率误当净利率导致几乎必报"高于行业"）
    rev = params.get("monthly_revenue")
    prof = params.get("monthly_profit")
    lo, hi = benchmark.get("profit_margin_min"), benchmark.get("profit_margin_max")
    if rev and prof is not None and lo is not None and hi is not None and rev > 0:
        nm = prof / rev
        if nm < lo:
            warnings.append(t("wf.bench.margin_low", nm=f"{nm*100:.0f}",
                              lo=f"{lo*100:.0f}", hi=f"{hi*100:.0f}"))
        elif nm > hi:
            warnings.append(t("wf.bench.margin_high", nm=f"{nm*100:.0f}",
                              lo=f"{lo*100:.0f}", hi=f"{hi*100:.0f}"))

    # 2) 回本月数对比（年化固定成本 ÷ 月利润，若盈利）
    months = None
    mf = params.get("monthly_profit")
    afc = params.get("annual_fixed_cost")
    if mf and mf > 0 and afc:
        months = afc / mf
    bmin, bmax = benchmark.get("breakeven_months_min"), benchmark.get("breakeven_months_max")
    if months is not None and bmax is not None:
        if months > bmax:
            hint = (t("wf.bench.breakeven_hint", bmin=bmin, bmax=bmax)
                    if bmin is not None else "")
            warnings.append(t("wf.bench.payback_exceed", months=f"{months:.0f}", hint=hint))

    # 3) 日均客流对比（仅 traffic_min/max 适用的行业）
    dt = params.get("daily_traffic")
    tmin, tmax = benchmark.get("traffic_min"), benchmark.get("traffic_max")
    if dt and tmin is not None and tmax is not None:
        if dt < tmin:
            warnings.append(t("wf.bench.traffic_low", dt=f"{dt:.0f}", tmin=tmin, tmax=tmax))
        elif dt > tmax:
            warnings.append(t("wf.bench.traffic_high", dt=f"{dt:.0f}", tmin=tmin, tmax=tmax))

    return warnings


# ─── 参数充分性门禁 + 置信档位（Phase 0：①②）─────────────────────────────

# 纳入“假设清单”的业务相关字段（避免展示 stage/分析月数等噪音）
ASSUMPTION_FIELDS = [
    "monthly_rent", "daily_traffic", "price_per_unit", "variable_cost_ratio",
    "employee_count", "avg_salary", "total_investment", "monthly_growth_rate",
    "stage", "founder_count",
]


def _derive_conf(param_sources: dict) -> dict:
    """从来源**状态码**推导置信档位 tier：user / default / guess / missing。

    走状态码而非展示文案：文案会随 locale 变，状态码不变。
    """
    # 与旧实现逐档对齐：[矛盾] 旧行为是「非 user/缺失/候选」→ guess，此处同。
    _TIER_BY_CODE = {USER: "user", MISSING: "missing", CANDIDATE: "default"}
    conf = {}
    for k, s in param_sources.items():
        if not isinstance(s, str):  # 跳过 _guard / _codes 等结构化元数据
            continue
        conf[k] = _TIER_BY_CODE.get(st.code_of(param_sources, k), "guess")
    return conf


def _derive_available_actions(params: dict, scenarios: dict) -> list:
    """根据当前参数和情景，推导可用的下一步动作列表（供 LLM 主持人推荐）。"""
    actions = ["quick_scan"]  # 始终可重新扫描

    # 有利润数据 → 可对比、可趋势
    if params.get("monthly_profit") is not None:
        actions.append("compare_scenarios")
        actions.append("trend_projection")

    # 有 benchmark → 可看行业基准
    if params.get("benchmark"):
        actions.append("benchmark")

    # 有情景分析 → 可看敏感性
    if scenarios and scenarios.get("has_uncertainty"):
        actions.append("sensitivity")

    # 总投资已知 → 可看跑道
    if params.get("available_cash") is not None:
        actions.append("runway")

    return actions


def _check_sufficiency(params: dict, param_sources: dict) -> dict:
    """充分性门禁（决策B）。

    - 硬门槛：月营收必须可得（用户直接给 或 客流×单价算出）> 0，否则拦，不输出仪表盘。
    - 软缺口：总投资 / 人工 缺失则列入 gaps（提示但不致命，按 0 计并标注）。

    gaps 是**展示文案**（随 locale 变），因此额外返回 gap_codes（语言无关的
    缺口码）供门禁判定用 —— 门禁不能靠中文前缀匹配（英文下会静默失效）。
    """
    gap_codes = []
    rev = params.get("monthly_revenue")
    # 收入序列：取第一个值判断充分性
    if isinstance(rev, (list, tuple)):
        rev = rev[0] if rev else None
    if not (rev and rev > 0):
        gap_codes.append("revenue")
    if st.code_of(param_sources, "total_investment") == MISSING:
        gap_codes.append("investment")
    if st.code_of(param_sources, "employee_count") == MISSING:
        gap_codes.append("labor")
    # D2：变动成本率缺失 → 无法算利润/平衡点 → 列入缺口，可算的部分照算
    if st.code_of(param_sources, "variable_cost_ratio") == MISSING:
        gap_codes.append("variable_cost")
    gaps = [t(f"wf.gap.{c}") for c in gap_codes]

    key_fields = ["monthly_revenue", "total_investment", "employee_count",
                  "avg_salary", "variable_cost_ratio", "monthly_rent"]
    known = sum(1 for f in key_fields
                if st.code_of(param_sources, f) in (USER, CANDIDATE))
    coverage = round(known / len(key_fields), 2)

    # 硬门槛：仅月营收缺失拦（无法算任何结论）；vc 缺失只列缺口、能算的照算。
    # 利润/平衡点在 vc 缺失时输出 None + 缺口标注，交由输出层呈现「还不能定」。
    ok = "revenue" not in gap_codes
    return {"ok": ok, "gaps": gaps, "gap_codes": gap_codes, "coverage": coverage}


# 状态码 → 假设类型文案键（缺失/候选/推算）
_KIND_KEY_BY_CODE = {MISSING: "missing", CANDIDATE: "candidate", DERIVED: "derived"}


def _build_assumptions(params: dict, param_sources: dict) -> list:
    """汇总非[用户]来源的参数，形成“当前假设清单”（决策A③ 前置可见）。

    `kind` 是展示文案（测试与前端按它取值），`kind_code` 是语言无关的状态码，
    供决策层做语义判断。
    """
    out = []
    for k in ASSUMPTION_FIELDS:
        s = param_sources.get(k, "")
        if not isinstance(s, str) or not s:
            continue
        code = st.code_of(param_sources, k)
        if code == USER:
            continue
        val = params.get(k)
        kind_key = _KIND_KEY_BY_CODE.get(code, "derived")
        out.append({"field": k, "value": val, "source": s,
                    "kind": t(f"wf.kind.{kind_key}"), "kind_code": code})
    return out


def _build_framework(params: dict, param_sources: dict) -> dict:
    """门禁拦下时，给出收入/成本模型的已知框架（只算有真实输入的部分）。"""
    rev = params.get("monthly_revenue", 0) or 0
    if isinstance(rev, (list, tuple)):
        rev = rev[0] if rev else 0
    rent = params.get("monthly_rent", 0) or 0
    labor = (params.get("employee_count", 0) or 0) * (params.get("avg_salary", 0) or 0)
    return {
        "revenue_model": (
            t("wf.framework.revenue_known", rev=f"{rev:,.0f}") if rev > 0
            else t("wf.framework.revenue_unknown")
        ),
        "cost_model": (
            t("wf.framework.cost", rent=f"{rent:,.0f}")
            + (t("wf.framework.labor_part", labor=f"{labor:,.0f}") if labor
               else t("wf.framework.labor_none"))
        ),
        "cash_model": (
            t("wf.framework.cash_unknown") if params.get("available_cash") is None
            else t("wf.framework.cash_known", cash=f"{params['available_cash']:,.0f}")
        ),
    }


# ─── 12 个月趋势预测 ──────────────────────────────────────────────────────


def _profit_readiness(params: dict) -> tuple[bool, str]:
    """统一判定「月利润是否可算」，供 trend / compare 共用。

    与 quick_scan 的「profit 为 None → 状态=未知」同一防御思路：算不出利润就
    降级提示，绝不硬算假趋势 / 不参与 diff 减法。返回 (ready, 缺口说明)。
    """
    if params.get("variable_cost_ratio") is None:
        return False, t("wf.readiness.no_vcr")
    if params.get("monthly_revenue") is None:
        return False, t("wf.readiness.no_revenue")
    if params.get("monthly_fixed_cost") is None:
        return False, t("wf.readiness.no_fixed")
    return True, ""


def _project_trend_12m(params: dict) -> dict:
    """生成多期趋势预测。

    支持两种输入模式：
    1. 单值 + growth_rate：base_revenue * (1+g)^n * seasonal（原逻辑）
    2. 收入序列：monthly_revenue=[30000, 45000, 60000, ...]，直接逐期计算

    收入序列模式下 growth_rate/seasonal_factor 仍可叠加（乘以序列值），
    但通常用户给序列时不再给增长率。
    """
    # 收入序列：优先从 _revenue_series 读（_fill_and_assess 标准化后 monthly_revenue 是标量）
    revenue_input = params.get("_revenue_series") or params["monthly_revenue"]
    growth = params.get("monthly_growth_rate") or 0.0
    seasonal = params.get("seasonal_factor") or 1.0
    fixed_cost = params.get("monthly_fixed_cost")
    vc_ratio = params.get("variable_cost_ratio")
    available = params.get("available_cash")

    # 防御：收入为 None 或空序列 → 返回空结果
    if revenue_input is None or (isinstance(revenue_input, (list, tuple)) and len(revenue_input) == 0):
        return {"months": [], "input_mode": "unknown", "months_count": 0,
                "summary": {"total_annual_profit": 0, "max_monthly_loss": 0,
                            "months_to_profitability": None, "months_to_breakeven": None,
                            "trend_direction": t("wf.trend.dir_flat")}}

    # 判断输入模式：序列 vs 单值
    is_series = isinstance(revenue_input, (list, tuple)) and len(revenue_input) > 0
    if is_series:
        series = [float(x) for x in revenue_input]
        # 序列模式：如果用户指定了 analysis_months 则用，否则默认 12 期
        # 序列内的值直接使用，序列用完后按 growth_rate 延续
        user_months = params.get("analysis_months")
        months_count = max(len(series), user_months) if user_months else max(len(series), 12)
        post_series_growth = growth
    else:
        base_revenue = float(revenue_input)
        # analysis_months 支持用户指定预测期数，默认 12
        months_count = params.get("analysis_months") or 12
        if months_count < 1:
            months_count = 12

    # 行业季节系数：优先读行业模板的 seasonal_profile，fallback 通用 map
    season_map = _get_industry_seasonal_profile(params.get("industry_name", ""))

    months = []
    cumulative_profit = 0
    cash = available if available is not None else 0
    breakeven_month = None
    months_to_profit = None
    # F2：投资回收期（累计利润 ≥ 总投资）与盈亏平衡月（累计利润首次转正）
    # 是**两个不同的概念**，旧实现把 breakeven_month 直接当 payback_months 输出，
    # 于是「总投资 20 万、月利 1.6 万」被说成「1 个月回本」（真值 12.5 个月）。
    # 没有总投资时回收期不可算，保持 None（不得用盈亏平衡月冒充）。
    payback_month = None
    total_invest = params.get("total_investment")

    for m in range(1, months_count + 1):
        s = season_map.get(((m - 1) % 12) + 1, 1.0) * seasonal

        if is_series:
            if m <= len(series):
                revenue = series[m - 1] * s
            else:
                # 序列用完后，用最后一个值 × 延续增长率
                last = series[-1]
                extra_months = m - len(series)
                revenue = last * ((1 + post_series_growth) ** extra_months) * s
        else:
            g = (1 + growth) ** (m - 1)
            revenue = base_revenue * g * s

        variable = revenue * vc_ratio if vc_ratio is not None else 0
        profit = revenue - fixed_cost - variable if fixed_cost is not None else None
        if profit is not None:
            cumulative_profit += profit
            cash += profit

        month_data = {
            "month": m,
            "revenue": round(revenue, 0),
            "fixed_cost": round(fixed_cost, 0) if fixed_cost is not None else None,
            "variable_cost": round(variable, 0) if vc_ratio is not None else None,
            "profit": round(profit, 0) if profit is not None else None,
            "cumulative_profit": round(cumulative_profit, 0),
            "cash_remaining": round(cash, 0),
        }
        months.append(month_data)

        if profit is not None:
            if months_to_profit is None and profit > 0:
                months_to_profit = m
            if breakeven_month is None and cumulative_profit > 0:
                breakeven_month = m
            # F2：回收期 = 累计利润累计到「覆盖总投资」的那个月
            if (payback_month is None and total_invest
                    and cumulative_profit >= total_invest):
                payback_month = m

    valid_profits = [m["profit"] for m in months if m["profit"] is not None]
    total_annual_profit = sum(valid_profits) if valid_profits else 0
    max_monthly_loss = min(valid_profits) if valid_profits else 0

    # 季节系数来源标注
    industry_name = params.get("industry_name", "")
    has_industry_seasonal = season_map != _DEFAULT_SEASON_MAP

    result = {
        "months": months,
        "input_mode": "series" if is_series else "growth_rate",
        "months_count": months_count,
        "seasonal_source": (t("wf.trend.seasonal_industry", industry=industry_name)
                            if has_industry_seasonal else t("wf.trend.seasonal_generic")),
        "summary": {
            "total_annual_profit": round(total_annual_profit, 0),
            "max_monthly_loss": round(max_monthly_loss, 0),
            "months_to_profitability": months_to_profit,
            "months_to_breakeven": breakeven_month,
            "trend_direction": (
                t("wf.trend.dir_up") if len(months) >= 2 and months[-1]["profit"] is not None
                and months[0]["profit"] is not None
                and months[-1]["profit"] > months[0]["profit"]
                else t("wf.trend.dir_down") if len(months) >= 2 and months[-1]["profit"] is not None
                and months[0]["profit"] is not None
                else t("wf.trend.dir_flat")
            ),
        },
    }
    # NPV/IRR（基于趋势现金流）
    if is_series or params.get("total_investment"):
        cashflows = []
        if params.get("total_investment"):
            cashflows.append(-float(params["total_investment"]))
        for m in months:
            if m["profit"] is not None:
                cashflows.append(float(m["profit"]))
        if len(cashflows) >= 2:
            from tools.financial_calculator import _npv_raw, _irr_raw
            npv = _npv_raw(0.08, cashflows)
            irr = _irr_raw(cashflows)
            result["investment_metrics"] = {
                "npv_8pct": round(npv, 0),
                "irr": round(irr, 4) if irr is not None else None,
                "irr_percent": (t("wf.trend.irr_percent", pct=f"{irr*100:.1f}")
                                if irr is not None else t("wf.trend.irr_unconverged")),
                # F2：回收期与盈亏平衡月分开输出（旧实现两者同一个值）
                "payback_months": payback_month,
                "breakeven_month": breakeven_month,
                "discount_rate": "8%",
                "cashflow_count": len(cashflows),
            }
    # 动态跑道（逐月扣减，找到现金耗尽月）
    if available is not None and available > 0:
        dynamic_runway = None
        min_cash = available
        min_cash_month = 0
        running_cash = available
        for m in months:
            if m["profit"] is not None:
                running_cash += m["profit"]
                if running_cash < min_cash:
                    min_cash = running_cash
                    min_cash_month = m["month"]
                if running_cash <= 0 and dynamic_runway is None:
                    dynamic_runway = m["month"]
        result["dynamic_runway"] = {
            "runway_months": dynamic_runway,
            "runway_label": (t("wf.trend.runway_months", n=dynamic_runway) if dynamic_runway
                             else t("wf.trend.runway_beyond", n=months_count)),
            "min_cash": round(min_cash, 0),
            "min_cash_month": min_cash_month,
        }

    return result


def _safe_runway(params: dict):
    """可用现金或变动成本缺失时返回 '未知'，否则返回跑道月数（避免 None 参与除法崩溃）。

    ⚠️ 返回值会被前端直接展示；'未知' 走文案层，英文下不再是中文残片。
    """
    if params.get("available_cash") is None:
        return t("wf.common.unknown")
    if params.get("variable_cost_ratio") is None and params.get("monthly_variable_cost") is None:
        return t("wf.common.unknown")
    burn = (params["monthly_fixed_cost"] or 0) + (params.get("monthly_variable_cost") or 0)
    rev = params["monthly_revenue"]
    if isinstance(rev, (list, tuple)):
        rev = rev[0] if rev else 0
    return _calc_runway(params["available_cash"], burn, rev).get("runway_months", "N/A")


# ─── 精确推算层（推算式：由用户给出基础值 → 确定公式 → 关联参数）─────────
# 公式唯一出处已上移到 src/field_model.py（声明式字段模型）。
# 本函数是 field_model.derived_values 的薄封装：引擎只消费模型，不手写公式。


def _build_derived_values(params: dict, src: dict) -> list:
    """把「能由用户输入精确推算出」的关联参数聚成清单。

    公式与来源见 field_model.DERIVED_SPECS（唯一出处）；本处只做消费。
    """
    from field_model import derived_values
    return derived_values(params, src)


# ─── 共享校验态（Phase 1：④ 防止三工具各自重算不一致）─────────────────────

def _fill_and_assess(raw: dict) -> dict:
    """一次填充 + 校验 + 标注，供 quick_scan / trend / compare 共用。

    返回统一状态字典，三工具不再各自调 _fill_params 导致默认/置信不一致。
    insufficient=True 时，下游应返回骨架而非静默误报（修 trend 在稀疏输入下
    输出 12 个月全 -8000 的静默误报）。
    """
    params, src, mixed = _fill_params(raw)
    conf = _derive_conf(src)
    suff = _check_sufficiency(params, src)
    # P0：数据基础分类（user/missing/hypothesis），供决策层与输出层用
    from param_guard import derive_basis_map
    basis = derive_basis_map(src)
    # 派生一致性（月营收 vs 客流×单价 等）：规则层先发现矛盾，不靠 LLM。
    # 一致性规则唯一出处已上移到 field_model.CONSISTENCY_RULES。
    from field_model import consistency_issues
    derived_issues = consistency_issues(params)
    # 收入序列标准化：把数组保存到 _revenue_series，params["monthly_revenue"] 标准化为标量
    # 这样下游所有单期算术（保本/敏感性/场景/陷阱）都用标量，趋势预测用 _revenue_series
    rev_val = params.get("monthly_revenue")
    if isinstance(rev_val, (list, tuple)) and len(rev_val) > 0:
        params["_revenue_series"] = list(rev_val)
        params["monthly_revenue"] = float(rev_val[0])
    return {
        "params": params,
        "src": src,
        "conf": conf,
        "basis": basis,
        "suff": suff,
        "assumptions": _build_assumptions(params, src),
        "framework": _build_framework(params, src),
        "derived": _build_derived_values(params, src),
        "derived_issues": derived_issues,
        "mixed": mixed,
        "insufficient": not suff["ok"],
        "gaps": suff["gaps"],
        "gap_codes": suff["gap_codes"],
        "coverage": suff["coverage"],
    }


def _build_skeleton(state: dict) -> dict:
    """门禁拦下时的统一骨架（参数不足，不输出误报结论）。"""
    return {
        "insufficient": True,
        "project_type": state["params"]["industry_name"],
        "stage": state["params"]["stage"],
        "template_mode": state["params"].get("_template_mode", ""),
        "message": t("wf.skeleton.message"),
        "gaps": state["gaps"],
        "gap_codes": state.get("gap_codes", []),
        "coverage": state["coverage"],
        "confidence": state["conf"],
        "basis": state.get("basis", {}),
        "param_sources": state["src"],
        "assumptions": state["assumptions"],
        "derived": state.get("derived", []),
        "framework": state["framework"],
        "next_step": t("wf.skeleton.next_step"),
    }


# ─── 情景/区间引擎（Phase 1：③⑥ 输出范围而非单点）───────────────────────

# 不确定输入的可扰动幅度（⑥-lite：通用 band，暂不改 12 模板结构）
_VC_BAND = 0.08          # 变动成本率 ±0.08（点值）
_REV_GUESS_BAND = 0.30   # 营收为[推算]时 ±30%（客流波动）


def _recompute_outputs(params: dict):
    """给定填充后的参数，重算 (月利润, 跑道月数)。避免重跑 _fill_params。

    D2：变动成本率缺失时 vc=None → 利润返回 None，不再算假硬数。
    收入序列模式：取序列第一个值做单期计算。
    """
    rev = params["monthly_revenue"]
    # 收入序列：取第一个值做单期快照
    if isinstance(rev, (list, tuple)):
        rev = rev[0] if rev else None
    vc = params.get("variable_cost_ratio")
    if vc is None:
        return None, _runway_numeric(params)
    fixed = params.get("monthly_fixed_cost")
    if rev is None or fixed is None:
        return None, _runway_numeric(params)
    profit = rev - fixed - rev * vc
    return profit, _runway_numeric(params)


def _runway_numeric(params: dict):
    """返回跑道月数（数值）或 None（现金/变动成本缺失）。区别于 _safe_runway 的 '未知' 字符串。"""
    if params.get("available_cash") is None:
        return None
    if params.get("variable_cost_ratio") is None and params.get("monthly_variable_cost") is None:
        return None
    burn = (params["monthly_fixed_cost"] or 0) + (params.get("monthly_variable_cost") or 0)
    rev = params["monthly_revenue"]
    if isinstance(rev, (list, tuple)):
        rev = rev[0] if rev else 0
    return _calc_runway(params["available_cash"], burn, rev).get("runway_months")


def _build_scenarios(params: dict, src: dict) -> dict:
    """对 [候选]/[推算]/[缺失] 输入在合理区间内扰动，输出 乐观/中性/保守 三档。

    核心思想：把"未知"从被掩盖的缺陷，变成驱动结论弹性的燃料。
    原 bug 里的"虚构 2 人×6000"在此被重新定位为「保守情景」而非「单一基准答案」。
    """
    vc_code = st.code_of(src, "variable_cost_ratio")
    rev_code = st.code_of(src, "monthly_revenue")
    labor_code = st.code_of(src, "employee_count")
    cash_code = st.code_of(src, "total_investment")

    base_profit, base_runway = _recompute_outputs(params)

    # 保守情景：成本/比率上行，营收（若推算）下行，人工缺失则假设需 2 人
    wc = dict(params)
    if vc_code == DERIVED and wc["variable_cost_ratio"] is not None:
        wc["variable_cost_ratio"] = min(0.95, wc["variable_cost_ratio"] + _VC_BAND)
    if rev_code == DERIVED and wc["monthly_revenue"] is not None:
        wc["monthly_revenue"] = wc["monthly_revenue"] * (1 - _REV_GUESS_BAND)
    # 无默认：人工缺失不再虚构 2人×6000（取消输入伪造型默认，D2 延展）
    worst_profit, worst_runway = _recompute_outputs(wc)

    # 乐观情景：反向
    bc = dict(params)
    if vc_code == DERIVED and bc["variable_cost_ratio"] is not None:
        bc["variable_cost_ratio"] = max(0.05, bc["variable_cost_ratio"] - _VC_BAND)
    if rev_code == DERIVED and bc["monthly_revenue"] is not None:
        bc["monthly_revenue"] = bc["monthly_revenue"] * (1 + _REV_GUESS_BAND)
    best_profit, best_runway = _recompute_outputs(bc)

    drivers = []
    if vc_code in (CANDIDATE, DERIVED):
        drivers.append(t("wf.scenario.driver_vc_guess"))
    elif vc_code == MISSING:
        drivers.append(t("wf.scenario.driver_vc_missing"))
    if rev_code == DERIVED:
        drivers.append(t("wf.scenario.driver_rev_guess"))
    if labor_code == MISSING:
        drivers.append(t("wf.scenario.driver_labor_missing"))
    if cash_code == MISSING:
        drivers.append(t("wf.scenario.driver_cash_missing"))

    # D2：变动成本率缺失 → best/worst/base 均 None，上下游按「未知」处理而非崩溃/假数
    def _round_or_none(v):
        return round(v, 0) if isinstance(v, (int, float)) else None

    return {
        "monthly_profit": {
            "best": _round_or_none(best_profit),
            "base": _round_or_none(base_profit),
            "worst": _round_or_none(worst_profit),
        },
        "runway": {"best": best_runway, "base": base_runway, "worst": worst_runway},
        "drivers": drivers,
        "has_uncertainty": bool(drivers),
    }


# ─── 叙事 > 判决（Phase 1：⑤ 风险聚焦，而非单一 🔴危险）─────────────────

# 不确定杠杆的呈现优先级（最该先让用户补的排前面）
_LEVER_PRIORITY = {
    "employee_count": 0,
    "monthly_revenue": 1,
    "variable_cost_ratio": 2,
    "total_investment": 3,
}
_LEVER_NAME = {
    "employee_count": "wf.lever.employee_count",
    "monthly_revenue": "wf.lever.monthly_revenue",
    "variable_cost_ratio": "wf.lever.variable_cost_ratio",
    "total_investment": "wf.lever.total_investment",
}
# 只有真正影响利润/跑道的核心杠杆才进「风险聚焦」头条；
# stage/growth_rate 等次要默认不在此列，避免把无关默认当风险。
_MATERIAL_FIELDS = {
    "employee_count", "avg_salary", "monthly_revenue", "daily_traffic",
    "price_per_unit", "variable_cost_ratio", "total_investment", "monthly_rent",
}


def _build_narrative(params: dict, src: dict, scenarios: dict, pit_total: int) -> str:
    """用置信层 + 情景，给出「风险集中在哪、你最该质疑什么」的聚焦陈述。"""
    uncertain = [k for k in _MATERIAL_FIELDS
                 if st.code_of(src, k) in (CANDIDATE, DERIVED, MISSING)]
    if not uncertain:
        return t("wf.narrative.confident")

    uncertain.sort(key=lambda k: _LEVER_PRIORITY.get(k, 9))
    top = uncertain[0]
    lead = t(_LEVER_NAME.get(top, top))

    sp = scenarios.get("monthly_profit", {})
    if sp.get("base") is None:
        # D2：变动成本率缺失 → 利润不可算，叙事只刷「还不能定」
        rng = t("wf.narrative.profit_unknown")
    elif scenarios.get("has_uncertainty"):
        rng = t("wf.narrative.profit_range", base=f"{sp['base']:,.0f}",
                best=f"{sp['best']:,.0f}", worst=f"{sp['worst']:,.0f}")
    else:
        rng = t("wf.narrative.profit_approx", v=f"{sp.get('base', 0):,.0f}")

    text = t("wf.narrative.risk_focus", lead=lead, rng=rng)
    if pit_total:
        text += t("wf.narrative.extra_risks", n=pit_total)
    return text


# ─── 工具 1: quick_scan（快速扫描）───────────────────────────────────────


@tool
def quick_scan(params_json: str) -> str:
    """
    快速扫描。接收项目参数（自然语言或 JSON），执行全量计算并返回仪表盘。

    适用场景：用户首次描述项目、修改参数后重新计算。

    参数:
        params_json: 用户输入的自然语言描述，或 JSON 格式的参数
        支持的参数：industry, total_investment, monthly_rent, daily_traffic,
        price_per_unit, monthly_revenue, monthly_expense, employee_count,
        founder_count, stage, city, monthly_growth_rate, etc.

    返回: JSON 仪表盘数据（核心指标 + 参数 + 敏感性 + 风险 + benchmark）
    """
    try:
        # 解析输入：支持 JSON 和自然语言两种格式
        raw, raw_text = _parse_tool_input(params_json)
        raw["_raw_text"] = raw_text
        state = _fill_and_assess(raw)
        params, param_sources = state["params"], state["src"]
        mixed_warning = state["mixed"]

        # ── 充分性门禁（决策B）：核心字段缺失 → 返回骨架，不输出误报仪表盘 ──
        if state["insufficient"]:
            return json.dumps(_build_skeleton(state), ensure_ascii=False, indent=2)

        # ── 模块 1: 盈亏平衡 ──
        # 无默认：价/变动成本/固定成本任一缺失 → 不硬算保本。
        # ① 客单价口径保本（需 price + vc_per_unit + fixed）
        # ② 营收口径保本（订阅/合同型无客单价：fixed/(1-vc)，需 vc + fixed）
        vcr_known = params.get("variable_cost_ratio") is not None
        fixed_known = params.get("annual_fixed_cost") is not None
        if (params["price_per_unit"] is None
                or params["variable_cost_per_unit"] is None
                or not fixed_known):
            be = {"error": t("wf.error.breakeven_incomplete")}
            daily_be = None
            rev_based_be = None
        else:
            be = _calc_breakeven(
                params["annual_fixed_cost"],
                params["price_per_unit"],
                params["variable_cost_per_unit"]
            )
            # F1 修复：`breakeven_units` 是**年度**保本单位数，旧实现除以 365 得日均，
            # 而月营收按「客流 × 客单价 × 30」算 —— 一年 360 天。两个口径混用，
            # 保本客流被系统性低估约 1.0~1.2%：按系统给的保本客流经营，
            # 实际月亏 200~1240 元，系统却标「可达保本」。
            # 统一到营收口径（30 天/月 ⇒ 360 天/年）。两处必须共用同一常量。
            daily_be = (round(be.get("breakeven_units", 0) / DAYS_PER_YEAR, 0)
                        if "error" not in be else None)
            rev_based_be = None
        # 补充「营收口径」保本：未给客单价（订阅/合同类生意）时，用 月固定÷(1-变动成本率)
        # 给出月均需营收，保证所有行业都能产出真实的收支平衡建议。
        # 条件：vc 已知 + fixed 已知（无默认；不要求有客单价）
        if ("error" in be or params["price_per_unit"] is None) and vcr_known and fixed_known:
            vcr = params.get("variable_cost_ratio", 0) or 0
            mfc = params.get("monthly_fixed_cost", 0) or 0
            if 0 <= vcr < 1 and mfc > 0:
                rev_based_be = round(mfc / (1 - vcr), 0)
            be = {
                "breakeven_revenue_monthly": rev_based_be,
                "contribution_margin_ratio": f"{round((1 - vcr) * 100, 1)}%",
                "note": t("wf.error.rev_based_note"),
            }

        # ── 模块 2: 现金流（可用现金缺失则跳过跑道计算；固定成本未知则跑道不可算）──
        # 收入序列模式：单期快照取第一个值
        _rev_for_calc = params["monthly_revenue"]
        if isinstance(_rev_for_calc, (list, tuple)):
            _rev_for_calc = _rev_for_calc[0] if _rev_for_calc else 0
        monthly_var_cost = params.get("monthly_variable_cost")
        vcr_missing = params.get("variable_cost_ratio") is None and monthly_var_cost is None
        fixed_cost = params.get("monthly_fixed_cost")
        if vcr_missing:
            # 缺失不当 0：变动成本未知时跑道/现金与利润同一套「未知」
            rw = {"runway_months": None, "note": t("wf.error.runway_vc_missing")}
        elif params["available_cash"] is not None and fixed_cost is not None:
            burn = fixed_cost + (monthly_var_cost or 0)
            rw = _calc_runway(params["available_cash"], burn, _rev_for_calc)
        elif params["available_cash"] is None:
            rw = {"runway_months": None, "note": t("wf.error.runway_invest_missing")}
        else:
            rw = {"runway_months": None, "note": t("wf.error.runway_fixed_missing")}

        # ── 模块 3: 敏感性 ──
        # F3 修复：固定/变动成本分传，变动成本随营收联动（边际贡献口径）
        _fix_c = fixed_cost or 0
        _var_c = monthly_var_cost or 0
        if params.get("monthly_profit") is None:
            sens = {"error": t("wf.error.sens_incomplete")}
        else:
            sens = _calc_sensitivity(
                params["monthly_revenue"],
                fixed_cost=_fix_c,
                variable_cost=_var_c,
            )

        # ── 模块 4: 陷阱 ──
        # 客单价/变动成本未知（为 0）时不跑定价检查，避免“定价低于成本”误报（缺失≠0）
        pits = _do_full_scan(
            price=params["price_per_unit"] if params["price_per_unit"] and params["price_per_unit"] > 0 else None,
            variable_cost=params["variable_cost_per_unit"] if params["variable_cost_per_unit"] and params["variable_cost_per_unit"] > 0 else None,
            current_cash=params["available_cash"],
            monthly_expense=params["monthly_fixed_cost"],
            monthly_revenue=params["monthly_revenue"],
            team_size=params["employee_count"],
            founder_count=params["founder_count"],
            has_tech_cofounder=params["has_tech_cofounder"],
            is_tech_project=(params["industry_name"] in ["SaaS", "软件"]),
            project_description=raw.get("description", ""),
            tam_description=params["tam_description"],
            industry=params["industry_name"],
        )

        # ── 模块 5: 情景/区间（③⑥）与 叙事（⑤）──
        scenarios = _build_scenarios(params, param_sources)
        pit_total = pits.get("total_pitfalls", 0)
        narrative = _build_narrative(params, param_sources, scenarios, pit_total)

        # ── 状态标记（D2：变动成本率缺失时 profit/gross_margin 为 None → 状态=未知）──
        if params["monthly_profit"] is None:
            profit_status = t("wf.status.profit_unknown")
        else:
            profit_status = (t("wf.status.profit_up") if params["monthly_profit"] > 0
                             else t("wf.status.profit_down"))
        rw_months = rw.get("runway_months")
        if vcr_missing:
            cash_status = t("wf.status.cash_unknown_vc")
        elif rw_months is None:
            cash_status = t("wf.status.cash_unknown_invest")
        elif isinstance(rw_months, (int, float)):
            if rw_months < 0:
                cash_status = t("wf.status.cash_positive")
            elif rw_months < 6:
                cash_status = t("wf.status.cash_danger")
            elif rw_months < 12:
                cash_status = t("wf.status.cash_tight")
            else:
                cash_status = t("wf.status.cash_safe")
        elif rw_months == "无限":
            cash_status = t("wf.status.cash_positive")
        else:
            cash_status = t("wf.status.cash_unknown")

        if daily_be is not None and params["daily_traffic"] is not None:
            be_status = (t("wf.status.be_traffic_short") if params["daily_traffic"] < daily_be
                         else t("wf.status.be_reachable"))
        elif rev_based_be is not None and params["monthly_revenue"] is not None:
            _rev_check = params["monthly_revenue"]
            if isinstance(_rev_check, (list, tuple)):
                _rev_check = _rev_check[0] if _rev_check else 0
            be_status = (t("wf.status.be_below") if _rev_check < rev_based_be
                         else t("wf.status.be_reachable"))
        else:
            be_status = t("wf.status.be_partial")
        # S3：gross_margin 内部为 0~1 口径，阈值由 50/30 相应改为 0.5/0.3
        if params.get("gross_margin") is None:
            margin_status = t("wf.status.profit_unknown")
        else:
            margin_status = (t("wf.status.margin_healthy") if params["gross_margin"] >= 0.5
                             else (t("wf.status.margin_fair") if params["gross_margin"] >= 0.3
                                   else t("wf.status.margin_low")))

        dashboard = {
            "project_type": params["industry_name"],
            "stage": params["stage"],
            "template_applied": not params.get("_skip_template", False),
            "mixed_industry_warning": mixed_warning,
            "param_sources": param_sources,
            "core_metrics": {
                "monthly_revenue": (
                    round(params["monthly_revenue"][0], 0)
                    if isinstance(params["monthly_revenue"], (list, tuple)) and params["monthly_revenue"]
                    else round(params["monthly_revenue"], 0) if params["monthly_revenue"] is not None else None
                ),
                "monthly_profit": round(params["monthly_profit"], 0) if params["monthly_profit"] is not None else None,
                "daily_breakeven": daily_be,
                "breakeven_revenue_monthly": rev_based_be,
                "runway_months": rw_months,
                # S3：内部 0~1，对外展示语义仍是百分数（60 表示 60%），
                # 故此处 ×100 保住 formatter / decision_engine 的既有契约。
                "gross_margin_percent": (round(params["gross_margin"] * 100, 1)
                                         if params.get("gross_margin") is not None else None),
            },
            "status": {
                "profit": profit_status,
                "cash": cash_status,
                "breakeven": be_status,
                "margin": margin_status,
            },
            "params": {
                "total_investment": params["total_investment"],
                "monthly_rent": params["monthly_rent"],
                "daily_traffic": params["daily_traffic"],
                "price_per_unit": params["price_per_unit"],
                "employee_count": params["employee_count"],
                "avg_salary": params["avg_salary"],
                "variable_cost_ratio": (
                    f"{params['variable_cost_ratio']*100:.0f}%"
                    if params['variable_cost_ratio'] is not None else t("wf.common.unknown")
                ),
                "available_cash": round(params["available_cash"], 0) if params["available_cash"] is not None else None,
                # 人工分解（裸薪 / 含社保）——让合计不再是黑箱
                "monthly_labor_cash": (
                    round(params["monthly_labor_cash"], 0)
                    if params.get("monthly_labor_cash") is not None else None
                ),
                "monthly_labor": (
                    round(params["monthly_labor"], 0)
                    if params.get("monthly_labor") is not None else None
                ),
                "labor_burden_rate": params.get("labor_burden_rate"),
                "monthly_fixed_cost": (
                    round(params["monthly_fixed_cost"], 0)
                    if params.get("monthly_fixed_cost") is not None else None
                ),
                "founder_count": params["founder_count"],
                "city": params["city"],
                "competitor_count": params["competitor_count"],
                "location_type": params["location_type"],
                "has_financing": params["has_financing"],
                "funding_round": params["funding_round"],
                "funding_amount": (
                    round(params["funding_amount"], 0)
                    if params.get("funding_amount") is not None else None
                ),
                # D8：用户口中的客流单位（碗/杯/份）透传给呈现层。
                # `_` 前缀内部键，不参与业务计算，只用于单位显示。
                "_traffic_unit": params.get("_traffic_unit"),
            },
            "param_sources": {k: v for k, v in param_sources.items()},
            "confidence": _derive_conf(param_sources),
            "basis": state["basis"],
            "assumptions": _build_assumptions(params, param_sources),
            "derived": state["derived"],
            "derived_issues": state["derived_issues"],
            "template_mode": params.get("_template_mode", ""),
            "mixed_warning": mixed_warning,
            "narrative": narrative,
            "scenarios": scenarios,
            "breakeven": be,
            "runway": rw,
            "sensitivity": sens,
            "pitfalls": pits,
            "benchmark": params["benchmark"],
            # 真实可计算的 benchmark 对比（数值区间超界告警）
            "benchmark_check": _benchmark_check(params, params["benchmark"]),
            # 行业典型成本结构占比（差异化参考）
            "industry_cost_structure": params.get("cost_structure", {}),
            # 可用动作列表（供 LLM 主持人推荐下一步）
            "available_actions": _derive_available_actions(params, scenarios),
        }

        # ── 趋势预测（多期模拟器核心）──
        profit_ready, _ = _profit_readiness(params)
        if profit_ready:
            trend = _project_trend_12m(params)
            dashboard["trend"] = {
                "input_mode": trend.get("input_mode", "growth_rate"),
                "months_count": trend.get("months_count", 12),
                "summary": trend["summary"],
                "months": trend["months"],
            }
            # NPV/IRR（已由 _project_trend_12m 计算）
            if "investment_metrics" in trend:
                dashboard["investment_metrics"] = trend["investment_metrics"]
            # 动态跑道（已由 _project_trend_12m 计算）
            if "dynamic_runway" in trend:
                dashboard["dynamic_runway"] = trend["dynamic_runway"]

        return json.dumps(dashboard, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("wf.error.quick_scan_fail", e=str(e))}, ensure_ascii=False)


# ─── 工具 2: trend_projection（趋势预测）─────────────────────────────────


@tool
def trend_projection(params_json: str) -> str:
    """
    12 个月趋势预测。基于当前参数，生成未来 12 个月的收入/成本/利润/现金流趋势。

    适用场景：用户想看长期走势、判断回本时间、评估季节性风险。

    参数:
        params_json: 当前项目参数的 JSON 字符串（与 quick_scan 相同格式）

    返回: JSON，包含每月详细数据和汇总趋势
    """
    try:
        raw, raw_text = _parse_tool_input(params_json)
        raw["_raw_text"] = raw_text
        state = _fill_and_assess(raw)
        # ④：稀疏输入（无月营收）下不再静默输出 12 个月全 -8000 的误报趋势
        if state["insufficient"]:
            return json.dumps(_build_skeleton(state), ensure_ascii=False, indent=2)
        params = state["params"]

        # M1：趋势预测依赖「可变成本 + 营收 + 固定成本」齐备才能算利润曲线。
        # 变动成本率缺失是软缺口（insufficient=False 但 profit 不可算），此时若硬算
        # 会输出 12 个月假趋势（甚至 None 崩溃）。统一走利润可算性降级，与 quick_scan
        # 的「⚪ 未知」同思路，绝不输出误导性趋势。
        profit_ready, profit_reason = _profit_readiness(params)
        if not profit_ready:
            return json.dumps({
                "insufficient": True,
                "tool": "trend",
                "message": t("wf.error.trend_insufficient", reason=profit_reason),
                "gaps": state.get("gaps", []),
                "coverage": state.get("coverage", 0),
            }, ensure_ascii=False, indent=2)

        trend = _project_trend_12m(params)

        return json.dumps(trend, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("wf.error.trend_fail", e=str(e))}, ensure_ascii=False)


# ─── 工具 3: compare_scenarios（场景对比）─────────────────────────────────


@tool
def compare_scenarios(base_json: str, alt_json: str) -> str:
    """
    场景对比。对比两个参数方案的核心指标差异。

    适用场景：用户说「如果租金降到1万 vs 保持1.5万」、「日均200人 vs 100人」。

    参数:
        base_json: 基准方案的参数 JSON 字符串
        alt_json: 对比方案的参数 JSON 字符串

    返回: JSON，包含两个方案的并排对比数据
    """
    try:
        base_raw, base_text = _parse_tool_input(base_json)
        alt_raw, alt_text = _parse_tool_input(alt_json)
        base_raw["_raw_text"] = base_text
        alt_raw["_raw_text"] = alt_text

        # ④：对比两方案均经同一校验态，避免默认/置信不一致；任一侧缺月营收则拦
        base_state = _fill_and_assess(base_raw)
        alt_state = _fill_and_assess(alt_raw)
        if base_state["insufficient"] or alt_state["insufficient"]:
            gaps = []
            if base_state["insufficient"]:
                gaps.append(t("wf.compare.scenario_a") + "：" + "；".join(base_state["gaps"]))
            if alt_state["insufficient"]:
                gaps.append(t("wf.compare.scenario_b") + "：" + "；".join(alt_state["gaps"]))
            return json.dumps({
                "insufficient": True,
                "message": t("wf.error.compare_rev_insufficient"),
                "gaps": gaps,
            }, ensure_ascii=False, indent=2)

        base_p = base_state["params"]
        alt_p = alt_state["params"]

        # ④b：利润可算性检查。insufficient 只拦「月营收缺失」，变动成本率缺失是软缺口
        # （insufficient=False 但 monthly_profit=None），此时 diff 减法会 None-None 崩溃。
        # 复用统一 _profit_readiness，保证 trend/compare 对「算不出利润」的判定一致。
        profit_gaps = []
        for side, p in ((t("wf.compare.scenario_a"), base_p), (t("wf.compare.scenario_b"), alt_p)):
            ready, reason = _profit_readiness(p)
            if not ready:
                profit_gaps.append(f"{side}：{reason}")
        if profit_gaps:
            return json.dumps({
                "insufficient": True,
                "message": t("wf.error.compare_profit_insufficient"),
                "gaps": profit_gaps,
            }, ensure_ascii=False, indent=2)

        # 收入序列取第一个值做单期 diff
        base_rev = base_p["monthly_revenue"]
        alt_rev = alt_p["monthly_revenue"]
        if isinstance(base_rev, (list, tuple)):
            base_rev = base_rev[0] if base_rev else 0
        if isinstance(alt_rev, (list, tuple)):
            alt_rev = alt_rev[0] if alt_rev else 0
        diff_profit = round(alt_p["monthly_profit"] - base_p["monthly_profit"], 0)
        diff_revenue = round(alt_rev - base_rev, 0)

        # 两个方案分别做陷阱扫描
        base_pits = _do_full_scan(
            price=base_p["price_per_unit"],
            variable_cost=base_p["variable_cost_per_unit"],
            current_cash=base_p["available_cash"],
            monthly_expense=base_p["monthly_fixed_cost"],
            monthly_revenue=base_p["monthly_revenue"],
            team_size=base_p["employee_count"],
            founder_count=base_p["founder_count"],
            has_tech_cofounder=base_p["has_tech_cofounder"],
            is_tech_project=(base_p["industry_name"] in ["SaaS", "软件"]),
            project_description="",
            tam_description="",
            industry=base_p["industry_name"],
        )
        alt_pits = _do_full_scan(
            price=alt_p["price_per_unit"],
            variable_cost=alt_p["variable_cost_per_unit"],
            current_cash=alt_p["available_cash"],
            monthly_expense=alt_p["monthly_fixed_cost"],
            monthly_revenue=alt_p["monthly_revenue"],
            team_size=alt_p["employee_count"],
            founder_count=alt_p["founder_count"],
            has_tech_cofounder=alt_p["has_tech_cofounder"],
            is_tech_project=(alt_p["industry_name"] in ["SaaS", "软件"]),
            project_description="",
            tam_description="",
            industry=alt_p["industry_name"],
        )

        comparison = {
            "base_scenario": {
                "label": t("wf.compare.label_a"),
                "monthly_revenue": round(base_rev, 0),
                "monthly_profit": round(base_p["monthly_profit"], 0),
                "monthly_fixed_cost": round(base_p["monthly_fixed_cost"], 0),
                "runway_months": _safe_runway(base_p),
                "pitfall_count": base_pits["total_pitfalls"],
            },
            "alt_scenario": {
                "label": t("wf.compare.label_b"),
                "monthly_revenue": round(alt_rev, 0),
                "monthly_profit": round(alt_p["monthly_profit"], 0),
                "monthly_fixed_cost": round(alt_p["monthly_fixed_cost"], 0),
                "runway_months": _safe_runway(alt_p),
                "pitfall_count": alt_pits["total_pitfalls"],
            },
            "diff": {
                "profit": diff_profit,
                "revenue": diff_revenue,
                "verdict": (t("wf.compare.verdict_b_better") if diff_profit > 0
                            else (t("wf.compare.verdict_a_better") if diff_profit < 0
                                  else t("wf.compare.verdict_tie"))),
            },
        }

        # 多期趋势对比：两个方案分别做趋势预测，比较年度利润和回本月
        profit_base_ready, _ = _profit_readiness(base_p)
        profit_alt_ready, _ = _profit_readiness(alt_p)
        if profit_base_ready and profit_alt_ready:
            base_trend = _project_trend_12m(base_p)
            alt_trend = _project_trend_12m(alt_p)
            base_annual = base_trend["summary"]["total_annual_profit"]
            alt_annual = alt_trend["summary"]["total_annual_profit"]
            base_be = base_trend["summary"]["months_to_breakeven"]
            alt_be = alt_trend["summary"]["months_to_breakeven"]
            comparison["trend_comparison"] = {
                "base_annual_profit": base_annual,
                "alt_annual_profit": alt_annual,
                "annual_profit_diff": round(alt_annual - base_annual, 0),
                "base_breakeven_month": base_be,
                "alt_breakeven_month": alt_be,
                "breakeven_delta": (
                    (alt_be or 99) - (base_be or 99)
                    if alt_be is not None or base_be is not None else None
                ),
                "verdict_annual": (
                    t("wf.compare.annual_b_higher") if alt_annual > base_annual
                    else t("wf.compare.annual_a_higher") if base_annual > alt_annual
                    else t("wf.compare.annual_tie")
                ),
            }

        return json.dumps(comparison, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("wf.error.compare_fail", e=str(e))}, ensure_ascii=False)


# ─── 工具 4: cashflow_projection（月现金流明细表，档 B）─────────────────────


@tool
def cashflow_projection(params_json: str) -> str:
    """
    12 个月现金流明细表（档 B）。

    适用场景：用户问「现金流怎么样」「钱什么时候花完」「现金够不够」「这个月要花多少」。

    与 quick_scan/trend 的关系：
    - quick_scan：P&L（收入-成本-利润）静态快照
    - cashflow_projection：实际到账/支出的逐月现金流（期初现金=总投资推导 available_cash，
      一次性大额 / 到账延迟 / 季度支付单独进现金流，不上 P&L）

    参数:
        params_json: 与 quick_scan 相同格式，额外支持：
            - one_time_expenses: [{"month": 2, "amount": 50000, "label": "装修"}]
            - receivable_lag_months: 收入到账延迟（月，0=即时）
            - payment_rhythm: {"rent": "quarterly"} 等

    返回: JSON {schedule, zero_cash_month, max_shortfall, insufficient, gaps}
    """
    try:
        raw, raw_text = _parse_tool_input(params_json)
        raw["_raw_text"] = raw_text
        state = _fill_and_assess(raw)
        # 期初现金 = available_cash（总投资推导；无默认，缺失→还不能定）
        opening = state["params"].get("available_cash")
        monthly_revenue_value = state["params"].get("monthly_revenue")
        fixed = state["params"].get("monthly_fixed_cost")
        variable = state["params"].get("monthly_variable_cost")
        lag = raw.get("receivable_lag_months")
        one_time = raw.get("one_time_expenses") or []
        rhythm = raw.get("payment_rhythm") or {}

        result = _calc_cashflow_schedule(
            opening_cash=opening,
            monthly_revenue=monthly_revenue_value,
            monthly_revenue_lag=lag,
            monthly_expenses={"fixed": fixed, "variable": variable},
            one_time_expenses=one_time,
            payment_rhythm=rhythm,
            months=12,
        )
        result["project_type"] = state["params"].get("industry_name")
        result["opening_now"] = opening
        result["basis"] = state["basis"]
        result["notes"] = []
        if lag:
            result["notes"].append(t("wf.cashflow.receivable_lag", lag=lag))
        if rhythm.get("rent"):
            if rhythm["rent"] == "quarterly":
                result["notes"].append(t("wf.cashflow.rent_quarterly"))
            elif rhythm["rent"] == "monthly":
                result["notes"].append(t("wf.cashflow.rent_monthly"))
        if one_time:
            result["notes"].append(t("wf.cashflow.one_time", n=len(one_time)))
        result["param_sources"] = {k: v for k, v in state["src"].items()
                                   if isinstance(v, str) and not k.startswith("_")}
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"error": t("wf.error.cashflow_fail", e=str(e))}, ensure_ascii=False)
