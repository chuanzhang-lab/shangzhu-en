"""Phase 3 · 跨轮会话状态（唯一真相源）

问题根因：原系统「规则引擎」与「LLM agent」是两条互不共享状态的路径——
用户第 2 轮补充的参数不会被第 1 轮的分析看到，于是反复报「信息不全」，
而自由 agent 又因为拿不到项目上下文，凭空编造（如「成都冒菜店 / 7.5 万」）。

本模块把 SessionState 设为**唯一真相源**：
- 以 thread_id 为键，累积每轮抽取出的结构化参数（params）与行业（industry）。
- 每轮新输入先 merge 到旧状态，再交给引擎计算，结果回写。
- 提供 `is_continuation`（续算意图识别）与 `get_session_context`（给 LLM 接地），
  使「再算一下 / 改成 / 补充」强制走 merge，且 chitchat 也能拿到真实项目背景。

设计为纯函数 + 进程内字典，不引入任何新依赖，也不碰文件/网络。
线程安全：所有对 _SESSIONS 的读写在同一把 RLock 下完成，避免并发 /chat 竞态。
"""

import threading
import time
from i18n import t
from typing import Dict, List, Optional

# thread_id -> 会话状态
_SESSIONS: Dict[str, dict] = {}
_LOCK = threading.RLock()

# 会话 TTL（秒）与最大数量，防止长期运行内存无限增长
_SESSION_TTL_SECONDS = 3600 * 4  # 4 小时无访问则过期
_MAX_SESSIONS = 1000

# raw_text 累积上限（字符），防止多轮超长输入撑爆内存
_MAX_RAW_TEXT_CHARS = 8000


def _cleanup_sessions(reserve_one: bool = False) -> None:
    """清理过期会话；若总数超限，按最后访问时间淘汰最旧的。

    调用方必须已持有 _LOCK。
    """
    now = time.time()
    # 先清过期
    expired = [
        tid for tid, st in _SESSIONS.items()
        if now - st.get("_last_access", 0) > _SESSION_TTL_SECONDS
    ]
    for tid in expired:
        del _SESSIONS[tid]
    # 再按数量淘汰（如需预留一个位置给新 session，则目标数量减 1）
    target = _MAX_SESSIONS - 1 if reserve_one else _MAX_SESSIONS
    if len(_SESSIONS) > target:
        sorted_items = sorted(
            _SESSIONS.items(),
            key=lambda item: item[1].get("_last_access", 0),
        )
        overflow = len(sorted_items) - target
        for tid, _ in sorted_items[:overflow]:
            del _SESSIONS[tid]


# 字段 → 人类可读标签（用于给 LLM 接地的上下文文本）
_FIELD_LABELS = {
    "industry": "ss.label.industry",
    "total_investment": "ss.label.total_investment",
    "monthly_rent": "ss.label.monthly_rent",
    "monthly_revenue": "ss.label.monthly_revenue",
    "monthly_expense": "ss.label.monthly_expense",
    "price_per_unit": "ss.label.price_per_unit",
    "daily_traffic": "ss.label.daily_traffic",
    "employee_count": "ss.label.employee_count",
    "avg_salary": "ss.label.avg_salary",
    "variable_cost_rate": "ss.label.variable_cost_rate",
    "variable_cost_ratio": "ss.label.variable_cost_ratio",
    "founder_count": "ss.label.founder_count",
    "city": "ss.label.city",
}


# ── 参数语义分组（用户心智模型的代码映射）──────────────────────────────────
# Direction 1：让 LLM 理解参数之间的维度关系，而非面对一堆散落的 key-value。
PARAM_GROUPS = {
    "ss.group.revenue": ["monthly_revenue", "daily_traffic", "price_per_unit"],
    "ss.group.cost": [
        "monthly_rent", "employee_count", "avg_salary",
        "variable_cost_ratio", "variable_cost_rate", "unit_variable_cost",
        "monthly_expense", "utilities", "packaging", "commission", "other_fixed",
    ],
    "ss.group.runway": ["total_investment"],
    "ss.group.positioning": ["industry", "city", "stage"],
}

