"""L2 决策引擎 — 验证期决策工作台（规则层，非 LLM 排序）

职责（D7）：把引擎证据 → 互斥可执行选项 → 规则排序 → 否决/条件 → 定稿。
真相源仍是引擎（quick_scan / suggest_params 的数值来自公式回算，不靠 LLM 心算）。

铁律（D5/D6）：
- 决策输出是「选项 + 风险 + 验证实验」，**不是答案**
- 缺关键事实 → 「还不能定」+ 该补什么（G6）
- LLM 只渲染这层结构、不表达倾向（倾向词由 web_server 层在 llm 提示里禁止）
"""

import json
import os
from functools import lru_cache
from typing import Optional

import yaml

from i18n import t

_POLICY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "decision_policy.yaml",
)


@lru_cache(maxsize=1)
def _policy() -> dict:
    with open(_POLICY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def policy() -> dict:
    return _policy()


def style():
    """输出规范：禁止倾向词等（供 llm_advisor 决策模式提示词用）。"""
    return _policy().get("output", {})


# ── 输入归一 ──────────────────────────────────────────────────────────────

_DECISION_TYPES = {"turnaround", "validate_first", "go_no_go",
                   "continue_stop", "runway", "choose"}


def normalize_type(decision_type: str) -> str:
    t = (decision_type or "turnaround").strip().lower()
    return t if t in _DECISION_TYPES else "turnaround"


# ── 证据包 ────────────────────────────────────────────────────────────────

def build_evidence(scan: dict, basis: dict = None) -> dict:
    """从引擎仪表盘抽「决策证据包」：只含客观数值 + 数据基础（basis）。"""
    core = (scan or {}).get("core_metrics") or {}
    ps = (scan or {}).get("param_sources") or {}
    basis = basis or {}
    profit = core.get("monthly_profit")
    return {
        "meta": {
            "project_type": (scan or {}).get("project_type"),
            "template_mode": (scan or {}).get("template_mode"),
            "insufficient": (scan or {}).get("insufficient"),
        },
        "metrics": {
            "monthly_revenue": core.get("monthly_revenue"),
            "monthly_profit": profit,
            "period_breakeven_traffic": core.get("daily_breakeven"),
            "breakeven_revenue_monthly": core.get("breakeven_revenue_monthly"),
            "runway_months": core.get("runway_months"),
            "gross_margin": core.get("gross_margin_percent"),
        },
        "params_summary": {
            "income_covered": any(
                basis.get(f) == "user"
                for f in ("monthly_revenue", "daily_traffic", "price_per_unit")
            ),
            "cost_covered": any(
                basis.get(f) == "user"
                for f in ("variable_cost_ratio", "monthly_rent", "avg_salary", "employee_count")
            ),
        },
        "basis": basis,
        # 缺口（缺失且无已采纳假设的关键事实）
        "gaps": [f for f in ps if isinstance(ps.get(f), str) and ps[f].startswith("[缺失]")],
        "assumptions": (scan or {}).get("assumptions") or [],
    }


# ── 缺关键事实 → 还不能定（G6 否决）─────────────────────────────────────

_GAP_LABELS = {
    "variable_cost_ratio": "de.gap.variable_cost_ratio",
    "daily_traffic": "de.gap.daily_traffic",
    "price_per_unit": "de.gap.price_per_unit",
    "avg_salary": "de.gap.avg_salary",
    "employee_count": "de.gap.employee_count",
    "monthly_rent": "de.gap.monthly_rent",
    "total_investment": "de.gap.total_investment",
}


def _veto_for_type(ev: dict, decision_type: str, accepted_hypotheses: dict = None) -> Optional[dict]:
    """返回 None=可下条件结论；非 None=「还不能定」及要补什么。"""
    accepted = accepted_hypotheses or {}
    required = policy()["veto"].get("required_user_fields", {}).get(decision_type, [])
    need_cash = decision_type in ("runway", "continue_stop", "go_no_go")

    gaps = []
    for f in required:
        # 该字段不是用户给出的、也没被用户采纳为假设 → 关键缺口
        basis = ev["basis"].get(f)
        if basis != "user" and f not in accepted:
            gaps.append(f)
    # 跑道类决策需要可用现金（由总投资推导）；缺失 → 缺口
    if need_cash and ev["metrics"].get("runway_months") is None:
        gaps.append("total_investment")
    # 利润本身不可算（缺 vc）时，凡涉及利润的决策都应「还不能定」
    if ev["metrics"].get("monthly_profit") is None and "variable_cost_ratio" not in accepted:
        gaps.append("variable_cost_ratio")

    gaps = list(dict.fromkeys(gaps))
    # 回退到 field.label.* 而非裸字段名：gaps 来自 policy 的 required_user_fields，
    # 新增字段时若忘了同步 _GAP_LABELS，t(字段名) 会把 "[i18n:missing:xxx]"
    # 当成「要补什么」直接显示给用户。
    labels = [t(_GAP_LABELS.get(g) or f"field.label.{g}") for g in gaps]
    if gaps:
        return {
            "verdict": "insufficient",
            "conclusion": {
                "text": t("de.insufficient.text"),
                "conditions": [t("de.insufficient.supplement", lab=lab) for lab in labels],
            },
            "gaps": labels,
            "options": [],
            "confidence": "insufficient",
        }
    return None


# ── 选项生成（改造 suggest_params 的数值建议为可执行 op）──────────────

def _make_op(field: str, suggested, params_summary: dict, label: str = "") -> Optional[dict]:
    """把「建议改某字段为 X」转成合法 op（仅 BASE_FIELDS 候选，非法即丢弃）。"""
    from op_executor import validate_op
    op = {
        "propose": "set",
        "field": field,
        "value": suggested,
        "changes": {field: suggested},
        "label": label,
    }
    ok, reason = validate_op(op)
    if not ok:
        return None
    return op


def _options_from_suggest(suggest_data: dict, current_params: dict, base_conf: dict) -> list:
    """把 suggest_params 的 suggestions 转成 op 候选（互斥消重：同字段取一个）。"""
    from op_executor import validate_op
    suggestions = suggest_data.get("suggestions") or []
    best_by_field: dict = {}
    label_map = {
        "price_per_unit": "de.opt.price",
        "monthly_fixed_cost": "de.opt.fixed_cost",
        "avg_salary": "de.opt.avg_salary",
        "employee_count": "de.opt.employee_count",
        "variable_cost_ratio": "de.opt.variable_cost",
        "daily_traffic": "de.opt.traffic",
        "monthly_rent": "de.opt.rent",
        "other_fixed": "de.opt.other_fixed",
    }
    for s in suggestions:
        field = s.get("target_param")
        if not field or field == "monthly_fixed_cost":
            continue  # 派生字段不可直接 op（由引擎重算）
        op = {
            "propose": "set", "field": field, "value": s.get("suggested"),
            "changes": {field: s.get("suggested")},
            # 回退到 field.label.* 而非裸字段名：label_map 未覆盖的 target_param
            #（如 multiple）会让 t(field) 报 missing key，把 "[i18n:missing:xxx]"
            # 直接显示给用户。
            "label": t(label_map.get(field) or f"field.label.{field}"),
            "_delta": s.get("expected_profit_delta", 0),
        }
        from op_executor import validate_op
        ok, _ = validate_op(op)
        if not ok:
            continue
        # 同字段保留改善最大的一项（互斥：一次只动一根杠杆）
        if field not in best_by_field or (op["_delta"] or 0) > (best_by_field[field]["_delta"] or 0):
            best_by_field[field] = op
    return list(best_by_field.values())



# ── 排名（政策权重）──────────────────────────────────────────────────────

def _rank_options(ops: list, base_conf: dict) -> list:
    """得分 = 杠杆强度 × 可逆性 / (1 + 验证成本)。返回降序。"""
    rp = policy()["ranking"]
    scored = []
    for op in ops:
        fld = op.get("field")
        lw = rp["lever_weights"].get(fld, 0.5)
        rev = rp["reversibility"].get(fld, 0.5)
        vc = rp["validation_cost"].get(fld, 0.5)
        # 用引擎回算预览给杠杆强度加权（负→低分）
        delta = op.get("_delta", 0)
        strength = abs(delta or 0) / max(1, abs(base_conf.get("monthly_profit") or 1))
        score = (lw * rev / (1 + vc)) * (1 + min(strength, 2))
        scored.append((score, op))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [op for _, op in scored]


def _leverk_make_options(decision_type: str, scan: dict, basis: dict, params: dict,
                         base_conf: dict, suggest_ops: list) -> list:
    """在 suggest 基础上，补足 lever_candidates 里尚未覆盖的可执行选项（引擎回算）。

    规则：对每个候选杠杆，若当前值已知且能推进盈亏平衡，则给「一个方向的幅度」，
    用 quick_scan 预览精确的 profit_after，得到 _delta = profit_after - 当前利润。
    非法（派生/超界/无可算）一律丢弃，绝不编造。
    """
    from op_executor import preview_op, validate_op
    from tools.workflow_engine import quick_scan as _scan
    levers = policy()["decision_types"].get(decision_type, {}).get("lever_candidates", [])
    profit_now = base_conf.get("monthly_profit")
    have = {o["field"] for o in suggest_ops}
    out = []
    for cand in levers:
        fld = cand["field"]
        if fld in have:
            continue
        cur = params.get(fld)
        if not isinstance(cur, (int, float)) or cur <= 0:
            continue
        op = {"propose": "set", "field": fld, "label": cand.get("label", fld)}
        # 方向由盈亏方向决定：亏损则按「提升收入/压缩成本」推一个可行步长
        if fld == "price_per_unit":
            op["value"] = round(cur * 1.2, 2)
            op["changes"] = {fld: op["value"]}
            op["_delta"] = None
        elif fld == "daily_traffic":
            op["value"] = round(cur * 1.2, 0)
            op["changes"] = {fld: op["value"]}
            op["_delta"] = None
        elif fld == "variable_cost_ratio":
            v = max(0.05, cur * 0.85)
            op["value"] = round(v, 3)
            op["changes"] = {fld: op["value"]}
            op["_delta"] = None
        elif fld in ("avg_salary",):
            v = round(cur * 0.9, 0)
            op["value"] = v
            op["changes"] = {fld: v}
            op["_delta"] = None
        elif fld in ("employee_count",):
            if cur >= 2:
                v = cur - 1
                op["value"] = v
                op["changes"] = {fld: v}
                op["_delta"] = None
            else:
                continue
        else:
            continue
        # 引擎精确预览（不靠心算）
        prev = preview_op(op, params, _scan)
        if not prev.get("ok"):
            continue
        pa = prev.get("profit_after")
        if not isinstance(pa, (int, float)):
            continue
        op["_delta"] = round(pa - (profit_now or 0), 0)
        out.append(op)
    # 同字段去重（若 suggest 未含，此处加了）
    return out


# ── 一次性建议：先验证什么 ─────────────────────────────────────────────

def recommend_first_validation(ev: dict, accepted: dict = None) -> dict:
    """从「不确定性最大 × 影响最大」的假设里，挑一件事给最小实验设计。"""
    accepted = accepted or {}
    # 只把「直接影响利润/跑道的材料杠杆」当候选，secondary 假设（growth/季节/阶段）不抢戏
    material = {"daily_traffic", "price_per_unit", "variable_cost_ratio",
                "avg_salary", "employee_count", "monthly_rent", "other_fixed"}
    assumptions = ev.get("assumptions") or []
    candidates = []
    for a in assumptions:
        fld = a.get("field")
        if fld not in material or fld in accepted:
            continue
        lw = policy()["ranking"]["lever_weights"].get(fld, 0)
        vc = policy()["ranking"]["validation_cost"].get(fld, 0.5)
        candidates.append((lw / (1 + vc), fld, a.get("value"), a.get("kind")))

    # 兜底：若引擎没列假设（全用户），从参数里找仍最不确定的
    if not candidates:
        for fld in ("daily_traffic", "price_per_unit", "variable_cost_ratio", "avg_salary"):
            if ev["basis"].get(fld) != "user" and fld not in accepted:
                lw = policy()["ranking"]["lever_weights"].get(fld, 0.5)
                vc = policy()["ranking"]["validation_cost"].get(fld, 0.5)
                candidates.append((lw / (1 + vc), fld, None, "缺失"))

    candidates.sort(key=lambda t: t[0], reverse=True)
    if not candidates:
        return {
            "what": t("de.first_validation.none"),
            "min_experiment": "",
            "watch_metric": "",
        }
    _, fld, cur, kind = candidates[0]
    return _experiment_for(fld, cur, kind)


def _experiment_for(field: str, cur, kind: str) -> dict:
    exp = {
        "daily_traffic": ("de.exp.traffic_what", "de.exp.traffic_exp", "de.exp.traffic_watch"),
        "price_per_unit": ("de.exp.price_what", "de.exp.price_exp", "de.exp.price_watch"),
        "variable_cost_ratio": ("de.exp.vcr_what", "de.exp.vcr_exp", "de.exp.vcr_watch"),
        "avg_salary": ("de.exp.labor_what", "de.exp.labor_exp", "de.exp.labor_watch"),
        "monthly_rent": ("de.exp.rent_what", "de.exp.rent_exp", "de.exp.rent_watch"),
    }.get(field)
    if not exp:
        return {
            "what": t("de.exp.verify_what", field=field, kind=kind),
            "min_experiment": t("de.exp.verify_exp", field=field),
            "watch_metric": field,
        }
    what, exp_, watch = exp
    return {"what": t("de.exp.first", what=t(what), kind=("假设/缺失" if kind != "用户" else "推算")),
            "min_experiment": t(exp_), "watch_metric": t(watch)}


# ── 主入口 ──────────────────────────────────────────────────────────────

def decide(
    decision_type: str,
    scan: dict,
    basis: dict = None,
    current_params: dict = None,
    suggest_data: dict = None,
    user_text: str = "",
    accepted_hypotheses: dict = None,
    cashflow_data: dict = None,
) -> dict:
    """产出结构化 decision_result（决策输出规范 4.3）。

    cashflow_data：可选，来自 cashflow_projection 的 {schedule, zero_cash_month,
    max_shortfall, insufficient, gaps}——供 runway 决策把「归零月 + 累计缺口」
    并进客观结论（档 C）。
    """
    decision_type = normalize_type(decision_type)
    ev = build_evidence(scan, basis)
    base_conf = {"monthly_profit": ev["metrics"].get("monthly_profit")}
    current_params = current_params or {}

    # 1) 否决：缺关键事实 → 还不能定
    veto = _veto_for_type(ev, decision_type, accepted_hypotheses)
    if veto:
        return {"type": decision_type, **veto}

    # 2) 选项集：来自 suggest_params 的数值建议 + 杠杆候选补足（均引擎回算口径）
    ops = []
    if suggest_data and suggest_data.get("suggestions"):
        ops = _options_from_suggest(suggest_data, current_params, base_conf)
    if decision_type in ("turnaround", "runway"):
        extra = _leverk_make_options(decision_type, scan, basis, current_params,
                                     base_conf, ops)
        ops = ops + extra
    if ops:
        ops = _rank_options(ops, base_conf)

    cap = policy()["decision_types"].get(decision_type, {}).get("option_cap", 4)
    ops = ops[:cap]

    options = []
    for i, op in enumerate(ops):
        if op.get("_delta") is None:
            continue
        options.append({
            "label": op.get("label", op.get("field")),
            "changes": op.get("changes"),
            "_delta": op.get("_delta"),
            "validation_cost": policy()["ranking"]["validation_cost"].get(op.get("field"), 0.5),
            "reversible": policy()["ranking"]["reversibility"].get(op.get("field"), 0.5) >= 0.6,
        })

    # 3) 先验证一件事（一次性建议）
    first_v = recommend_first_validation(ev, accepted_hypotheses)

    # 4) 定稿（纯客观陈述）：先判利润是否可算，再判是否全为用户事实
    profit_known = ev["metrics"].get("monthly_profit") is not None
    vc_is_user = ev["basis"].get("variable_cost_ratio") == "user"
    labor_is_user = ev["basis"].get("avg_salary") == "user" and ev["basis"].get("employee_count") == "user"
    rev_is_user = ev["basis"].get("monthly_revenue") == "user" or (
        ev["basis"].get("daily_traffic") == "user" and ev["basis"].get("price_per_unit") == "user")
    all_core_user = (vc_is_user and rev_is_user and labor_is_user
                     and ev["basis"].get("total_investment") == "user")
    confidence = (
        "full_user" if profit_known and all_core_user
        else "with_hypothesis" if profit_known
        else "insufficient"
    )
    # 「开不开 / 继续还是关」类：给客观距离与跑道，不带倾向（D5/D6）
    conclusion = {}
    if decision_type in ("go_no_go", "continue_stop") and profit_known:
        profit = ev["metrics"].get("monthly_profit")
        rw = ev["metrics"].get("runway_months")
        dbe = ev["metrics"].get("period_breakeven_traffic")
        traffic = current_params.get("daily_traffic")
        parts = []
        if profit is not None:
            parts.append(t("de.decide.profit", profit=f"{profit:,.0f}"))
        if dbe is not None and isinstance(traffic, (int, float)):
            gap = dbe - traffic
            # D8：用用户口中的量词（碗/杯/份），不要写死「杯/天」。
            _tu = current_params.get("_traffic_unit") or "杯"
            state = t("de.decide.above_be") if gap <= 0 else t("de.decide.be_gap", gap=f"{gap:.0f}", unit=_tu)
            parts.append(state)
        if rw is not None and isinstance(rw, (int, float)):
            parts.append(t("de.decide.runway", rw=f"{rw:.1f}"))
        elif rw == "无限":
            parts.append(t("de.decide.runway_infinite"))
        conclusion["text"] = "；".join(parts) + "。"
        conclusion["conditions"] = [
            t("de.decide.based_on_params")
        ]
    elif decision_type in ("go_no_go", "continue_stop"):
        conclusion["text"] = t("de.decide.incomplete")
        conclusion["conditions"] = [t("de.decide.fill_gaps")]
    elif decision_type == "runway":
        # 档 C：消费现金流明细（归零月 + 累计缺口）——客观陈述，无倾向
        cf = cashflow_data or {}
        zc = cf.get("zero_cash_month")
        ms = cf.get("max_shortfall")
        if cf.get("insufficient"):
            conclusion["text"] = t("de.decide.cf_incomplete")
            conclusion["conditions"] = [t("de.insufficient.supplement", lab=g) for g in (cf.get("gaps") or [])]
        elif zc is not None:
            conclusion["text"] = (
                t("de.decide.cf_exhaust", zc=zc)
                + (t("de.decide.cf_shortfall", ms=f"{ms:,.0f}") if ms is not None else "")
                + "。"
            )
            conclusion["conditions"] = [
                t("de.decide.cf_basis"),
            ]
        else:
            conclusion["text"] = t("de.decide.cf_survives")
            conclusion["conditions"] = [t("de.decide.cf_changes")]
    return {
        "type": decision_type,
        "confidence": confidence,
        "conclusion": conclusion or None,
        "options": options,
        "recommended_first_validation": first_v,
        "not_recommended": [],
        "gaps": ev["gaps"],
    }


# ── 交互渲染（web_server / LLM 协作前的人读视图）────────────────────────

def render_decision(decision_result: dict) -> str:
    """把结构化 decision_result 渲染成 Markdown。LLM 可在此基础上讲人话，规则层保证结构。"""
    from op_executor import parse_apply_command  # noqa: F401 防未使用
    lines = []
    dtype = decision_result["type"]
    if decision_result.get("confidence") == "insufficient":
        lines.append(t("de.render.undecided"))
        lines.append("")
        lines.append(decision_result["conclusion"]["text"])
        for c in decision_result["conclusion"].get("conditions", []):
            lines.append(f"- {c}")
        return "\n".join(lines)

    meta = decision_result.get("_meta") or {}
    conclusion = decision_result.get("conclusion")
    if conclusion and conclusion.get("text"):
        lines.append(t("de.render.objective"))
        lines.append(conclusion["text"])
        for c in conclusion.get("conditions", []):
            lines.append(f"  - {c}")
        lines.append("")
    fv = decision_result.get("recommended_first_validation") or {}
    # validate_first 类：验证建议是主输出，置顶呈现
    if dtype == "validate_first" and fv and fv.get("what"):
        lines.append(t("de.render.validate_first"))
        lines.append(f"- {fv.get('what')}")
        if fv.get("min_experiment"):
            lines.append(t("de.render.min_exp", exp=fv.get("min_experiment")))
        if fv.get("watch_metric"):
            lines.append(t("de.render.watch", metric=fv.get("watch_metric")))
        lines.append("")
    if decision_result["options"] and dtype != "validate_first":
        lines.append(t("de.render.candidates"))
        lines.append(t("de.render.mutually_exclusive"))
        lines.append("")
        for i, o in enumerate(decision_result["options"]):
            tag = "ABCD"[i]
            chg = t("llm.brief.sep").join(f"{k}={v}" for k, v in o["changes"].items())
            rev = t("de.render.reversible") if o.get("reversible") else t("de.render.hard_to_revert")
            lines.append(
                t("de.render.option_line", tag=tag, label=o['label'], chg=chg,
                  delta=f"{o.get('_delta', 0):+,g}", rev=rev))
        lines.append("")
    fv = decision_result.get("recommended_first_validation") or {}
    # validate_first 已是主输出（置顶🔬），底部不重复
    if fv and fv.get("what") and dtype != "validate_first":
        lines.append(t("de.render.one_shot"))
        lines.append(t("de.render.validate_line", what=fv.get("what")))
        if fv.get("min_experiment"):
            lines.append(t("de.render.min_exp", exp=fv.get("min_experiment")))
        if fv.get("watch_metric"):
            lines.append(t("de.render.watch", metric=fv.get("watch_metric")))
    return "\n".join(lines)


def forbidden_tone_scan(text: str) -> list:
    """扫描 LLM 输出中的禁止倾向词（D5 硬守卫，供 web_server 检测并打回）。"""
    bad = [w for w in style().get("forbidden_tone_words", []) if w in text]
    return bad
