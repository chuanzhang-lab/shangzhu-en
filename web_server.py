"""
创业者工作台 — 本地 Web 启动入口
独立 FastAPI 服务（本地工作台，仅引擎层 + Engine Steward）

启动: ./.venv/bin/python web_server.py   （默认端口 8081）
访问: http://localhost:8081

架构：
  用户输入
    ↓
  意图路由（router，纯规则，0 LLM）
    ↓
  ├─ 业务意图 → 参数提取（正则）→ 调工具 → 模板输出
  └─ chitchat → 走 LLM
"""

import os
import sys

# ── 解释器自修复：仅当「直接运行」(python web_server.py) 且当前解释器不是项目
#    .venv 时，自动以 .venv 重新执行，避免用系统/其他 python 在 import 处就抛
#    ModuleNotFoundError。
# 关键：本守卫**只能**在 __name__ == "__main__" 时执行。若 web_server 被当作
#    模块 import（如 TestClient 测试、p49 测试），绝不能触发 execv——否则会把
#    测试进程劫持成 uvicorn 服务并永久卡死（这就是 P4-9 守护测试一度被回退的根因）。
if __name__ == "__main__":
    _VENV_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
    if (
        os.path.isfile(_VENV_PY)
        and os.path.realpath(sys.executable) != os.path.realpath(_VENV_PY)
    ):
        print(
            f"[web_server] 检测到非项目 .venv 解释器，自动切换到 .venv 运行：{_VENV_PY}",
            file=sys.stderr,
        )
        os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__), *sys.argv[1:]])
        sys.exit(1)  # 不会到达（os.execv 已替换进程）

import json
import re
import uuid as _uuid
import logging
import logging.handlers
import asyncio
import time
from typing import Any, Dict, AsyncGenerator, Optional
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# 路径设置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(SCRIPT_DIR, "src")
os.environ["SHANGZHU_WORKSPACE_PATH"] = SCRIPT_DIR

# 把 src 加入路径以支持相对导入
if SRC_DIR not in __import__("sys").path:
    __import__("sys").path.insert(0, SRC_DIR)

# 路由模块（不调 LLM）
from router.intent import detect_intent
from router.param_extractor import extract_params
from router.formatter import format_response

# Phase 3：跨轮会话状态（唯一真相源）+ 续算/重置识别 + LLM 接地上下文
from session_state import (
    apply_turn,
    apply_turn_guarded,
    reset_state,
    is_reset_command,
    get_session_context,
    get_state,
    hydrate_session,
    session_stats,
    to_llm_view,
    infer_focus_fields,
    get_advise_meta,
    reset_advise_count,
    incr_advise_count,
    get_turn_number,
    get_biz_snapshot,
    get_params_version,
    set_last_analysis,
    get_last_analysis,
)

# 工具（直调，不走 LLM）
from tools.workflow_engine import (
    quick_scan as quick_scan_tool,
    trend_projection as trend_tool,
    compare_scenarios as compare_tool,
    cashflow_projection as cashflow_tool,
)
from tools.param_advisor import suggest_params as suggest_params_tool
from tools.market_research import search_industry_benchmarks as benchmark_tool
from tools.cost_attribution import _build_cost_attribution
from tools.financial_calculator import _calc_sensitivity
from tools.report_generator import (
    generate_financial_report as report_pdf_tool,
    generate_financial_excel as report_excel_tool,
    build_report_markdown,
    build_excel_sheets,
)
from llm_advisor import (
    advise as llm_advise,  # Phase 2: LLM 协作层（轻量、被动、只读）
    get_model_name,
    get_base_url,
    has_api_key,
    test_llm_config,
    invalidate_llm_cache,
)
from config.settings import get_llm_config_view, save_llm_config
from op_executor import (
    preview_op,
    apply_op,
    parse_apply_command,
    validate_op,
    BASE_FIELDS,
    DERIVED_FIELDS,
    OP_CONFIRMATION_PREFIX,
)

# 本地存储层：任务持久化（Task 4 引入）
from storage.local_store import get_store

# ── 配置单源 ──────────────────────────────────────────────────────────────
# model/endpoint 统一从 config/agent_llm_config.json 读取（经 llm_advisor），
# 不再在本文件散落硬编码字面值，避免改 json 不生效的配置漂移。
MODEL_NAME = get_model_name()
ENDPOINT = get_base_url()