# 反向索引：field → group name（O(1) 查找）
_FIELD_TO_GROUP: Dict[str, str] = {}
for _grp, _fields in PARAM_GROUPS.items():
    for _f in _fields:
        _FIELD_TO_GROUP[_f] = _grp


# ── 关注字段推断（Direction 5：长会话注意力）────────────────────────────────
# 从用户文本推断当前关注的参数字段，供 to_llm_view 做相关性过滤。
_FIELD_KEYWORDS = {
    "客流": "daily_traffic", "卖": "daily_traffic", "每天": "daily_traffic",
    "日均": "daily_traffic", "日售": "daily_traffic", "杯": "daily_traffic",
    "租金": "monthly_rent", "房租": "monthly_rent", "月租": "monthly_rent", "铺租": "monthly_rent",
    "客单价": "price_per_unit", "单价": "price_per_unit", "售价": "price_per_unit",
    "人工": "employee_count", "员工": "employee_count", "人数": "employee_count",
    "薪资": "avg_salary", "工资": "avg_salary", "月薪": "avg_salary",
    "变动成本": "variable_cost_ratio", "成本率": "variable_cost_ratio",
    "投资": "total_investment", "投入": "total_investment", "启动资金": "total_investment",
    "营收": "monthly_revenue", "收入": "monthly_revenue", "月收": "monthly_revenue", "流水": "monthly_revenue",
    "利润": "monthly_profit", "盈利": "monthly_profit", "亏损": "monthly_profit",
    "水电": "utilities", "包装": "packaging", "提成": "commission",
}


def infer_focus_fields(text: str) -> Optional[List[str]]:
    """从用户文本推断关注的参数字段。返回 None 表示不过滤（全量输出）。"""
    if not text:
        return None
    fields = []
    for keyword, field in _FIELD_KEYWORDS.items():
        if keyword in text:
            fields.append(field)
    return list(set(fields)) if fields else None


def _new_state() -> dict:
    return {
        "params": {},           # A 类 + C 类字段
        "user_overrides": {},   # B 类字段用户覆盖（临时，依赖变化自动清除）
        "industry": None,
        "raw_text": "",
        "turn": 0,
        "_last_access": time.time(),
        # P0：用户确认采纳的行业/LLM 候选假设（basis=hypothesis 的来源登记）
        #   形如 {"avg_salary": {"value": 7000, "industry": "餐饮"}}
        "_accepted_hypotheses": {},
        # Direction 4：写入版本号（每次 apply_turn_guarded +1，可观测 + 未来 CAS 基础）
        "_version": 0,
        # 主持人模式：LLM 推荐/追问计数器（用于节奏控制）
        "_advise_meta": {
            "recommendation_count": 0,
            "question_count": 0,
        },
    }


def get_state(thread_id: str) -> dict:
    """取得（或初始化）某 thread 的会话状态。"""
    with _LOCK:
        # 为新 session 预留一个位置，避免创建后瞬间超限
        _cleanup_sessions(reserve_one=thread_id not in _SESSIONS)
        if thread_id not in _SESSIONS:
            _SESSIONS[thread_id] = _new_state()
        _SESSIONS[thread_id]["_last_access"] = time.time()
        return _SESSIONS[thread_id]


def reset_state(thread_id: str) -> None:
    """清空某 thread 的会话状态（用户说「重新开始 / 新项目 / 清空」时调用）。"""
    with _LOCK:
        _cleanup_sessions(reserve_one=False)
        _SESSIONS[thread_id] = _new_state()


