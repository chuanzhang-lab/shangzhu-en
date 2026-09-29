"""参数守门层 — 从根上断绝参数失真

问题根因：
- 抽取把「人工3500*2」读成 3500 人、把「6000%」读成 60 倍成本率；
- 会话合并静默覆盖历史值，矛盾直到 LLM 解读才被发现（太晚）；
- 引擎对荒谬值只算不拦，产出「月亏3400万」的垃圾仪表盘。

本层在「抽取 → 合并 → 引擎 → 输出」四道关统一设卡：

1. **约束注册表（FIELD_CONSTRAINTS）**：每个字段的类型/合理区间/单位归一化规则
2. **分级校验**：
   - CRITICAL（致命）：明显物理不可能 → 拒绝/自动修正并标记待确认
   - WARNING（警告）：超出常识但可能合法 → 接受但标记
   - CONTRADICTION（矛盾）：与历史会话冲突 → 标记待确认，不静默覆盖
3. **归一化**：百分比/倍数/中文数字统一成标准值，杜绝「60 vs 0.6 vs 6000%」歧义
4. **无 LLM 也能发现矛盾**：规则层先检测，LLM 只做人性化表达

设计原则：
- 薄：纯函数 + 一张约束表，不引入依赖
- 诚实：任何自动修正都标记「待确认」，绝不静默
- 前置：矛盾在结构化输出里显式暴露，不依赖 LLM
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from i18n import industry_name, t

# ── 校验级别 ──────────────────────────────────────────────────────────────
LEVEL_OK = "ok"
LEVEL_WARNING = "warning"      # 超出常识但可能合法
LEVEL_CRITICAL = "critical"    # 物理不可能，需修正
LEVEL_CONTRADICTION = "contradiction"  # 与历史冲突，需确认


# ── 数据基础（basis）分类 ────────────────────────────────────────────────
# 每个参数归类为三类来源之一（P0 数据基础层）：
#   user       → 用户亲口给出的事实，唯一硬输入
#   missing    → 未提供且无行业候选，能算的照算、缺口标出
#   hypothesis → 未提供但有行业常识候选（须用户「应用A」确认后才临时进计算）
BASIS_USER = "user"
BASIS_MISSING = "missing"
BASIS_HYPOTHESIS = "hypothesis"


def classify_basis(source_tag: str, has_candidate: bool = False) -> str:
    """按来源标注判 basis。

    - [用户]/[推导]（源自用户给出的值）→ user
    - [缺失] → missing
    - [候选]（行业模板猜测）→ hypothesis（需确认才进计算，除非无候选）
    """
    if not source_tag:
        return BASIS_MISSING
    if source_tag.startswith(("[用户]", "[推导]")):
        return BASIS_USER
    if source_tag.startswith("[缺失]"):
        return BASIS_MISSING
    if source_tag.startswith("[候选]"):
        # 有候选才算 hypothesis；候选即模板默认本身，总标记为 hypothesis
        return BASIS_HYPOTHESIS
    return BASIS_USER  # [推算] 由用户给出的基础值推导而来，视为 user 衍生


def derive_basis_map(param_sources: dict) -> Dict[str, str]:
    """批量从来源标注推导每个字段的 basis。"""
    out = {}
    for k, s in (param_sources or {}).items():
        if not isinstance(s, str):
            continue
        out[k] = classify_basis(s)
    return out


# ── 约束注册表 ────────────────────────────────────────────────────────────
# 每个字段定义：
#   type: 期望类型
#   min/max: 硬边界（超出即 CRITICAL）
#   soft_min/soft_max: 软边界（超出即 WARNING）
#   normalize: 单位归一化函数（可选）
#   unit_hint: 期望单位（用于生成提示）
FIELD_CONSTRAINTS = {
    "monthly_rent": {
        "type": (int, float), "min": 0, "max": 100_000_000,
        "soft_min": 0, "soft_max": 500_000,
        "unit_hint": "pg.unit.per_month",
    },
    "monthly_revenue": {
        "type": (int, float), "min": 0, "max": 1e12,
        "soft_min": 0, "soft_max": 10_000_000,
        "unit_hint": "pg.unit.per_month",
    },
    "total_investment": {
        "type": (int, float), "min": 0, "max": 1e12,
        "soft_min": 0, "soft_max": 50_000_000,
        "unit_hint": "pg.unit.cny",
    },
    "price_per_unit": {
        "type": (int, float), "min": 0.01, "max": 10_000_000,
        "soft_min": 0.1, "soft_max": 100_000,
        "unit_hint": "pg.unit.cny",
        # 餐饮/零售客单价软上限：串台到几十万必须待确认并剔除
        "industry_soft_max": {"餐饮": 1000, "零售": 5000, "电商": 5000},
    },
    "daily_traffic": {
        "type": (int, float), "min": 0, "max": 1_000_000,
        "soft_min": 0, "soft_max": 50_000,
        "unit_hint": "pg.unit.per_day",
    },
    "employee_count": {
        "type": (int, float), "min": 0, "max": 200,
        "soft_min": 0, "soft_max": 50,
        "unit_hint": "pg.unit.person",
    },
    "avg_salary": {
        "type": (int, float), "min": 0, "max": 5_000_000,
        "soft_min": 500, "soft_max": 100_000,
        "unit_hint": "pg.unit.per_month",
    },
    "variable_cost_ratio": {
        "type": (int, float), "min": 0, "max": 1.0,
        "soft_min": 0, "soft_max": 0.95,
        "normalize": "percent_or_ratio",
        "unit_hint": "pg.unit.ratio_or_pct",
    },
    "variable_cost_rate": {  # 抽取可能给 60 表示 60%
        "type": (int, float), "min": 0, "max": 100,
        "soft_min": 0, "soft_max": 95,
        "normalize": "percent_or_ratio",
        "unit_hint": "pg.unit.pct",
    },
    "gross_margin": {
        "type": (int, float), "min": -1, "max": 1,
        "soft_min": 0, "soft_max": 0.9,
        "normalize": "percent_or_ratio",
        "unit_hint": "pg.unit.ratio_or_pct",
    },
    "monthly_growth_rate": {
        "type": (int, float), "min": -1, "max": 1,
        "soft_min": -0.2, "soft_max": 0.5,
        "normalize": "percent_or_ratio",
        "unit_hint": "pg.unit.ratio_or_pct",
    },
    "monthly_expense": {  # 显式固定成本总数
        "type": (int, float), "min": 0, "max": 1e10,
        "soft_min": 0, "soft_max": 5_000_000,
        "unit_hint": "pg.unit.per_month",
    },
    "utilities": {
        "type": (int, float), "min": 0, "max": 1e8,
        "soft_min": 0, "soft_max": 100_000,
        "unit_hint": "pg.unit.per_month",
    },
    "packaging": {
        "type": (int, float), "min": 0, "max": 1e8,
        "soft_min": 0, "soft_max": 200_000,
        "unit_hint": "pg.unit.per_month",
    },
    "commission": {
        "type": (int, float), "min": 0, "max": 1e8,
        "soft_min": 0, "soft_max": 500_000,
        "unit_hint": "pg.unit.per_month",
    },
    "other_fixed": {
        "type": (int, float), "min": 0, "max": 1e8,
        "soft_min": 0, "soft_max": 200_000,
        "unit_hint": "pg.unit.per_month",
    },
    "unit_variable_cost": {
        "type": (int, float), "min": 0, "max": 1e6,
        "soft_min": 0, "soft_max": 10_000,
        "unit_hint": "pg.unit.per_unit",
    },
}


# ── 归一化 ────────────────────────────────────────────────────────────────

def normalize_value(field: str, value: Any) -> Tuple[Any, Optional[str]]:
    """按字段的目标尺度做单位归一化（字段尺度感知）。

    关键：归一化不能把「6000%」这种荒谬原值先缩到 60 而放过校验。
    - 比例字段（硬上限 ≤1，如 variable_cost_ratio）：v>1 → /100；仍超界则交校验拦
    - 百分比字段（硬上限 100，如 variable_cost_rate）：0<v≤1 → ×100；>100 保留交校验拦

    返回 (归一值, 归一说明)。
    """
    if value is None:
        return None, None
    spec = FIELD_CONSTRAINTS.get(field, {})
    if spec.get("normalize") != "percent_or_ratio":
        return value, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value, None

    v = float(value)
    hard_max = spec.get("max", 1.0)
    if hard_max <= 1.0:
        # 比例字段：60 → 0.6；6000 → 60（仍 >1，由校验标记 CRITICAL）
        if v > 1.0:
            norm = v / 100.0
            return norm, t("pg.norm.to_ratio", value=value, norm=f"{norm:g}")
        return v, None
    # 百分比字段：0.6 → 60；6000 保留（由校验按 >100 标记 CRITICAL）
    if 0 < v <= 1.0:
        norm = v * 100.0
        return norm, t("pg.norm.to_pct", value=value, norm=f"{norm:g}")
    return v, None


# ── 单字段校验 ────────────────────────────────────────────────────────────

def validate_field(field: str, value: Any, industry: Optional[str] = None) -> Dict[str, Any]:
    """校验单个字段值。

    返回 {
        "field": field,
        "value": value,
        "level": LEVEL_*,
        "message": 人类可读说明,
        "auto_fix": 自动修正后的值（若可修）,
        "needs_confirmation": bool,
    }
    """
    result = {
        "field": field,
        "value": value,
        "level": LEVEL_OK,
        "message": "",
        "auto_fix": None,
        "needs_confirmation": False,
    }
    if value is None:
        return result

    spec = FIELD_CONSTRAINTS.get(field)
    if not spec:
        return result

    # 类型检查
    expected = spec.get("type")
    if expected and not isinstance(value, expected):
        result.update(
            level=LEVEL_WARNING,
            message=t("pg.err.type_mismatch", actual=type(value).__name__),
            needs_confirmation=True,
        )
        return result

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return result

    v = float(value)
    hard_min = spec.get("min", float("-inf"))
    hard_max = spec.get("max", float("inf"))
    soft_min = spec.get("soft_min", float("-inf"))
    soft_max = spec.get("soft_max", float("inf"))
    # unit_hint 存的是**键**不是文案：模块级常量直接存文案会把语言冻结在 import 时刻
    unit = t(spec.get("unit_hint") or "pg.unit.none")

    # 硬边界：物理不可能 → 尝试自动修正（宁修正+标记，不丢弃；丢弃会让整句落 chitchat）
    if v < hard_min or v > hard_max:
        auto_fix = None
        # 比例/百分比字段：v/100 落回合法区间 → 视为多打了个数量级（6000%→60%），自动修正
        if spec.get("normalize") == "percent_or_ratio" and v > hard_max:
            candidate = v / 100.0
            if hard_min <= candidate <= hard_max:
                auto_fix = candidate
        if auto_fix is not None:
            result["auto_fix"] = auto_fix
            result["message"] = t(
                "pg.err.autofix", field=field, value=f"{v:g}",
                fixed=f"{auto_fix:g}", unit=unit,
            )
        elif v > hard_max:
            result["message"] = t(
                "pg.err.above_max", field=field, value=f"{v:g}",
                max=f"{hard_max:g}", unit=unit,
            )
        else:
            result["message"] = t("pg.err.below_min", field=field, value=f"{v:g}",
                                  min=f"{hard_min:g}", unit=unit)
        result["level"] = LEVEL_CRITICAL
        result["needs_confirmation"] = True
        return result

    # 行业软上限（餐饮客单价 30 万）：升级为待确认并剔除，与负租金同一策略
    industry_caps = spec.get("industry_soft_max") or {}
    cap = industry_caps.get(industry) if industry else None
    if cap is not None and v > cap:
        result.update(
            level=LEVEL_CRITICAL,
            message=t(
                "pg.err.industry_cap", field=field, value=f"{v:g}",
                industry=industry_name(industry), cap=f"{cap:g}", unit=unit,
            ),
            needs_confirmation=True,
        )
        return result

    # 软边界：超出常识但可能合法 → WARNING
    if v < soft_min or v > soft_max:
        result.update(
            level=LEVEL_WARNING,
            message=t("pg.err.soft_range", field=field, value=f"{v:g}",
                      lo=f"{soft_min:g}", hi=f"{soft_max:g}", unit=unit),
            needs_confirmation=False,
        )
        return result

    return result


# ── 批量校验 + 归一化 ─────────────────────────────────────────────────────

def validate_params(
    params: Dict[str, Any],
    industry: Optional[str] = None,
    history_params: Optional[Dict[str, Any]] = None,
    is_continuation: bool = False,
) -> Dict[str, Any]:
    """对一批参数做归一化 + 校验 + 历史矛盾检测。

    返回 {
        "cleaned": {field: 修正后值},
        "issues": [validate_field 结果列表],
        "contradictions": [{field, old_value, new_value, message}],
        "needs_confirmation": [field 列表],
        "has_critical": bool,
    }
    """
    cleaned: Dict[str, Any] = {}
    issues: List[Dict] = []
    contradictions: List[Dict] = []
    needs_confirm: List[str] = []

    for field, raw_value in (params or {}).items():
        # 1) 归一化
        value, norm_note = normalize_value(field, raw_value)
        if norm_note:
            issues.append({
                "field": field, "value": value, "level": LEVEL_WARNING,
                "message": norm_note, "auto_fix": None, "needs_confirmation": False,
            })

        # 2) 单字段校验
        check = validate_field(field, value, industry=industry)
        if check["level"] != LEVEL_OK:
            issues.append(check)
            if check["level"] == LEVEL_CRITICAL:
                needs_confirm.append(field)
                # 有自动修正则采用修正值
                if check["auto_fix"] is not None:
                    value = check["auto_fix"]
                else:
                    # 无修正 → 不放入 cleaned，阻断进入引擎
                    continue

        # 3) 历史矛盾检测
        if history_params and field in history_params:
            old = history_params[field]
            if _values_conflict(field, old, value, is_continuation=is_continuation):
                contradictions.append({
                    "field": field,
                    "old_value": old,
                    "new_value": value,
                    "message": t("pg.err.contradiction", field=field,
                                 new=value, old=old),
                })
                needs_confirm.append(field)

        cleaned[field] = value

    return {
        "cleaned": cleaned,
        "issues": issues,
        "contradictions": contradictions,
        "needs_confirmation": list(set(needs_confirm)),
        "has_critical": any(i["level"] == LEVEL_CRITICAL for i in issues),
    }


def _values_conflict(field: str, old: Any, new: Any,
                     is_continuation: bool = False) -> bool:
    """判断同字段新旧值是否构成矛盾。

    Direction 2（改主意权）：当 is_continuation=True 时（用户明确说「改成/改为/调整」），
    放宽阈值——大幅变更是用户主动行为，不是笔误。
    - continuation=True：比例类 >200%、数值类 >100x 才判矛盾
    - continuation=False：比例类 >50%、数值类 >10x 判矛盾（原逻辑）
    """
    if old is None or new is None:
        return False
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        if old == 0:
            return new != 0
        ratio = abs(new - old) / abs(old)
        if is_continuation:
            # 用户明确说「改成」：大幅变更是预期行为
            if field in ("variable_cost_ratio", "gross_margin", "monthly_growth_rate"):
                return ratio >= 2.0  # 比例类 >=200% 判（0.5→1.5 = ratio 2.0，不可能合法）
            return ratio >= 100      # 数值类 >=100x 判（8000→800000 明显笔误）
        else:
            # 未明确改参意图：保持严格阈值
            if field in ("variable_cost_ratio", "gross_margin", "monthly_growth_rate"):
                return ratio > 0.5
            return ratio > 10
    return str(old).strip() != str(new).strip()


# ── 派生一致性检查（引擎层调用）───────────────────────────────────────────

def check_derived_consistency(params: Dict[str, Any]) -> List[Dict[str, str]]:
    """检查派生字段与基础字段的一致性。

    返回 [{"field": ..., "message": ...}]，用于输出层高亮。
    """
    issues: List[Dict[str, str]] = []

    # 月营收 vs 客流×单价
    rev = params.get("monthly_revenue")
    traffic = params.get("daily_traffic")
    price = params.get("price_per_unit")
    if all(isinstance(x, (int, float)) and x > 0 for x in (rev, traffic, price)):
        # 延迟导入：field_model 依赖 param_guard，顶层 import 会循环。
        # 月营收公式只此一处（field_model.monthly_revenue_from_traffic），
        # 此处不再自己写 `traffic * price * 30`。
        from field_model import monthly_revenue_from_traffic, DAYS_PER_MONTH
        implied = monthly_revenue_from_traffic(traffic, price)
        if abs(implied - rev) / rev > 0.5:
            issues.append({
                "field": "monthly_revenue",
                "message": t(
                    "pg.dc.revenue_mismatch", rev=f"{rev:,.0f}",
                    traffic=traffic, price=price, days=DAYS_PER_MONTH,
                    implied=f"{implied:,.0f}",
                ),
            })

    # 变动成本率与利润结构
    vc_ratio = params.get("variable_cost_ratio")
    fixed = params.get("monthly_fixed_cost")
    if all(isinstance(x, (int, float)) for x in (rev, vc_ratio, fixed)):
        if isinstance(rev, (int, float)) and rev > 0:
            total_cost = fixed + rev * vc_ratio
            if total_cost > rev * 10:
                issues.append({
                    "field": "cost_structure",
                    "message": t("pg.dc.cost_unsustainable",
                                 total=f"{total_cost:,.0f}", rev=f"{rev:,.0f}"),
                })

    return issues


# ── 便捷接口：web_server / 引擎 用 ─────────────────────────────────────────

def guard_extracted(params: Dict[str, Any], industry: Optional[str] = None) -> Dict[str, Any]:
    """抽取后立即调用：归一化 + 基础校验。返回 (cleaned, guard_info)。"""
    res = validate_params(params, industry=industry, history_params=None)
    return res["cleaned"], res


def guard_merge(
    new_params: Dict[str, Any],
    history_params: Optional[Dict[str, Any]],
    industry: Optional[str] = None,
    is_continuation: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """会话合并前调用：检测历史矛盾 + 归一化。

    返回 (cleaned, guard_info)。cleaned 已含修正值，但 contradictions 需调用方决定是否应用。
    """
    res = validate_params(new_params, industry=industry, history_params=history_params,
                          is_continuation=is_continuation)
    return res["cleaned"], res