def _read_app_version() -> str:
    """版本单源：优先读 pyproject.toml 的 project.version，失败回落 0.1.0。"""
    pyproject = os.path.join(SCRIPT_DIR, "pyproject.toml")
    try:
        with open(pyproject, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("version") and "=" in line:
                    # version = "0.1.0"
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    except OSError:
        pass
    return "0.1.0"


APP_VERSION = _read_app_version()

def _setup_logging() -> logging.Logger:
    """配置控制台 + 文件双通道日志。"""
    log = logging.getLogger("web")
    log.setLevel(logging.INFO)
    if log.handlers:
        return log

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # 控制台
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    log.addHandler(console)

    # 文件（便于后台运行/排查）
    # R1 修复：RotatingFileHandler 轮转，防 web_server.log 无限增长吃满磁盘
    log_dir = os.path.join(SCRIPT_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "web_server.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    log.addHandler(file_handler)

    return log


logger = _setup_logging()

# 服务启动时间，用于 health 接口展示运行时长
_START_TIME = time.time()

# 自由 agent 栈已从本仓整体移除：本地工作台只用引擎层 + Engine Steward，
# 不得引入自由 agent 及其重型平台依赖（boto3/sqlalchemy 等），护栏见 test_p48_*。

# ─── 路由策略：意图 → 工具调用 ─────────────────────────────────────────────
# 每个意图对应的工具调用方式

# 走「结构化引擎 + 会话状态」的业务意图（不进自由 agent）
_PROJECT_INTENTS = {
    "quick_scan", "suggest", "trend", "compare", "breakeven",
    "benchmark", "market", "report_pdf", "report_excel", "decide", "cashflow",
    "attribution", "sensitivity",
}
# 会累积进 SessionState 的「项目定义」意图（与纯调研/报告/假设分析区分）
# compare 虽能修改参数，但属于一次性假设分析，不污染 session base。
_STATE_INTENTS = {"quick_scan", "suggest", "trend", "decide", "cashflow"}

# 一次性/假设分析意图：本句抽出的参数不污染 session base
_STATELESS_INTENTS = {"compare", "report_pdf", "report_excel", "market", "benchmark",
                       "attribution", "sensitivity"}

# 安全限制：单条用户消息最大长度（字符数），防止极端输入拖垮引擎/LLM
_MAX_INPUT_LENGTH = 10000
# 闲聊 steward 硬超时：超时不阻断主流程。
_LLM_ADVISE_TIMEOUT = 8.0
# 顾问面板 / 按需 AI 解读：用户主动触发，允许完整调用完成。
_ADVISOR_PANEL_TIMEOUT = 30.0


async def _advise_with_timeout(*args, timeout=None, **kwargs):
    """llm_advise 硬超时：超时不阻断结构化 JSON。"""
    advise_timeout = _LLM_ADVISE_TIMEOUT if timeout is None else timeout
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(llm_advise, *args, **kwargs),
            timeout=advise_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("LLM 解读跳过: 超时 %.0fs", advise_timeout)
        return {"text": "", "ops": [], "_skipped": "timeout"}

# 对比句中常用来引出「假设/变更方案」的引导词
_COMPARE_MARKERS = ("如果", "假设", "要是", "若", "换成", "改为", "变成",
                    "提高至", "提升到", "降低至", "降低到", "增加", "减少")


def _safe_json_loads(data: str, context: str = "工具") -> dict:
    """安全解析工具返回的 JSON；失败时返回带 error 字段的字典，不抛异常。"""
    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        logger.warning(f"{context} 返回非 JSON: {e}")
        return {"error": f"{context} 返回格式异常", "raw": data[:500]}
    except Exception as e:
        logger.warning(f"{context} 解析失败: {e}")
        return {"error": f"{context} 解析失败: {e}"}


def _extract_alt_clause(user_text: str) -> str:
    """从用户文本中提取「假设/对比」子句，用于构造方案 B。"""
    if not user_text:
        return ""
    # 优先按显式对比标记切分，取最后一段作为变更方案
    for marker in _COMPARE_MARKERS:
        idx = user_text.find(marker)
        if idx >= 0:
            return user_text[idx:].strip()
    return user_text


def _extract_alt_params(base_params: dict, user_text: str) -> dict:
    """
    基于 base_params 构造方案 B 的参数集。

    策略：
    1. 若文本含「如果/假设/换成」等标记，从该子句抽取增量参数，覆盖 base。
    2. 若未检出任何变更参数，返回 base 的浅拷贝（调用方可据此降级提示）。
    """
    alt_clause = _extract_alt_clause(user_text)
    alt_delta = extract_params(alt_clause) if alt_clause else {}
    alt = dict(base_params)
    if alt_delta:
        # 只合并真正的数值/字符串参数，不覆盖空值
        for k, v in alt_delta.items():
            if v is not None and v != "":
                alt[k] = v
    return alt


def _render_guard_banner(guard_info: dict, scan: dict) -> str:
    """把守门层检测到的「待确认/矛盾」渲染成前置横幅，规则层先发现，不依赖 LLM。"""
    if not isinstance(guard_info, dict):
        return ""
    parts: list = []
    # 历史矛盾（最高优先级）
    for c in guard_info.get("contradictions", []) or []:
        parts.append(f"⚠️ **参数矛盾**：{c.get('message', '')}")
    # 致命/需确认项
    for issue in guard_info.get("issues", []) or []:
        if issue.get("level") == "critical":
            parts.append(f"🛑 **需确认**：{issue.get('message', '')}")
        elif issue.get("needs_confirmation"):
            parts.append(f"🟡 **请确认**：{issue.get('message', '')}")
    # 引擎返回里的守门信息（派生一致性等）
    engine_guard = (scan.get("param_sources") or {}).get("_guard", {})
    for issue in (engine_guard.get("issues") or []):
        msg = issue.get("message", "")
        if msg and msg not in " ".join(parts):
            parts.append(f"🟡 {msg}")
    # 派生一致性矛盾（规则层先发现，月营收 vs 客流×单价 等）——最高优先级
    derived_issues = scan.get("derived_issues") or []
    for issue in derived_issues:
        msg = issue.get("message", "")
        if msg and msg not in " ".join(parts):
            parts.append(f"⚠️ **数据冲突**：{msg}")
    if not parts:
        return ""
    return "## ⚠️ 参数守门（先确认，再计算）\n" + "\n".join(parts)


def _build_advise_context(tid: str, scan: dict) -> dict:
    """构建主持人模式所需的 context 对象，供 LLM 决定追问/推荐/静默。"""
    # 审查修复 F4：锁内快照读取（get_advise_meta / get_turn_number），
    # 不再持有 get_state 引用在锁外读，避免与 apply_turn 并发写交错。
    meta = get_advise_meta(tid)
    snap = to_llm_view(tid)

    # 缺失参数（[param_sources] 含 [缺失] 的字段）
    param_sources = (scan or {}).get("param_sources", {})
    missing_params = [k for k, v in param_sources.items() if isinstance(v, str) and v.startswith("[缺失]")]

    # 可用动作
    available_actions = (scan or {}).get("available_actions", ["quick_scan"])

    # 行业默认值标记（从 scan params 里判断哪些字段有非 None 值）
    has_default = {}
    scan_params = (scan or {}).get("params", {})
    for field in ("daily_traffic", "price_per_unit", "variable_cost_ratio", "avg_salary", "employee_count"):
        if scan_params.get(field) is not None:
            has_default[field] = scan_params[field]

    return {
        "available_actions": available_actions,
        "recommendation_count": meta.get("recommendation_count", 0),
        "question_count": meta.get("question_count", 0),
        "missing_params": missing_params,
        "has_default": has_default,
        "turn_number": get_turn_number(tid),
        "industry": snap.get("industry"),
    }


def _reset_advise_meta(tid: str):
    """追问打断「连续推荐」时，清零推荐计数（重新给推荐机会）。"""
    # 审查修复 F4：改调 session_state 锁内 API，原先锁外 st[...] = 0 是
    # 与 apply_turn 并发时的裸写。
    reset_advise_count(tid, "recommendation_count")


def _incr_advise_meta(tid: str, field: str):
    """增加指定计数器（recommendation / comparison / question）。"""
    # 审查修复 F4：同上，锁内原子自增，防并发丢计数。
    incr_advise_count(tid, field)


# 候选方案序号标签：A/B/C/D
_OP_LABELS = ["A", "B", "C", "D", "E", "F"]


def _render_ops_block(ops: list, base_params: dict, current_scan: dict) -> str:
    """把 LLM 输出的 ops 提议渲染成「方案X→月利润Y，回『应用X』生效」的确认流。

    每条 op 跑 preview_op 算预览，过校验才展示；不合规的标拒因。
    """
    if not ops:
        return ""
    cur_profit = None
    if isinstance(current_scan, dict):
        cur_profit = (current_scan.get("core_metrics") or {}).get("monthly_profit")
    lines = [OP_CONFIRMATION_PREFIX]
    if cur_profit is not None:
        lines.append(f"当前月利润 **{cur_profit:,.0f} 元**。候选方案预览（引擎重算后）：")
    lines.append("")
    for i, op in enumerate(ops):
        tag = _OP_LABELS[i] if i < len(_OP_LABELS) else str(i + 1)
        preview = preview_op(op, base_params, quick_scan_tool)
        if not preview.get("ok"):
            lines.append(f"- **方案{tag}** ❌ 拒绝：{preview.get('reason', '')}（{preview.get('label','')}）")
            continue
        p = preview.get("profit_after")
        profit_str = f"{p:,.0f} 元" if isinstance(p, (int, float)) else "—"
        label = preview.get("label", "")
        chg = preview.get("changes", {})
        chg_str = "、".join(f"{k}={v}" for k, v in chg.items())
        lines.append(
            f"- **方案{tag}** {label}（{chg_str}）→ 月利润预计 **{profit_str}**"
            f"  ｜ 回复「**应用{tag}**」生效"
            f"  ｜ LLM 只提议、引擎算"
        )
    return "\n".join(lines)


def _render_ops_block_from_persisted(proposals: list, base_params: dict,
                                     current_scan: dict) -> str:
    """从 session 持久化的 ops 复用渲染（用户回「应用」时）。"""
    return _render_ops_block(proposals, base_params, current_scan)


def _fac_apply_for_step3():
    """暂时占位（见 chat 步骤3 的应用流）；保一致性用。"""
    pass


def _infer_sensitivity_variable(drivers: list, params: dict) -> str:
    """从 scenarios.drivers 推断最应分析的变量。

    优先级：客流 > 租金 > 变动成本率 > 人工。
    无 drivers 时默认分析客流（最常见的创业关切）。
    """
    _DRIVER_TO_VAR = {
        "客流": "daily_traffic",
        "月营收": "daily_traffic",  # 营收波动的根源是客流
        "租金": "monthly_rent",
        "人工": "employee_count",
        "变动成本率": "variable_cost_ratio",
    }
    for driver in drivers:
        for keyword, var in _DRIVER_TO_VAR.items():
            if keyword in driver:
                return var
    # 默认：如果用户有客流数据就分析客流，否则分析租金
    if params.get("daily_traffic") is not None:
        return "daily_traffic"
    if params.get("monthly_rent") is not None:
        return "monthly_rent"
    return "daily_traffic"


def _build_single_variable_sensitivity(params: dict, variable: str) -> dict:
    """单一变量弹性分析：找到指定变量的盈亏平衡点。

    Args:
        params: 已填充参数
        variable: 要分析的变量名（daily_traffic / monthly_rent / variable_cost_ratio）

    Returns:
        {
            "variable": "daily_traffic",
            "variable_label": "日均客流",
            "current_value": 100,
            "breakeven_value": 67,
            "margin": 33,           # 当前值离盈亏平衡点的距离
            "margin_pct": 0.33,     # 距离百分比
            "direction": "下降",    # 变量需要上升还是下降才能盈亏平衡
            "sensitivity_curve": [  # 变量在不同值下的利润
                {"value": 50, "profit": -3000},
                {"value": 67, "profit": 0},
                {"value": 100, "profit": 5000},
                ...
            ],
            "interpretation": "客流降至 67 杯/天时盈亏平衡，当前 100 杯/天有 33% 的安全边际。",
        }
    """
    revenue = params.get("monthly_revenue") or 0
    fixed_cost = params.get("monthly_fixed_cost") or 0
    vc_ratio = params.get("variable_cost_ratio")
    daily_traffic = params.get("daily_traffic") or 0
    price = params.get("price_per_unit") or 0
    rent = params.get("monthly_rent") or 0

    if vc_ratio is None:
        return {"insufficient": True, "message": "变动成本率缺失，无法做弹性分析。", "gaps": ["变动成本率"]}

    _VAR_LABELS = {
        "daily_traffic": "日均客流（杯/天）",
        "monthly_rent": "月租金（元）",
        "variable_cost_ratio": "变动成本率（%）",
        "employee_count": "员工人数（人）",
        "avg_salary": "人均月薪（元）",
        "price_per_unit": "客单价（元）",
    }
    label = _VAR_LABELS.get(variable, variable)

    # ── 盈亏平衡点求解 ──
    # 利润 = 营收 - 固定成本 - 营收×vc_ratio = 营收×(1-vc_ratio) - 固定成本
    # 营收 = daily_traffic × price × 30
    # 所以：daily_traffic × price × 30 × (1-vc_ratio) = 固定成本
    # → daily_traffic = 固定成本 / (price × 30 × (1-vc_ratio))

    current_value = params.get(variable) or 0
    breakeven_value = None

    if variable == "daily_traffic":
        if price > 0 and (1 - vc_ratio) > 0:
            breakeven_value = round(fixed_cost / (price * DAYS_PER_MONTH * (1 - vc_ratio)), 1)
    elif variable == "monthly_rent":
        # 利润 = revenue×(1-vc_ratio) - rent - (fixed_cost - rent) - revenue×vc_ratio
        # 简化：利润 = revenue - fixed_cost - revenue×vc_ratio
        # 固定成本 = rent + other_fixed
        # → breakeven_rent = revenue×(1-vc_ratio) - (fixed_cost - rent)
        other_fixed = fixed_cost - rent
        contribution_margin = revenue * (1 - vc_ratio)
        breakeven_value = round(contribution_margin - other_fixed, 0)
    elif variable == "variable_cost_ratio":
        if revenue > 0:
            # 利润 = revenue×(1-vc_ratio) - fixed_cost = 0
            # → vc_ratio = 1 - fixed_cost/revenue
            breakeven_value = round(1 - fixed_cost / revenue, 4)
    elif variable == "employee_count":
        avg_salary = params.get("avg_salary") or 0
        if avg_salary > 0:
            other_labor = fixed_cost - (current_value * avg_salary if current_value else 0)
            contribution_margin = revenue * (1 - vc_ratio)
            breakeven_value = round((contribution_margin - other_labor + rent) / avg_salary, 1) if avg_salary else None
    elif variable == "price_per_unit":
        if daily_traffic > 0 and (1 - vc_ratio) > 0:
            breakeven_value = round(fixed_cost / (daily_traffic * DAYS_PER_MONTH * (1 - vc_ratio)), 1)

    if breakeven_value is None or breakeven_value <= 0:
        return {
            "variable": variable,
            "variable_label": label,
            "current_value": current_value,
            "breakeven_value": None,
            "interpretation": f"当前参数下无法计算「{label}」的盈亏平衡点（可能缺少关键数据）。",
        }

    # ── 安全边际 ──
    if variable == "variable_cost_ratio":
        # 变动成本率越低越好，margin = 当前值 - 盈亏平衡值（负的margin=已超平衡点）
        margin = breakeven_value - current_value
        direction = "上升" if margin > 0 else "已超"
    else:
        margin = current_value - breakeven_value
        direction = "下降" if margin > 0 else "已超"

    margin_pct = abs(margin) / current_value if current_value > 0 else 0

    # ── 敏感度曲线（11 个点）──
    curve = []
    for i in range(11):
        pct = -0.5 + i * 0.1  # -50% 到 +50%
        v = breakeven_value * (1 + pct) if breakeven_value > 0 else 0
        if variable == "daily_traffic":
            # 月营收公式唯一出处在 field_model（曾在本文件手写 `v * price * 30`，
            # 与保本侧的 365 口径并存 → 见 F1）
            from field_model import monthly_revenue_from_traffic, DAYS_PER_MONTH
            rev = monthly_revenue_from_traffic(v, price)
            profit = rev * (1 - vc_ratio) - fixed_cost
        elif variable == "monthly_rent":
            other_fixed = fixed_cost - rent
            profit = revenue * (1 - vc_ratio) - v - other_fixed
        elif variable == "variable_cost_ratio":
            profit = revenue * (1 - v) - fixed_cost
        else:
            profit = 0  # 其他变量的曲线简化
        curve.append({"value": round(v, 1), "profit": round(profit, 0)})

    # ── 解读 ──
    if margin > 0:
        interp = (
            f"「{label}」的盈亏平衡点是 {breakeven_value:g}，"
            f"当前 {current_value:g}，有 {abs(margin):g}（{margin_pct:.0%}）的安全边际。"
            f"即使{label}{'下降' if variable not in ('variable_cost_ratio',) else '上升'}到 {breakeven_value:g}，项目仍不亏。"
        )
    else:
        interp = (
            f"⚠️ 「{label}」当前 {current_value:g}，已{'超过' if variable == 'variable_cost_ratio' else '低于'}"
            f"盈亏平衡点 {breakeven_value:g}，项目处于亏损状态。"
        )

    return {
        "variable": variable,
        "variable_label": label,
        "current_value": current_value,
        "breakeven_value": breakeven_value,
        "margin": round(abs(margin), 1),
        "margin_pct": round(margin_pct, 4),
        "direction": direction,
        "sensitivity_curve": curve,
        "interpretation": interp,
    }


def _route_intent(intent: str, merged_params: dict, user_text: str) -> Optional[Dict]:
    """
    根据意图和**已 merge 的会话参数**，调对应工具，返回结构化数据。
    返回 None 表示该意图不直接走工具（chitchat 等）。

    Phase 3：入参改为 merge 后的 params（来自 SessionState），而非每轮重新
    抽取——这是消除「信息不全反复误报」的关键。

    同步阻塞调用，须在线程池中执行（见 chat 中 asyncio.to_thread）。
    """
    params_json = json.dumps(merged_params, ensure_ascii=False) if merged_params else user_text

    if intent in ("quick_scan", "breakeven"):
        # breakeven 问句（"调到多少能保本"）复用 quick_scan：引擎已算保本模块，
        # 统一走唯一计算点，绝不落自由 agent（P4-3 路由收口）
        tool_data = quick_scan_tool.invoke({"params_json": params_json})
        return {"intent": "quick_scan", "data": _safe_json_loads(tool_data, "quick_scan"), "params": merged_params}

    elif intent == "suggest":
        # 调参建议需要 quick_scan + suggest_params
        scan = _safe_json_loads(quick_scan_tool.invoke({"params_json": params_json}), "quick_scan")
        sug = _safe_json_loads(suggest_params_tool.invoke({"params_json": params_json}), "suggest_params")
        return {"intent": intent, "data": sug, "scan": scan, "params": merged_params}

    elif intent == "decide":
        # L2 决策引擎（验证期决策工作台）：规则排序，不靠 LLM
        # 决策类型从问句子路由（该不该开/继续/先验证/撑多久…）
        from router.intent import decide_type_of
        from decision_engine import decide as decide_engine
        dtype = decide_type_of(user_text)
        scan = _safe_json_loads(quick_scan_tool.invoke({"params_json": params_json}), "quick_scan")
        sug = _safe_json_loads(suggest_params_tool.invoke({"params_json": params_json}), "suggest_params")
        # 档 C：runway 决策消费现金流明细（归零月 + 累计缺口）
        cf = None
        if dtype == "runway":
            cf = _safe_json_loads(cashflow_tool.invoke({"params_json": params_json}), "cashflow_projection")
        res = decide_engine(
            decision_type=dtype, scan=scan, basis=scan.get("basis"),
            current_params=merged_params, suggest_data=sug,
            user_text=user_text, accepted_hypotheses=None,
            cashflow_data=cf,
        )
        return {
            "intent": "decide", "data": res, "scan": scan,
            "params": merged_params, "decision_type": dtype,
        }

    elif intent == "cashflow":
        # 现金流明细表（档 B）：不污染 P&L，期初现金=available_cash
        tool_data = cashflow_tool.invoke({"params_json": params_json})
        return {"intent": intent,
                "data": _safe_json_loads(tool_data, "cashflow_projection"),
                "scan": _safe_json_loads(quick_scan_tool.invoke({"params_json": params_json}), "quick_scan"),
                "params": merged_params}

    elif intent == "trend":
        tool_data = trend_tool.invoke({"params_json": params_json})
        return {"intent": intent, "data": _safe_json_loads(tool_data, "trend_projection"), "params": merged_params}

    elif intent == "attribution":
        # 归因拆解：复用 quick_scan 的 _fill_and_assess 产出，不重算
        scan = _safe_json_loads(quick_scan_tool.invoke({"params_json": params_json}), "quick_scan")
        if scan.get("insufficient"):
            return {"intent": intent, "data": scan, "params": merged_params}
        filled_params = scan.get("params", merged_params)
        # 注入参数来源标注（供归因展示"来自用户/默认/推算"）
        filled_params["_param_sources"] = scan.get("param_sources", {})
        attr_data = _build_cost_attribution(filled_params)
        return {"intent": intent, "data": attr_data, "scan": scan, "params": merged_params}

    elif intent == "sensitivity":
        # 敏感度分析：复用 quick_scan 产出，提取单一变量弹性
        scan = _safe_json_loads(quick_scan_tool.invoke({"params_json": params_json}), "quick_scan")
        if scan.get("insufficient"):
            return {"intent": intent, "data": scan, "params": merged_params}
        filled_params = scan.get("params", merged_params)
        scenarios = scan.get("scenarios", {})
        # 确定分析哪个变量：从 scenarios.drivers 取第一个，或默认分析客流
        drivers = scenarios.get("drivers", [])
        variable = _infer_sensitivity_variable(drivers, filled_params)
        sens_data = _build_single_variable_sensitivity(filled_params, variable)
        return {"intent": intent, "data": sens_data, "scan": scan, "params": merged_params}

    elif intent == "compare":
        alt_params = _extract_alt_params(merged_params, user_text)
        alt_json = json.dumps(alt_params, ensure_ascii=False)
        tool_data = compare_tool.invoke({
            "base_json": params_json,
            "alt_json": alt_json,
        })
        return {"intent": intent, "data": _safe_json_loads(tool_data, "compare_scenarios"), "params": merged_params}

    elif intent == "benchmark":
        industry = merged_params.get("industry", "通用")
        tool_data = benchmark_tool.invoke({"industry": industry})
        return {"intent": intent, "data": _safe_json_loads(tool_data, "search_industry_benchmarks") if isinstance(tool_data, str) else tool_data, "params": merged_params}

    elif intent == "market":
        # market 与 benchmark 共享行业基准数据；提取可选指标关键词提升查询精度
        industry = merged_params.get("industry", "通用")
        metric = ""
        metric_keywords = ["毛利率", "获客成本", "增长率", "市场规模",
                           "复购率", "客单价", "转化率", "渗透率"]
        for kw in metric_keywords:
            if kw in user_text:
                metric = kw
                break
        tool_data = benchmark_tool.invoke({"industry": industry, "metric": metric})
        return {"intent": intent, "data": _safe_json_loads(tool_data, "search_market_data") if isinstance(tool_data, str) else tool_data, "params": merged_params}

    elif intent == "report_pdf":
        # 报告生成需要完整上下文：把 industry 注入参数避免 quick_scan 丢失项目类型
        report_params = dict(merged_params)
        if merged_params.get("industry"):
            report_params.setdefault("industry", merged_params["industry"])
        report_json = json.dumps(report_params, ensure_ascii=False)
        scan = _safe_json_loads(quick_scan_tool.invoke({"params_json": report_json}), "quick_scan")
        md = build_report_markdown(scan)
        title = f"report_{merged_params.get('industry', 'project')}"
        tool_data = report_pdf_tool.invoke({
            "report_content_markdown": md,
            "report_title": title,
        })
        return {"intent": intent, "data": _safe_json_loads(tool_data, "generate_financial_report"), "params": merged_params}

    elif intent == "report_excel":
        report_params = dict(merged_params)
        if merged_params.get("industry"):
            report_params.setdefault("industry", merged_params["industry"])
        report_json = json.dumps(report_params, ensure_ascii=False)
        scan = _safe_json_loads(quick_scan_tool.invoke({"params_json": report_json}), "quick_scan")
        sheets = build_excel_sheets(scan)
        title = f"model_{merged_params.get('industry', 'project')}"
        tool_data = report_excel_tool.invoke({
            "sheets_json": json.dumps(sheets, ensure_ascii=False),
            "report_title": title,
        })
        return {"intent": intent, "data": _safe_json_loads(tool_data, "generate_financial_excel"), "params": merged_params}

    return None


# ─── Pydantic 模型 ─────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str
    # 安全审查 S7：单条消息长度上限（防超长输入拖爆解析/存储）。
    # C1 修复：pydantic 层上限仅作 DoS 硬防线（远大于业务限制），
    # 10000 的规格限制仍由业务层 _MAX_INPUT_LENGTH 统一拦截返回 400，
    # 避免双轨矛盾与 422/400 语义冲突
    content: str = Field(max_length=100000)


class ChatRequest(BaseModel):
    # 安全审查 S7：消息条数上限（防构造超大 messages 数组 DoS）
    messages: list[ChatMessage] = Field(max_length=100)
    thread_id: Optional[str] = "default"
    task_id: Optional[str] = None


class AdviceRequest(BaseModel):
    thread_id: Optional[str] = "default"
    task_id: Optional[str] = None
    analysis_id: Optional[str] = None
    params_version: Optional[int] = None


class TaskReq(BaseModel):
    # 安全审查 S7：任务名长度上限（防超大任务名入库）
    name: str = Field(default="新任务", max_length=100)


class TaskRename(BaseModel):
    name: str = Field(max_length=100)


# ─── FastAPI 应用 ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Web 服务启动：本地工作台模式（仅引擎层 + Engine Steward）")
    yield
    # R3/R4 修复：服务关闭时释放资源（文件描述符 + 数据库连接）
    store = get_store()
    if hasattr(store, 'close'):
        store.close()
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    logger.info("Web 服务关闭")


app = FastAPI(title="创业者工作台", lifespan=lifespan)

# CORS：收紧为白名单，避免任意恶意站点跨站操作。
# 白名单跟随实际服务端口（PORT 环境变量，默认 8081，与 start.sh / argparse 默认值同源），
# 并保留历史端口 8080——否则默认端口启动时跨端口调试会被 CORS 拦掉（端口三方不一致的残留）。
_SERVICE_PORT = os.environ.get("PORT", "8081")
_ALLOWED_ORIGINS = [
    f"http://127.0.0.1:{_SERVICE_PORT}",
    f"http://localhost:{_SERVICE_PORT}",
    "http://127.0.0.1:8080",
    "http://localhost:8080",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


@app.middleware("http")
async def require_xhr_for_writes(request: Request, call_next):
    """POST/PUT/DELETE 要求 X-Requested-With 头，防止恶意站点 <form> 跨站伪造。"""
    if request.method in ("POST", "PUT", "DELETE"):
        if request.headers.get("X-Requested-With", "").lower() != "xmlhttprequest":
            return JSONResponse({"error": "missing X-Requested-With header"}, status_code=403)
    return await call_next(request)

# M3：前端静态资源（app.css / app.js）从 CHAT_HTML 内联抽取为独立文件，
# 由 FastAPI StaticFiles 挂载到 /static。抽取后 CHAT_HTML 仅剩 HTML 骨架。
import os as _os
_STATIC_DIR = _os.path.join(SCRIPT_DIR, "src", "web_static")
if _os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ─── 聊天界面 HTML ─────────────────────────────────────────────────────────
CHAT_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>创业者工作台</title>
<link rel="stylesheet" href="/static/app.css?v=20260413a">
</head>
<body>
<header>
  <h1>创业者工作台 <span id="model-name" style="opacity:0.5;font-size:12px;">· __MODEL_NAME__</span></h1>
  <div style="display:flex;align-items:center;gap:10px;">
    <button id="settings-btn" title="LLM 配置">⚙️</button>
    <span class="meta" id="status" title="点击设置大模型">连接中...</span>
  </div>
</header>
<div id="layout">
  <aside id="sidebar">
    <div id="sidebar-head"><span>任务列表</span><button id="new-task">+ 新建</button></div>
    <div id="task-list"></div>
  </aside>
  <div id="main">
    <div id="chat">
      <div class="empty" id="empty">
        <h2>商业建模助手</h2>
        <p>输入你的项目参数，AI 会调用工具给出分析</p>
        <div class="examples">
          <button class="example" data-q="开一家咖啡店，月租金15000，员工3人，人均工资5000，每天50杯客流量，均价25元。详细分析">📊 餐饮项目分析</button>
          <button class="example" data-q="做一个 SaaS 工具，目标客户中小企业，定价99元/月，预计首年1000用户。详细分析">💻 SaaS 模式评估</button>
          <button class="example" data-q="快速：奶茶店总投资50万，月租2万">⚡ 快速扫描</button>
          <button class="example" data-q="查一下 2024 年中国咖啡行业的毛利率和获客成本基准">🔍 行业基准</button>
        </div>
      </div>
    </div>
    <div id="cat-bar">
      <button class="cat-btn active" data-cat="all">全部<span class="cat-badge"></span></button>
      <button class="cat-btn" data-cat="analyze">分析<span class="cat-badge"></span></button>
      <button class="cat-btn" data-cat="params">改参<span class="cat-badge"></span></button>
      <button class="cat-btn" data-cat="decide">决策<span class="cat-badge"></span></button>
      <button class="cat-btn" data-cat="compare">对比<span class="cat-badge"></span></button>
    </div>
    <div id="analyze-status"></div>
    <div id="analyze-controls">
      <button class="panel-btn" data-q="趋势预测">📈 趋势预测</button>
      <button class="panel-btn" data-q="现金流怎么样">💧 现金流</button>
      <button class="panel-btn" data-q="怎么扭亏">💡 扭亏建议</button>
      <button class="panel-btn" data-q="查一下行业基准">📊 行业基准</button>
      <button class="panel-btn" data-q="重新分析当前项目">🔄 重新分析</button>
    </div>
    <div id="decide-prompt"></div>
    <div id="decide-controls">
      <button class="panel-btn" data-q="该不该继续">🤔 该不该继续</button>
      <button class="panel-btn" data-q="先验证什么">🔍 先验证什么</button>
      <button class="panel-btn" data-q="还能撑多久">⏳ 还能撑多久</button>
    </div>
    <div id="params-controls">
      <div class="pc-row" id="pc-fields"></div>
      <div class="pc-row"><button class="pc-recalc">重算</button></div>
    </div>
    <div id="compare-controls">
      <div class="cc-row" id="cc-row-a"><span style="color:#666;">方案A = 当前参数（锁）</span></div>
      <div class="cc-row" id="cc-row-b"><span>方案B：改</span><select id="cc-field"><option value="monthly_rent">月租金</option><option value="daily_traffic">日均客流</option><option value="price_per_unit">客单价</option><option value="employee_count">员工人数</option><option value="total_investment">总投资</option><option value="avg_salary">人均薪资</option><option value="variable_cost_ratio">变动成本率</option></select><input id="cc-value" placeholder="数值"><button class="cc-run">跑对比</button></div>
    </div>
    <div id="input-area">
      <textarea id="input" rows="1" placeholder="输入项目参数，或点击上方示例开始…"></textarea>
      <button id="send">发送</button>
    </div>
  </div>
  <aside id="params-panel">
    <div id="params-head">
      <div id="params-tabs">
        <button class="params-tab active" data-tab="params">参数</button>
        <button class="params-tab" data-tab="advisor">顾问</button>
      </div>
    </div>
    <div id="export-btns"><button class="export-btn" id="export-pdf" disabled>📄 PDF</button><button class="export-btn" id="export-excel" disabled>📊 Excel</button></div>
    <div id="params-list"><p style="color:#999;font-size:13px;padding:12px 0;">输入项目参数后这里会显示</p></div>
    <div id="advisor-list" style="display:none;"></div>
  </aside>
</div>
<script src="/i18n.js"></script>
<script src="/static/app.js?v=20260413a"></script>
</body>
</html>
"""

SETTINGS_MODAL_HTML = """
<div class="modal-overlay" id="settings-modal" style="display:none;">
  <div class="modal-box">
    <div class="modal-title">⚙️ LLM 配置</div>
    <label class="modal-label">模型名称</label>
    <input class="modal-input" id="cfg-model" placeholder="例如: deepseek-v4-flash">
    <label class="modal-label">API 端点 (base_url)</label>
    <input class="modal-input" id="cfg-base-url" placeholder="https://api.longcat.chat/openai">
    <label class="modal-label">API Key</label>
    <input class="modal-input" id="cfg-api-key" type="password" placeholder="sk-...">
    <div class="modal-actions">
      <button class="modal-btn cancel" id="cfg-cancel">取消</button>
      <button class="modal-btn save" id="cfg-save">保存</button>
    </div>
  </div>
</div>
"""


# ─── 路由 ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    # 配置单源：模型名运行时注入（占位符替换，避免 f-string 与 CSS 花括号冲突）
    # 动态读取，保证用户通过 /settings/llm 保存后刷新页面即看到新名，无需重启
    return CHAT_HTML.replace("__MODEL_NAME__", get_model_name())


@app.get("/i18n.js", response_class=Response)
async def i18n_js():
    """下发前端文案字典：locale（部署级）+ 摊平后的键值表，挂到 window.__I18N__。

    语言与后端同源（src/i18n/{locale}.yaml），前端 app.js 的 t() 只读这张表，
    不做自己的文案维护——避免前后端文案两套真相源漂移。
    """
    import i18n as _i18n
    import json as _json
    locale = _i18n.get_locale()
    table = _i18n._load(locale)
    return Response(
        "window.__SHANGZHU_LOCALE__ = " + _json.dumps(locale, ensure_ascii=False) + ";\n"
        "window.__I18N__ = " + _json.dumps(table, ensure_ascii=False) + ";",
        media_type="application/javascript",
    )


@app.get("/health")
async def health():
    uptime_seconds = round(time.time() - _START_TIME, 1)
    stats = session_stats()
    return {
        "status": "ok",
        "model": get_model_name(),
        "endpoint": get_base_url(),
        "version": APP_VERSION,
        "uptime_seconds": uptime_seconds,
        "llm_configured": has_api_key(),
        "sessions": stats,
        "store_backend": type(get_store()).__name__,
    }


# ── 模型设置 API（运行时切换 LLM 配置）──────────────────────────────────────
@app.get("/settings/llm")
async def get_llm_settings():
    """读取当前模型配置（脱敏，key 只回显掩码）。"""
    return get_llm_config_view()


@app.post("/settings/llm")
async def set_llm_settings(req: Request):
    """保存模型配置（model / base_url / api_key，key 空则不覆盖）。"""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "请求体需为 JSON"}, status_code=400)

    model = body.get("model", "")
    base_url = body.get("base_url", "")
    api_key = body.get("api_key", "")

    if not str(model).strip():
        return JSONResponse({"error": "模型名称不能为空"}, status_code=400)
    if api_key and len(api_key.strip()) < 8:
        return JSONResponse({"error": f"API Key 过短（{len(api_key.strip())} 位），至少 8 位"}, status_code=400)

    try:
        view = save_llm_config(model, base_url, api_key)
        # 审查修复 F7：保存成功后立即失效 LLM client 缓存，
        # 缩短「新配置已存但旧 client 仍在用」的不一致窗口。
        invalidate_llm_cache()
    except ValueError as e:
        # 参数校验失败（如 API Key 过短）→ 400（校验消息为自控文案，无内部信息）
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception:
        # 安全审查 S3：非预期异常不透传原文（可能含内部路径），详情只进日志
        logger.exception("保存模型配置失败")
        return JSONResponse({"error": "保存失败，请稍后重试或查看服务日志"}, status_code=500)

    return JSONResponse({"ok": True, **view})


@app.post("/settings/llm/test")
async def test_llm_settings(req: Request):
    """连通性探测：验证 model+base_url+key 是否可用。"""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "请求体需为 JSON"}, status_code=400)

    model = body.get("model", "").strip()
    base_url = body.get("base_url", "").strip()
    api_key = body.get("api_key", "")

    if not model:
        return JSONResponse({"error": "模型名称不能为空"}, status_code=400)
    if not base_url:
        return JSONResponse({"error": "接口 URL 不能为空"}, status_code=400)
    # key 为空时，尝试用现有配置中的 key（保存时"留空=不修改"，测试时需要用实际 key）
    if not api_key:
        try:
            from config.settings import load
            current = load()
            api_key = current.get("config", {}).get("api_key", "")
        except Exception:
            pass
    if not api_key:
        return JSONResponse({"error": "API Key 不能为空"}, status_code=400)

    try:
        result = test_llm_config(model, base_url, api_key)
    except Exception as e:
        logger.exception("连通性探测失败")
        # 安全审查 S3：同上，异常原文不透传
        return JSONResponse({"ok": False, "error": "探测失败，请稍后重试或查看服务日志"}, status_code=500)

    return JSONResponse(result)


# ── 任务 CRUD API（Task 4）──────────────────────────────────────────────────
@app.post("/tasks")
async def create_task(req: TaskReq):
    """新建任务，返回任务对象（含生成的 id，兼作 thread_id）。"""
    store = get_store()
    return store.create_task(req.name)


@app.get("/tasks")
async def list_tasks():
    """列出未归档的任务（按最近更新倒序）。"""
    return get_store().list_tasks()


@app.put("/tasks/{task_id}/rename")
async def rename_task(task_id: str, req: TaskRename):
    get_store().rename_task(task_id, req.name)
    return {"ok": True, "id": task_id, "name": req.name}


@app.get("/tasks/{task_id}/messages")
async def get_task_messages(task_id: str):
    """取某任务的完整消息历史。"""
    return get_store().get_messages(task_id)


@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """软删（归档）：隐藏但保留消息，可恢复。同时清理 session_state 防内存泄漏。"""
    try:
        get_store().delete_task(task_id)
    except Exception as e:
        logger.error(f"删除任务 {task_id} 失败: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    # F6：删除任务时同步清理内存中的 session_state，防 4h TTL 到期前内存泄漏
    try:
        from session_state import reset_state
        reset_state(task_id)
    except Exception as e:
        logger.warning(f"清理 session_state 失败（不影响删除）: {e}")
    return {"ok": True, "id": task_id, "deleted": True}


# ── 顾问面板 API ─────────────────────────────────────────────────────────

def _build_scan_for_advisor(tid: str) -> dict:
    """为顾问面板构建 scan：复用 quick_scan 逻辑。"""
    params = get_biz_snapshot(tid)["params"]
    params_json = json.dumps(params, ensure_ascii=False)
    try:
        return json.loads(quick_scan_tool.invoke({"params_json": params_json}))
    except Exception:
        return {}


def _get_param_sources_for_advisor(tid: str) -> dict:
    """获取当前参数来源标注。"""
    scan = _build_scan_for_advisor(tid)
    return scan.get("param_sources") or {}


@app.get("/advisor")
async def get_advisor(tid: str):
    """获取当前状态的 LLM 顾问建议（只读，不写状态）。

    触发方式：前端显式调用（用户点击「顾问」Tab）或参数哈希变化 + 防抖。
    返回：顾问面板 JSON（judgment / risks / actions / citations）。
    """
    if not tid or not _is_uuid(tid):
        return JSONResponse({"error": "无效 tid"}, status_code=400)

    clean_view = to_llm_view(tid)
    scan = _build_scan_for_advisor(tid)
    param_sources = _get_param_sources_for_advisor(tid)

    # 主持人模式：构建 context
    advise_context = _build_advise_context(tid, scan)

    # 调用 LLM（与 /chat 同源 advise，非阻塞）
    try:
        advice = await _advise_with_timeout(
            scan, "", clean_view, advise_context,
            timeout=_ADVISOR_PANEL_TIMEOUT,
        )
    except Exception as e:
        logger.warning(f"顾问 LLM 调用失败: {e}")
        advice = {"text": "", "ops": []}

    # 格式化 + 数字防火墙
    from advisor.advisor_formatter import format_advice
    formatted = format_advice(advice, clean_view, param_sources)
    return JSONResponse(formatted)


@app.post("/analysis/advice")
async def post_analysis_advice(req: AdviceRequest):
    """按需生成本轮结构化结果的 AI 解读（不并入 /chat，不影响顾问 Tab）。

    校验 analysis_id + params_version：参数已变则返回 stale，不贴旧解读。
    """
    tid = req.task_id or req.thread_id or ""
    if not tid:
        return JSONResponse({"error": "无效 tid", "status": "error"}, status_code=400)

    snap = get_last_analysis(tid)
    if not snap:
        return JSONResponse({
            "status": "idle",
            "text": "",
            "ops": [],
            "ops_block": "",
            "reason": "暂无本轮分析，请先发送项目参数",
        }, status_code=404)

    current_version = get_params_version(tid)
    snap_version = snap.get("params_version")
    snap_id = snap.get("analysis_id")
    if req.analysis_id and snap_id and req.analysis_id != snap_id:
        return JSONResponse({
            "status": "stale",
            "text": "",
            "ops": [],
            "ops_block": "",
            "reason": "这是上一轮分析，请对最新结果重新生成解读",
            "analysis_id": snap_id,
            "params_version": current_version,
        })
    if req.params_version is not None and (
        req.params_version != snap_version or snap_version != current_version
    ):
        return JSONResponse({
            "status": "stale",
            "text": "",
            "ops": [],
            "ops_block": "",
            "reason": "参数已更新，请对最新分析结果重新生成解读",
            "analysis_id": snap_id,
            "params_version": current_version,
        })

    scan = snap.get("scan") or {}
    user_text = snap.get("user_text") or ""
    intent = snap.get("intent") or ""
    base_params = snap.get("params") or get_biz_snapshot(tid).get("params") or {}
    advise_context = _build_advise_context(tid, scan)
    try:
        advice = await _advise_with_timeout(
            scan,
            user_text,
            to_llm_view(tid, focus_fields=infer_focus_fields(user_text)),
            advise_context,
            timeout=_ADVISOR_PANEL_TIMEOUT,
        )
    except Exception as e:
        logger.warning("按需解读失败: %s", e)
        advice = {"text": "", "ops": [], "_skipped": "error"}

    advice_text = advice.get("text", "") if isinstance(advice, dict) else (advice or "")
    ops_proposals = advice.get("ops", []) if isinstance(advice, dict) else []
    skipped = isinstance(advice, dict) and advice.get("_skipped")

    if intent == "decide" and advice_text:
        from decision_engine import forbidden_tone_scan
        if forbidden_tone_scan(advice_text):
            advice_text = ""

    has_conflict = bool((scan.get("derived_issues") or []) if isinstance(scan, dict) else [])
    if has_conflict:
        ops_proposals = []
        from field_model import conflict_resolution_ops
        ops_proposals = conflict_resolution_ops(base_params) or []

    if isinstance(advice, dict) and advice.get("meta"):
        m = advice["meta"]
        if m.get("made_recommendation"):
            _incr_advise_meta(tid, "recommendation_count")
        if m.get("asked_question"):
            _reset_advise_meta(tid)
            _incr_advise_meta(tid, "question_count")

    ops_block = ""
    if ops_proposals:
        ops_block = _render_ops_block(ops_proposals, base_params, scan) or ""
        if ops_block:
            from session_state import set_pending_ops
            set_pending_ops(tid, ops_proposals)

    if skipped and not advice_text:
        status = "timeout" if advice.get("_skipped") == "timeout" else "error"
        return JSONResponse({
            "status": status,
            "text": "",
            "ops": ops_proposals,
            "ops_block": ops_block,
            "analysis_id": snap_id,
            "params_version": current_version,
            "reason": "解读超时，可重试" if status == "timeout" else "解读失败，可重试",
        })

    return JSONResponse({
        "status": "ok" if advice_text else "empty",
        "text": advice_text,
        "ops": ops_proposals,
        "ops_block": ops_block,
        "analysis_id": snap_id,
        "params_version": current_version,
    })


@app.get("/advisor/preview")
async def preview_advisor_action(tid: str, op: str):
    """预览顾问建议动作的效果（不写状态）。

    op：JSON 编码的 op 字典。
    返回：{ok, preview, reason}。
    """
    if not tid or not _is_uuid(tid):
        return JSONResponse({"error": "无效 tid"}, status_code=400)

    try:
        op_dict = json.loads(op)
    except json.JSONDecodeError:
        return JSONResponse({"error": "op 格式错误"}, status_code=400)

    ok, reason = validate_op(op_dict)
    if not ok:
        return JSONResponse({"ok": False, "preview": "", "reason": reason})

    base_params = get_biz_snapshot(tid)["params"]
    preview = await asyncio.to_thread(
        preview_op, op_dict, base_params, quick_scan_tool,
    )
    if preview and preview.get("ok"):
        return JSONResponse({
            "ok": True,
            "preview": preview.get("label", ""),
            "changes": preview.get("changes", {}),
            "reason": "",
        })
    return JSONResponse({
        "ok": False,
        "preview": "",
        "reason": preview.get("reason", "预览失败") if preview else "预览失败",
    })


@app.post("/advisor/apply")
async def apply_advisor_action(tid: str, request: Request):
    """执行顾问建议的动作（与 /chat「应用 A/B」共用同一入口）。

    请求体：{op: {...}}
    动作经过 validate_op → apply_op → apply_turn，与对话区同源。
    """
    if not tid or not _is_uuid(tid):
        return JSONResponse({"error": "无效 tid"}, status_code=400)

    try:
        body = await request.body()  # noqa: F821
        data = json.loads(body) if body else {}
    except Exception:
        return JSONResponse({"error": "请求体格式错误"}, status_code=400)

    op_dict = data.get("op")
    if not op_dict:
        return JSONResponse({"error": "缺 op"}, status_code=400)

    # 与 /chat「应用 A/B」完全同源：validate_op → apply_op → apply_turn
    ok, reason = validate_op(op_dict)
    if not ok:
        return JSONResponse({"ok": False, "applied": False, "reason": reason})

    ok, reason, new_merged = await asyncio.to_thread(
        apply_op, op_dict, tid, apply_turn,
    )
    if not ok:
        return JSONResponse({"ok": False, "applied": False, "reason": reason})

    return JSONResponse({"ok": True, "applied": True, "new_params": new_merged})


def _is_uuid(s: str) -> bool:
    """判断字符串是否为合法 UUID（tasks.id 列只接受 uuid）。"""
    if not s:
        return False
    try:
        _uuid.UUID(str(s))
        return True
    except (ValueError, AttributeError):
        return False


def _persist_turn(store, tid: str, user_msg: str, content: str) -> None:
    """把一轮对话写入 store：记录 user+assistant 消息 + params 快照。

    只存业务字段（复用 to_llm_view 过滤思路），不带 `_pending_ops` 等临时内部键，
    避免把运行时临时状态落盘。turn 取自 SessionState。

    仅对 uuid 任务 id 落盘；legacy 非 uuid thread_id 走纯内存（兼容旧行为）。

    F4 修复：不再持有 SessionState 引用迭代 params，改用 to_llm_view 获取锁内拷贝。
    """
    if not tid or not _is_uuid(tid):
        return
    try:
        view = to_llm_view(tid)
        turn = view.get("turn", 0)
        store.add_message(tid, "user", user_msg, turn)
        store.add_message(tid, "assistant", content, turn)
        biz = {k: v for k, v in (view.get("params") or {}).items()
               if not k.startswith("_")}
        store.update_params(tid, biz)
    except Exception as e:  # noqa
        logger.warning(f"持久化本轮失败(不阻断): {e}")


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    同步聊天接口。
    主路径走意图路由 + 工具 + 模板（0 LLM 调用）。
    chitchat 走 LLM。
    """
    store = get_store()

    # ── 任务维度解析（Task 5）──────────────────────────────────────────────
    # 统一以任务 id 作为会话键（兼 thread_id）。仅 uuid 任务 id 走持久化；
    # legacy 非 uuid thread_id 保持纯内存（兼容现有测试/旧前端）。
    tid = req.task_id or req.thread_id or "default"
    task = None
    if _is_uuid(tid):
        # uuid：按任务 id 查，不存在则新建（归档/误传/首次）
        task = store.get_task(tid)
        if task is None:
            task = store.create_task("新任务")
            tid = task["id"]
    elif tid in (None, "", "default"):
        # 无有效任务 id（default/空）：新建一个承载会话
        task = store.create_task("新任务")
        tid = task["id"]
    # else: legacy 非 uuid thread_id（如 "rb"），task=None → 纯内存，不碰 store
    if task is not None and task.get("params"):
        # hydrate：锁内操作，避免与 apply_turn_guarded 的并发写竞态（F1）
        hydrate_session(tid, task.get("params"), industry=task.get("industry"))

    try:
        # 取最后一条用户消息
        last_user_msg = ""
        for m in reversed(req.messages):
            if m.role == "user":
                last_user_msg = m.content
                break

        if not last_user_msg:
            return JSONResponse({"error": "无用户输入", "thread_id": tid}, status_code=400)

        if len(last_user_msg) > _MAX_INPUT_LENGTH:
            logger.warning(f"输入过长: {len(last_user_msg)} 字符 (thread_id={tid})")
            return JSONResponse({
                "error": f"输入过长，请控制在 {_MAX_INPUT_LENGTH} 字符以内",
                "thread_id": tid,
            }, status_code=400)

        # ── 步骤 1: 意图路由（纯规则，0 LLM） ──
        # has_base 必须在 merge 之前取：弱对比词「如果」仅在已有项目参数时进 compare
        _base_snap = get_biz_snapshot(tid)
        _base_params = _base_snap.get("params") or {}
        has_base = any(
            (not str(k).startswith("_")
             and k not in ("industry", "city", "stage")
             and v not in (None, "", [], {}))
            for k, v in _base_params.items()
        )
        intent, confidence = detect_intent(last_user_msg, has_base=has_base)
        logger.info(f"意图: {intent} (置信度: {confidence:.1f}) | 输入: {last_user_msg[:50]}")

        # ── 步骤 1.5: 把本句抽出的参数累积进 SessionState（唯一真相源）──
        # Phase 3 关键修复：此前 apply_turn 只在「项目意图」分支内执行，导致
        # 被误判为 chitchat 的补参轮（如「月营收20000元」）既不写 session、
        # 也不触发重算，下一轮补参时上一轮数据即「被遗忘」。
        # 现改为：每一轮都先把抽出的参数 merge 进 session（重置命令先清状态），
        # 保证跨轮累积永不断裂；chitchat 也因 session 更新而获得正确接地背景。
        #
        # 例外：compare/report/market/benchmark 属于「一次性操作/假设分析」，
        # 本句参数不应污染 session base；base 仍沿用当前会话状态。
        params = extract_params(last_user_msg)
        industry = params.get("industry")
        if is_reset_command(last_user_msg):
            reset_state(tid)

        should_merge = (
            params
            and intent not in _STATELESS_INTENTS
        )
        guard_info: dict = {}
        if should_merge:
            # 带守门合并：自动检测「6000% vs 历史 60%」类矛盾，挂到 state 前置展示
            apply_turn_guarded(tid, params, last_user_msg, industry)
            # 审查修复 F4：合并后锁内一次取齐业务快照，不再持有共享引用锁外分次读
            snap = get_biz_snapshot(tid)
            guard_info = snap["pending_guard"]
        else:
            # 本句无任何项目参数，或意图不累积参数：沿用已有 session
            snap = get_biz_snapshot(tid)
            guard_info = snap["pending_guard"]
        # 快照已是深拷贝，下游 setdefault/工具侧写不会污染 SessionState
        merged = snap["params"]
        # 无论哪种情况，都把会话中的行业注入 params，避免下游工具丢失项目类型
        if snap["industry"]:
            merged.setdefault("industry", snap["industry"])

        # ── 步骤 1.6: 应用 LLM 上轮提议的 ops（Tier 1，人在环「应用」关键词触发）──
        # 用户回「应用」/「应用A」/「应用1」时，校验并应用上次挂到 session 的候选，
        # 重跑 quick_scan 并返回新仪表盘。LLM 自己没有执行权，靠用户的一个确认词驱动。
        apply_idx = parse_apply_command(last_user_msg)
        if apply_idx is not None:
            # 原子 pop：防止双击「应用」时 _pending_ops 被消费两次
            from session_state import pop_pending_ops
            pending_ops = pop_pending_ops(tid)
            if pending_ops:
                target_op = pending_ops[min(apply_idx, len(pending_ops) - 1)] if apply_idx == 0 \
                            else pending_ops[min(apply_idx - 1, len(pending_ops) - 1)]
                # 清空 pending，避免重复应用
                from session_state import clear_last_changes
                # apply_op 通过 apply_turn 写进 session；
                # op 若标记了 hypothesis（行业/LLM 候选），一并登记 _accepted_hypotheses
                op_basis = target_op.get("hypothesis") if isinstance(target_op, dict) else None
                ok, reason, new_merged = await asyncio.to_thread(
                    apply_op, target_op, tid, apply_turn,
                    hypothesis=op_basis,
                )
                if ok:
                    # 重算仪表盘
                    new_json = json.dumps(new_merged, ensure_ascii=False)
                    new_scan = await asyncio.to_thread(
                        lambda: json.loads(quick_scan_tool.invoke({"params_json": new_json}))
                    )
                    content = "### ✅ 已应用方案\n\n" + format_response("quick_scan", new_scan)
                    content += (
                        "\n\n（LLM 只有提议权，应用由你的「应用」关键词 + op_executor 校验共同触发。"
                        "派生字段 monthly_labor / monthly_fixed_cost / monthly_profit 由引擎重算，永不直接改。）"
                    )
                    _persist_turn(store, tid, last_user_msg, content)
                    return JSONResponse({
                        "content": content, "thread_id": tid,
                        "mode": "apply", "intent": "apply", "params": new_merged,
                        "param_sources": new_scan.get("param_sources", {}),
                        "derived": new_scan.get("derived", []),
                        "ops_available": False,
                    })
                else:
                    _persist_turn(store, tid, last_user_msg,
                                  f"### ❌ 应用失败\n\n{reason}\n\n候选方案已过期或未过校验。重新描述需求即可。")
                    return JSONResponse({
                        "content": f"### ❌ 应用失败\n\n{reason}\n\n候选方案已过期或未过校验。重新描述需求即可。",
                        "thread_id": tid, "mode": "apply", "intent": "apply", "ok": False,
                    })

        # ── 步骤 2: 业务意图 → 工具 + 模板（永不走自由 agent）──
        if intent in _PROJECT_INTENTS:
            try:
                # 引擎/工具同步计算也丢线程池，避免重计算阻塞事件循环
                routed = await asyncio.to_thread(
                    _route_intent, intent, merged, last_user_msg
                )
            except Exception as e:
                logger.exception(f"工具调用失败 (intent={intent})")
                routed = None

            if routed is not None:
                # 模板格式化
                content = format_response(routed["intent"], routed["data"])
                ops_proposals = []  # 兜底：异常路径下 return 引用仍可用
                # ── 守门高亮：把「待确认参数/矛盾」前置到内容最上方，不依赖 LLM ──
                guard_banner = _render_guard_banner(guard_info, routed["data"])
                if guard_banner:
                    content = guard_banner + "\n\n" + content
                # 规则层冲突对齐 ops（不走 LLM）：用户可「应用A/B」一键修正口径。
                has_conflict = bool(
                    (routed["data"].get("derived_issues") or [])
                    if isinstance(routed.get("data"), dict) else []
                )
                if has_conflict:
                    from field_model import conflict_resolution_ops
                    ops_proposals = conflict_resolution_ops(merged) or []
                    if ops_proposals:
                        ops_block = _render_ops_block(ops_proposals, merged, routed["data"])
                        if ops_block:
                            content += "\n\n---\n\n" + ops_block
                            from session_state import set_pending_ops
                            set_pending_ops(tid, ops_proposals)
                params_version = get_params_version(tid)
                analysis_id = str(_uuid.uuid4())
                set_last_analysis(tid, {
                    "analysis_id": analysis_id,
                    "params_version": params_version,
                    "intent": intent,
                    "user_text": last_user_msg,
                    "scan": routed.get("data") or {},
                    "params": routed.get("params") or merged,
                })
                _persist_turn(store, tid, last_user_msg, content)
                return JSONResponse({
                    "content": content,
                    "thread_id": tid,
                    "mode": "structured",
                    "intent": intent,
                    "params": routed.get("params", {}),
                    "ops_available": bool(ops_proposals),
                    "param_sources": (routed.get("data") or {}).get("param_sources", {}),
                    "derived": (routed.get("data") or {}).get("derived", []),
                    "ai_advice": {
                        "available": True,
                        "status": "idle",
                        "analysis_id": analysis_id,
                        "params_version": params_version,
                    },
                })

            # 业务意图绝不漏给自由 agent（避免编造）；工具兜不住时给友好提示
            _fallback_content = "抱歉，这条业务请求暂时无法生成结构化分析。请补充项目参数后重试（例如：月营收、总投资、人工成本）。"
            _persist_turn(store, tid, last_user_msg, _fallback_content)
            return JSONResponse({
                "content": _fallback_content,
                "thread_id": tid,
                "mode": "structured",
                "intent": intent,
            })

        # ── 步骤 3: chitchat → 走 Engine Steward（只读、看清净背景当对话伙伴）──
        # 本地工作台只用引擎管理者(llm_advisor)做解读，不引入自由 agent，
        # 不引入自由 agent，避免编造未在会话中出现的具体数字（如历史 bug「成都冒菜店/7.5万」）。
        # 审查修复 F4：锁内快照，不持有共享引用
        snapshot = get_biz_snapshot(tid)
        grounding = get_session_context(tid)
        clean_view = to_llm_view(tid, focus_fields=infer_focus_fields(last_user_msg))
        # 用当前会话状态构造 scan，供 Steward 接地解读（无状态则空 scan）
        try:
            params_json = json.dumps(snapshot.get("params", {}), ensure_ascii=False)

            def _scan_for_steward():
                return json.loads(quick_scan_tool.invoke({"params_json": params_json}))

            scan = await asyncio.to_thread(_scan_for_steward)
        except Exception:
            scan = {}
        user_text = last_user_msg or ""
        if grounding:
            user_text = (
                "[项目真实背景，请勿编造未出现的店铺名/投资额等具体数字]\n"
                f"{grounding}\n\n用户问题：{user_text}"
            )
        advice_obj = {"text": "", "ops": []}
        try:
            # 主持人模式：传入 context；8s 硬超时，超时不阻断主流程
            advise_context = _build_advise_context(tid, scan)
            advice_obj = await _advise_with_timeout(scan, user_text, clean_view, advise_context)
        except Exception as e:  # noqa
            logger.warning(f"LLM 解读跳过: {e}")
        advice_text = advice_obj.get("text", "") if isinstance(advice_obj, dict) else (advice_obj or "")
        ops_proposals = advice_obj.get("ops", []) if isinstance(advice_obj, dict) else []
        content = advice_text
        if not content:
            # 无 API key 或无可解读内容时的友好降级（仍不引入自由 agent）
            content = (
                "本工作台专注创业项目结构化分析。请告诉我项目参数"
                "（如月营收、总投资、人工成本、客单价），我来帮你测算收支平衡、跑道与利润。"
            )
        if ops_proposals:
            base_params = snapshot.get("params", {}) if isinstance(snapshot, dict) else {}
            ops_block = _render_ops_block(ops_proposals, base_params, scan)
            if ops_block:
                content += "\n\n---\n\n" + ops_block
                # 原子写入 session，等用户回「应用X」时取出 op 应用
                from session_state import set_pending_ops
                set_pending_ops(tid, ops_proposals)
        _persist_turn(store, tid, last_user_msg, content)
        return JSONResponse({
            "content": content,
            "thread_id": tid,
            "mode": "steward",
            "intent": "chitchat",
        })
    except Exception as e:
        # S1 修复：错误信息不泄露内部细节（堆栈/路径/版本号），仅内部日志记录
        logger.exception("chat 失败 (thread_id=%s): %s", tid, e)
        return JSONResponse(
            {
                "error": "服务内部错误，请稍后重试或联系开发者",
                "thread_id": tid,
            },
            status_code=500,
        )


# ─── 启动 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="创业者工作台 — 本地 Web 服务 (A)")
    parser.add_argument("-p", "--port", type=int, default=8081, help="HTTP 端口")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    args = parser.parse_args()

    # 启动前确保输出目录存在（报告/Excel 本地降级写入）
    os.makedirs(os.path.join(SCRIPT_DIR, "output"), exist_ok=True)
    os.makedirs(os.path.join(SCRIPT_DIR, "logs"), exist_ok=True)

    llm_status = "已配置" if has_api_key() else "未配置（Engine Steward 将静默跳过）"
    print("=" * 50)
    print("创业者工作台 Web 版")
    print(f"地址: http://{args.host}:{args.port}")
    print(f"模型: {MODEL_NAME}")
    print(f"版本: {APP_VERSION}")
    print(f"LLM:  {llm_status}")
    print("=" * 50)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