def hydrate_session(thread_id: str, persisted_params: dict, industry: Optional[str] = None) -> dict:
    """从持久化 store 把参数灌回 SessionState（锁内操作，防竞态）。

    在 web_server 请求入口处调用，确保 hydrate 写与 apply_turn_guarded 写互斥。
    仅写入业务字段（过滤下划线开头的内部键）。
    """
    with _LOCK:
        st = get_state(thread_id)
        if persisted_params:
            for k, v in persisted_params.items():
                if not k.startswith("_") and v is not None:
                    st["params"][k] = v
        if industry:
            st["industry"] = industry
        return st


def merge_params(old: dict, new: dict) -> dict:
    """把新一轮抽出的参数合并进旧状态。

    规则：新值非 None 即覆盖旧值（用户最新输入优先）；旧值中未在新轮出现
    的字段保留。这样「第 2 轮补人工」不会丢掉「第 1 轮的投资/租金」。

    F1：变动成本率的双键一致性——若本轮带来 `variable_cost_rate`（百分比），
    则同步 ratio = rate/100，避免引擎（优先消费 ratio）与 rate 脱节（旧 ratio 残留
    或 ratio 缺失都覆盖）。
    """
    merged = dict(old or {})
    for k, v in (new or {}).items():
        if v is not None:
            merged[k] = v
    # F1 修复（2026-09-27）：只认「本轮新输入」带来的 rate，绝不用历史残留的旧 rate
    # 反推 ratio。旧实现读 merged.get("variable_cost_rate")，一旦会话里残留旧 rate=35，
    # 「变动成本率改为60」（只带 ratio、不带 rate）也会被 rate/100=0.35 覆盖回退，
    # 表现为改参跳回旧值 + 前端误报「未采纳」。
    rate = (new or {}).get("variable_cost_rate")
    if isinstance(rate, (int, float)) and 0 < rate < 100:
        merged["variable_cost_ratio"] = round(rate / 100, 4)
    elif "variable_cost_ratio" in (new or {}) and (new or {}).get("variable_cost_ratio") is not None:
        # 本轮明确给了 ratio：清掉可能残留的旧 rate，避免它在持久化里继续传染、
        # 并污染 LLM 接地上下文（_fmt_value 会把 rate=35 显示成 3500%）。
        merged.pop("variable_cost_rate", None)
    return merged


def merge_params_guarded(old: dict, new: dict, industry: str = "",
                         is_continuation: bool = False) -> tuple[dict, dict]:
    """带守门的合并：先检测历史矛盾，再合并。

    返回 (merged, guard_info)。guard_info 含 contradictions / needs_confirmation，
    调用方可据此在响应中高亮「待确认」而非静默覆盖。
    """
    from param_guard import guard_merge

    # 分离元数据（_guard 等）与业务参数
    new_meta = {k: v for k, v in (new or {}).items() if k.startswith("_")}
    new_biz = {k: v for k, v in (new or {}).items() if not k.startswith("_")}

    cleaned, guard = guard_merge(new_biz, old or {}, industry=industry or None,
                                 is_continuation=is_continuation)

    # 抽取时已拦截的「6000%」等信号存在 new_meta["_guard"]，须并入最终 guard，
    # 否则清洗后的干净值会让「请确认」信号在合并时丢失。
    extract_guard = new_meta.get("_guard") or {}
    if extract_guard:
        guard["issues"] = list(extract_guard.get("issues", [])) + list(guard.get("issues", []))
        guard["contradictions"] = (
            list(extract_guard.get("contradictions", []))
            + list(guard.get("contradictions", []))
        )
        guard["needs_confirmation"] = list(set(
            list(extract_guard.get("needs_confirmation", []))
            + list(guard.get("needs_confirmation", []))
        ))
        guard["has_critical"] = bool(
            extract_guard.get("has_critical") or guard.get("has_critical")
        )

    merged = merge_params(old or {}, cleaned)
    merged.update(new_meta)
    return merged, guard


def _truncate_raw(text: str) -> str:
    """限制 raw_text 长度，保留尾部（最近上下文更有用）。"""
    if not text:
        return ""
    if len(text) <= _MAX_RAW_TEXT_CHARS:
        return text
    return text[-_MAX_RAW_TEXT_CHARS:]


def apply_turn(thread_id: str, new_params: dict, new_raw: str = "",
               industry: Optional[str] = None) -> dict:
    """应用一轮用户输入到会话状态（merge + 累积原文 + 计轮次）。

    返回更新后的状态 dict。
    """
    with _LOCK:
        st = get_state(thread_id)
        st["params"] = merge_params(st["params"], new_params)
        if industry:
            st["industry"] = industry
        if new_raw:
            st["raw_text"] = _truncate_raw((st["raw_text"] + " " + new_raw).strip())
        st["turn"] += 1
        st["_version"] = st.get("_version", 0) + 1
        return st


def apply_turn_guarded(thread_id: str, new_params: dict, new_raw: str = "",
                       industry: Optional[str] = None) -> tuple[dict, dict]:
    """带守门的 apply_turn：先检测矛盾，再合并，guard_info 挂到 state 供输出层读取。

    返回 (state, guard_info)。同时记录本轮变更（_last_changes），供 LLM brief 用，
    让 LLM 基于「变化」讲解，而不是去翻历史原文（这是上一轮「对账幻觉」的根因）。

    B 类字段覆盖失效：当 A 类依赖字段变化时，自动清掉旧 B 类用户覆盖，回退公式推算。
    """
    with _LOCK:
        st = get_state(thread_id)
        old_params = dict(st.get("params") or {})

        from field_model import OVERRIDABLE_FIELDS, clear_stale_overrides

        # ── 第一步：所有字段统一走守门（归一化 + 校验 + 矛盾检测）──
        # B 类字段（如 variable_cost_ratio=6000）必须也经过 validate_params 归一化
        # 成 60 → CRITICAL 自动修正 0.6，否则 6000 会绕过检测直接污染 params。
        # Direction 2：检测续算意图，传递给守门层放宽矛盾阈值
        continuation = is_continuation(new_raw)
        merged, guard = merge_params_guarded(
            st["params"], new_params, industry=industry or st.get("industry") or "",
            is_continuation=continuation
        )

        # ── 第二步：从已验证的 merged 中分离 B 类字段 ──
        old_overrides = dict(st.get("user_overrides") or {})
        # 新轮带来的 B 类字段（已归一化/修正）→ 加入 user_overrides
        new_b_fields = {k: v for k, v in merged.items()
                        if k in OVERRIDABLE_FIELDS and v is not None}
        if new_b_fields:
            old_overrides.update(new_b_fields)

        # 依赖变化 → 清掉失效覆盖
        non_b_params = {k: v for k, v in (new_params or {}).items()
                        if k not in OVERRIDABLE_FIELDS}
        updated_overrides = clear_stale_overrides(old_params, non_b_params, old_overrides)
        st["user_overrides"] = updated_overrides

        # B 类字段被清除时，从 merged 中移除旧值
        # （merged 是刚算出的新 params，若不清除失效 B 类字段会残留为假 override）
        removed_b_fields = set(old_overrides.keys()) - set(updated_overrides.keys())
        for bf in removed_b_fields:
            merged.pop(bf, None)

        # B 类字段同时保留在 params 中（历史/展示/推算输入都需要）
        st["params"] = merged
        if industry:
            st["industry"] = industry
        if new_raw:
            st["raw_text"] = _truncate_raw((st["raw_text"] + " " + new_raw).strip())
        st["turn"] += 1
        st["_version"] = st.get("_version", 0) + 1
        # 本轮变更：只记业务字段的 diff（元数据/守门信息不进 LLM 视野）
        st["_last_changes"] = _compute_param_diff(old_params, merged)
        # 把本回合的守门信息挂到 state，web_server 可读取并前置展示
        if guard.get("contradictions") or guard.get("needs_confirmation"):
            st["_pending_guard"] = guard
        else:
            st.pop("_pending_guard", None)
        return st, guard


def _compute_param_diff(old: dict, new: dict) -> dict:
    """计算业务参数的 diff，用于 LLM brief 的「本轮变更」段。

    返回 {field: {"from": old, "to": new, "kind": "added|changed|removed"}}，
    元数据（下划线开头的 key）不进 diff。
    """
    diff: dict = {}
    old = old or {}
    new = new or {}
    for k, v in new.items():
        if k.startswith("_"):
            continue
        old_v = old.get(k)
        if k not in old:
            diff[k] = {"from": None, "to": v, "kind": "added"}
        elif old_v != v:
            diff[k] = {"from": old_v, "to": v, "kind": "changed"}
    for k in old:
        if k.startswith("_"):
            continue
        if k not in new:
            diff[k] = {"from": old[k], "to": None, "kind": "removed"}
    return diff


def clear_last_changes(thread_id: str) -> None:
    """应用 ops 后清空本轮变更标记（避免下次仍按旧 diff 讲）。"""
    with _LOCK:
        if thread_id in _SESSIONS:
            _SESSIONS[thread_id].pop("_last_changes", None)


def pop_pending_ops(thread_id: str) -> Optional[list]:
    """原子地取出并清空 _pending_ops（锁内操作，防双击重复应用）。"""
    with _LOCK:
        if thread_id not in _SESSIONS:
            return None
        return _SESSIONS[thread_id].pop("_pending_ops", None)


def set_pending_ops(thread_id: str, ops: list) -> None:
    """原子地写入 _pending_ops（锁内操作，避免与其他写操作竞争）。"""
    with _LOCK:
        if thread_id in _SESSIONS:
            _SESSIONS[thread_id]["_pending_ops"] = ops


def get_params_version(thread_id: str) -> int:
    """锁内读取写入版本号，供按需解读做 stale 校验。"""
    with _LOCK:
        st = _SESSIONS.get(thread_id)
        return int(st.get("_version", 0)) if st else 0


def set_last_analysis(thread_id: str, payload: dict) -> None:
    """保存本轮结构化分析快照，供 POST /analysis/advice 按需解读。"""
    import copy
    with _LOCK:
        st = get_state(thread_id)
        st["_last_analysis"] = copy.deepcopy(payload or {})


def get_last_analysis(thread_id: str) -> Optional[dict]:
    """读取本轮分析快照（深拷贝）；无则 None。"""
    import copy
    with _LOCK:
        st = _SESSIONS.get(thread_id)
        if not st:
            return None
        snap = st.get("_last_analysis")
        return copy.deepcopy(snap) if snap else None

# ── 主持人模式计数器（锁内读-改-写，审查修复 F4）───────────────────────
# 原实现在 web_server 锁外直接 st["_advise_meta"][field] += 1，并发轮次会丢计数。

def get_advise_meta(thread_id: str) -> dict:
    """锁内读取 _advise_meta 快照（浅拷贝），供锁外只读使用。"""
    with _LOCK:
        st = _SESSIONS.get(thread_id)
        return dict(st.get("_advise_meta", {})) if st else {}

def reset_advise_count(thread_id: str, field: str = "recommendation_count") -> None:
    """锁内把指定计数器清零。"""
    with _LOCK:
        st = _SESSIONS.get(thread_id)
        if st is not None:
            st.setdefault("_advise_meta", {})[field] = 0

def incr_advise_count(thread_id: str, field: str) -> None:
    """锁内自增指定计数器（原子读-改-写）。"""
    with _LOCK:
        st = _SESSIONS.get(thread_id)
        if st is not None:
            meta = st.setdefault("_advise_meta", {})
            meta[field] = meta.get(field, 0) + 1

def get_turn_number(thread_id: str) -> int:
    """锁内读取轮次号。"""
    with _LOCK:
        st = _SESSIONS.get(thread_id)
        return st.get("turn", 0) if st else 0

def get_biz_snapshot(thread_id: str) -> dict:
    """锁内一次性读取业务快照（params / industry / _pending_guard）。

    审查修复 F4：web_server 此前持有 get_state 返回的共享引用在锁外
    分次读 st.get("params") / st.get("industry")，两次读之间可能被并发
    apply_turn 写入，导致跨字段不一致。本函数在锁内一次取齐，返回深拷贝。
    """
    with _LOCK:
        st = _SESSIONS.get(thread_id)
        if st is None:
            return {"params": {}, "industry": None, "pending_guard": {}}
        import copy
        return {
            "params": copy.deepcopy(st.get("params") or {}),
            "industry": st.get("industry"),
            "pending_guard": dict(st.get("_pending_guard") or {}),
        }


# ── P0 数据基础层：已采纳假设（basis=hypothesis）登记 ─────────────────────

def record_accepted_hypothesis(thread_id: str, field: str, value, industry: str = "") -> None:
    """用户经「应用X」确认某行业/LLM 候选假设后登记，供决策层区分「用户事实 vs 假设」。

    写入 _accepted_hypotheses[field] = {"value":..., "industry":...}。
    """
    with _LOCK:
        st = get_state(thread_id)
        st.setdefault("_accepted_hypotheses", {})[field] = {
            "value": value, "industry": industry or st.get("industry") or "",
        }


def get_accepted_hypotheses(thread_id: str) -> dict:
    """返回 {field: {value, industry}}（无则空 dict）。"""
    with _LOCK:
        st = get_state(thread_id)
        return dict(st.get("_accepted_hypotheses") or {})


def to_llm_view(thread_id: str, focus_fields: Optional[List[str]] = None) -> dict:
    """给 LLM 的清洁会话视图：去掉 raw_text 与内部字段，含本轮变更。

    Direction 1 增强：按语义分组输出参数（grouped_params），让 LLM 理解维度关系。
    Direction 3 增强：输出已采纳假设（accepted_hypotheses），非参数真相维。
    Direction 5 增强：focus_fields 不为 None 时，只输出相关参数 + 核心派生参数，
    其余压缩为摘要字符串，减少长会话中无关参数对 LLM 注意力的干扰。

    关键：raw_text 含历史用户原文（如「人工3500*2」），喂给 LLM 会引发
    「引擎还在用旧值」式对账幻觉。只给 LLM 当前 params / industry / turn /
    本轮变更，让它基于「当前真实状态」讲话。
    """
    # 核心派生参数（引擎结果，无论 focus 与否都必须给 LLM）
    _CORE_FIELDS = {
        "monthly_revenue", "monthly_fixed_cost", "monthly_profit",
        "runway_months", "daily_breakeven", "monthly_labor",
        "monthly_labor_cash", "labor_burden_rate",
    }
    with _LOCK:
        st = get_state(thread_id)
        raw_params = {k: v for k, v in (st.get("params") or {}).items()
                      if not k.startswith("_")}
        hypotheses = dict(st.get("_accepted_hypotheses") or {})

    # Direction 5：相关性过滤
    if focus_fields:
        relevant = set(focus_fields) | _CORE_FIELDS
        focused = {k: v for k, v in raw_params.items() if k in relevant}
        rest = {k: v for k, v in raw_params.items() if k not in relevant}
        params_for_view = focused
        params_summary = (
            t("ss.view.other_summary") + "、".join(
                f"{k}={v}" for k, v in rest.items()
            )
        ) if rest else ""
    else:
        params_for_view = raw_params
        params_summary = ""

    # Direction 1：按语义分组
    grouped = {}
    all_grouped_fields = set()
    for group_key, fields in PARAM_GROUPS.items():
        gp = {f: params_for_view[f] for f in fields if f in params_for_view}
        if gp:
            grouped[t(group_key)] = gp
            all_grouped_fields.update(gp.keys())
    orphan = {k: v for k, v in params_for_view.items()
              if k not in all_grouped_fields}
    if orphan:
        grouped[t("ss.view.other_group")] = orphan

    view = {
        "params": params_for_view,
        "grouped_params": grouped,
        "params_summary": params_summary,
        "accepted_hypotheses": hypotheses,
        "industry": st.get("industry"),
        "turn": st.get("turn", 0),
        "version": st.get("_version", 0),
        "last_changes": st.get("_last_changes", {}),
    }
    return view


def session_stats() -> dict:
    """可观测性：当前进程内会话数量与配置上限（供 /health 使用）。"""
    with _LOCK:
        _cleanup_sessions(reserve_one=False)
        return {
            "active_sessions": len(_SESSIONS),
            "max_sessions": _MAX_SESSIONS,
            "ttl_seconds": _SESSION_TTL_SECONDS,
        }


# ─── 续算意图 / 重置命令识别 ──────────────────────────────────────────────

_CONTINUATION_HINTS = [
    "再算", "重新算", "重算", "再分析", "重新分析", "重新评估",
    "改成", "改为", "调整", "更新", "加上", "补上", "补充", "加上去",
    "改一下", "调一下", "更新一下", "换一下", "改成", "把",
    "那如果", "如果", "假如", "假设", "现在", "当前", "我的项目",
]

_RESET_HINTS = [
    "重新开始", "重新来", "新项目", "开新项目", "清空", "重置", "重开",
    "换个项目", "不要之前", "忘掉之前", "换个方向",
]


def is_continuation(text: str) -> bool:
    """识别「续算意图」——用户是在既有项目上补充/修改，而非开启全新话题。

    命中任一续算线索即返回 True。引擎据此**强制走 merge**，不重新抽取、
    不进 chitchat，从而消除「信息不全」反复误报。
    """
    if not text:
        return False
    t = text.lower()
    return any(h in t for h in _CONTINUATION_HINTS)


def is_reset_command(text: str) -> bool:
    """识别用户想要清空当前会话、开启新项目。"""
    if not text:
        return False
    t = text.lower()
    return any(h in t for h in _RESET_HINTS)


# ─── 给 LLM 接地的项目上下文 ──────────────────────────────────────────────

def _fmt_value(field: str, value) -> str:
    """把参数值格式化为可读文本（大数用千分位 + 元）。"""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return t("ss.fmt.yes") if value else t("ss.fmt.no")
    if isinstance(value, (int, float)):
        # 比率类（0~1 或 0~100）单独处理
        if field in ("variable_cost_rate", "variable_cost_ratio"):
            return f"{float(value) * 100:.0f}%"
        if abs(value) >= 10000:
            return f"{value:,.0f}{t('ss.fmt.yuan')}"
        if abs(value) >= 1000:
            return f"{value:,.0f}"
        return str(value)
    return str(value)


def get_session_context(thread_id: str) -> str:
    """生成给 LLM 接地的项目上下文文本。

    仅包含真实已知的字段（来自历史各轮 merge 的结果），不编造任何数字。
    无上下文时返回空字符串（调用方据此决定是否注入）。
    """
    with _LOCK:
        st = get_state(thread_id)
        params = dict(st.get("params", {}))
        industry = st.get("industry")

    if not params and not industry:
        return ""

    parts: list = []
    if industry:
        parts.append(f"{t('ss.ctx.industry')}{industry}")

    for field, label_key in _FIELD_LABELS.items():
        if field == "industry":
            continue
        if field in params and params[field] not in (None, "", 0):
            v = _fmt_value(field, params[field])
            if v:
                parts.append(f"{t(label_key)}：{v}")

    if not parts:
        return ""
    return "；".join(parts)
