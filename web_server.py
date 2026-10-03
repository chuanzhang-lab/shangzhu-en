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
        # 这条横幅会打在 stderr 上给用户看，所以也得走文案层。
        # 此刻 src 还没进 sys.path（路径设置在下面），先把路径补上再 import。
        _SRC_BOOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
        if _SRC_BOOT not in sys.path:
            sys.path.insert(0, _SRC_BOOT)
        from i18n import t as _boot_t
        print(_boot_t("ws.log.venv_switch", path=_VENV_PY), file=sys.stderr)
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
from router import rules

# 文案：展示走 i18n.t()，输入层匹配词走 router.rules（两者不得混放）
from i18n import get_locale, has as i18n_has, t

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
    """版本单源：读 pyproject.toml 的 project.version。

    ⚠️ 读不到时**不回落一个写死的版本号**。旧实现回落 `0.1.0`，那是第二个
    版本真值源：升版漏改它 → /health 静默报一个过期版本，且不报错。
    按项目「缺失不冒充」同一条原则，回落成显式的 `unknown` —— 宁可让人看见
    「读不到」，也不要看见一个看起来正常但是错的版本号。
    """
    pyproject = os.path.join(SCRIPT_DIR, "pyproject.toml")
    try:
        with open(pyproject, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("version") and "=" in line:
                    # version = "0.3.0"
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    except OSError:
        pass
    return "unknown"


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
        logger.warning(t("ws.log.llm_advise_timeout"), advise_timeout)
        return {"text": "", "ops": [], "_skipped": "timeout"}

# 对比句中常用来引出「假设/变更方案」的引导词。
# 这是**输入层匹配数据**（不是展示文案）：英文部署里若只剩中文词表，
# 「if rent were 9000」这类对比句永远切不出子句，对比意图静默退化成重算当前参数。
# 因此走 rules.compare_markers()（locale 感知），不在此处写死。


def _safe_json_loads(data: str, context: str = "tool") -> dict:
    """安全解析工具返回的 JSON；失败时返回带 error 字段的字典，不抛异常。"""
    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        logger.warning(t("ws.log.tool_not_json", context=context, err=e))
        return {"error": t("ws.log.tool_bad_format", context=context), "raw": data[:500]}
    except Exception as e:
        logger.warning(t("ws.log.tool_parse_fail", context=context, err=e))
        return {"error": t("ws.log.tool_parse_fail", context=context, err=e)}


def _extract_alt_clause(user_text: str) -> str:
    """从用户文本中提取「假设/对比」子句，用于构造方案 B。"""
    if not user_text:
        return ""
    # 优先按显式对比标记切分，取最后一段作为变更方案
    for marker in rules.compare_markers():
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
        parts.append(t("ws.guard.contradiction", msg=c.get("message", "")))
    # 致命/需确认项
    for issue in guard_info.get("issues", []) or []:
        if issue.get("level") == "critical":
            parts.append(t("ws.guard.critical", msg=issue.get("message", "")))
        elif issue.get("needs_confirmation"):
            parts.append(t("ws.guard.confirm", msg=issue.get("message", "")))
    # 引擎返回里的守门信息（派生一致性等）
    engine_guard = (scan.get("param_sources") or {}).get("_guard", {})
    for issue in (engine_guard.get("issues") or []):
        msg = issue.get("message", "")
        if msg and msg not in " ".join(parts):
            parts.append(t("ws.guard.engine", msg=msg))
    # 派生一致性矛盾（规则层先发现，月营收 vs 客流×单价 等）——最高优先级
    derived_issues = scan.get("derived_issues") or []
    for issue in derived_issues:
        msg = issue.get("message", "")
        if msg and msg not in " ".join(parts):
            parts.append(t("ws.guard.conflict", msg=msg))
    if not parts:
        return ""
    return t("ws.guard.header") + "\n".join(parts)


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


# 候选方案序号标签：A/B/C/D（**数据**，不是文案——语言只影响它外面那层「方案/Option」）
_OP_LABELS = ["A", "B", "C", "D", "E", "F"]

# 行业兜底键（**数据键**，不是文案）：行业值全程以数据键流转，只在渲染时映射成
# 展示名。放进 i18n 就等于把数据面和展示面混在一起，两处都会漂。
_DEFAULT_INDUSTRY = "通用"


def _option_name(i: int) -> str:
    """方案名「方案A / Option A」——与 op_executor 和前端正则同源。

    三处必须一致，否则用户在界面上看到 Option A、回复 Apply A 却不生效：
    1. 本函数渲染候选块；2. op_executor.parse_apply_command 解析回执；
    3. 前端 app.js 用 `ui.option_re` 正则从消息里抓出按钮。
    """
    if i >= len(_OP_LABELS):
        return str(i + 1)
    return t(f"op.option.{_OP_LABELS[i].lower()}")


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
        lines.append(t("ws.ops.current_profit", v=f"{cur_profit:,.0f}"))
    lines.append("")
    for i, op in enumerate(ops):
        tag = _OP_LABELS[i] if i < len(_OP_LABELS) else str(i + 1)
        # 「方案A/B」与「应用」都是**会被引擎和前端按字面匹配**的命令词：
        # op_executor.parse_apply_command 认「应用X」，app.js 用正则抓「**方案X**」。
        # 两处必须同源，否则英文下用户在界面上看到 Option A 却得回中文才生效。
        option = _option_name(i)
        apply_cmd = t("op.cmd.apply")
        preview = preview_op(op, base_params, quick_scan_tool)
        if not preview.get("ok"):
            lines.append(t("ws.ops.rejected", option=option,
                           reason=preview.get("reason", ""), label=preview.get("label", "")))
            continue
        p = preview.get("profit_after")
        profit_str = (t("ws.ops.money", v=f"{p:,.0f}")
                      if isinstance(p, (int, float)) else t("ws.ops.dash"))
        label = preview.get("label", "")
        chg = preview.get("changes", {})
        chg_str = t("ws.ops.joiner").join(f"{k}={v}" for k, v in chg.items())
        lines.append(
            t("ws.ops.row", option=option, label=label, changes=chg_str,
              profit=profit_str, apply=apply_cmd, tag=tag)
        )
    return "\n".join(lines)


def _render_ops_block_from_persisted(proposals: list, base_params: dict,
                                     current_scan: dict) -> str:
    """从 session 持久化的 ops 复用渲染（用户回「应用」时）。"""
    return _render_ops_block(proposals, base_params, current_scan)


def _fac_apply_for_step3():
    """暂时占位（见 chat 步骤3 的应用流）；保一致性用。"""
    pass


def _infer_sensitivity_variable(scenarios: dict, params: dict) -> str:
    """从 scenarios 推断最应分析的变量。

    优先级：客流 > 租金 > 变动成本率 > 人工。
    无 drivers 时默认分析客流（最常见的创业关切）。

    ⚠️ 判定必须靠 **driver_codes**（机器可读的变量名），不能拿关键词去匹配
    `drivers` 里的展示文案：英文部署下 drivers 是英文，中文关键词命中率恒为 0
    → 静默退化成「总是分析客流」。这是「改了文案就悄悄丢能力」的典型形态。
    """
    scenarios = scenarios if isinstance(scenarios, dict) else {}
    for code in scenarios.get("driver_codes") or []:
        if code in ("daily_traffic", "monthly_rent", "variable_cost_ratio",
                    "employee_count", "total_investment"):
            return code
    # 兼容旧调用方（只传了 drivers 文案列表）：此时无法按语言可靠匹配，
    # 直接走下面的默认值，不做关键词猜测（猜错比不猜更糟）。
    # 默认：如果用户有客流数据就分析客流，否则分析租金
    if params.get("daily_traffic") is not None:
        return "daily_traffic"
    if params.get("monthly_rent") is not None:
        return "monthly_rent"
    return "daily_traffic"


def _as_ratio(value: Any) -> Optional[float]:
    """把 variable_cost_ratio 的各种形态归一到 0~1 比例，无法解析返回 None。

    存在原因：quick_scan 的出口 params 把该字段渲染成展示串（"60%"），而内部
    计算用的是 float 0.6 —— 数值契约与展示契约混在同一 dict。消费方若直接拿去
    做算术会抛 `TypeError: unsupported operand type(s) for -: 'int' and 'str'`。
    与其在每个消费点各写一遍，统一由这里收口。

    '60%' → 0.6 | 60 → 0.6 | '0.6' → 0.6 | 0.6 → 0.6 | None/垃圾 → None
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        s = value.strip().rstrip("%").replace("％", "").strip()
        try:
            n = float(s)
        except ValueError:
            return None
        return n / 100.0 if n > 1 else n
    if isinstance(value, (int, float)):
        n = float(value)
        return n / 100.0 if n > 1 else n
    return None


def _project_view_params(params: Optional[dict]) -> dict:
    """把「引擎现算值」并入返回给前端的 params（只投影，不写回 session）。

    解决两件各自独立的事：

    1. 派生值在 session params 里根本不存在。例如用户给了「每份成本 12 元 + 客单价 15」，
       变动成本率明明能算成 0.8，但 session 只存了那两个物理量；前端参数面板的
       `if (lastParams[f] == null) return` 会直接不渲染这个字段 —— 用户能看到报表里
       写着「变动成本率 80%」，却在参数面板里找不到、也改不了它。
    2. 会话里已有的用户直述值优先，**不覆盖**（base.get(k) is None 才补），
       所以「用户说率 55% 但物理量推算是 33%」时，返回的仍是用户说的 55%。

    投影用 derive() 而不是手写 `unit_var / price`：率是多路推导，
    derive() 是引擎的唯一计算点，能覆盖所有路；这里只做「附加入响应」。
    """
    base = dict(params or {})
    try:
        from field_model import derive as _derive
        vals, _ = _derive(dict(base))
    except Exception:  # 投影是增强，绝不能因为它把整个响应打断
        logger.debug(t("ws.log.project_view_skipped"))
        return base
    for key in ("variable_cost_ratio",):
        if base.get(key) is None and vals.get(key) is not None:
            base[key] = vals[key]
    return base


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
    # 必须在**函数体开头**导入：DAYS_PER_MONTH 在下面的分支里就要用，
    # 若像旧代码那样在 for 循环内 import，它会成为本函数的局部变量，
    # 而上面的分支（513/535 行）在赋值前引用 → UnboundLocalError。
    from field_model import monthly_revenue_from_traffic, DAYS_PER_MONTH

    revenue = params.get("monthly_revenue") or 0
    fixed_cost = params.get("monthly_fixed_cost") or 0
    # 引擎出口可能给展示串（"60%"）也可能给 float(0.6)，统一收口成比例数值
    vc_ratio = _as_ratio(params.get("variable_cost_ratio"))
    daily_traffic = params.get("daily_traffic") or 0
    price = params.get("price_per_unit") or 0
    rent = params.get("monthly_rent") or 0

    if vc_ratio is None:
        # 变量名（gaps）是**数据**，展示名（message）是文案 —— 两者分开走
        return {
            "insufficient": True,
            "message": t("ws.sens.insufficient_msg"),
            "gaps": ["variable_cost_ratio"],
        }

    # 变量展示名带单位，且要跟着 locale 走（写死就会在英文输出里嵌中文标签）
    label = t(f"ws.var_label.{variable}") if i18n_has(f"ws.var_label.{variable}") else variable

    # ── 盈亏平衡点求解 ──
    # 利润 = 营收 - 固定成本 - 营收×vc_ratio = 营收×(1-vc_ratio) - 固定成本
    # 营收 = daily_traffic × price × 30
    # 所以：daily_traffic × price × 30 × (1-vc_ratio) = 固定成本
    # → daily_traffic = 固定成本 / (price × 30 × (1-vc_ratio))

    current_value = params.get(variable) or 0
    # 同一处陷阱：vcr 的 raw 值是展示串（"60%"），直接拿来算 margin 会崩
    #（见 _as_ratio 的存在说明）。
    if variable == "variable_cost_ratio":
        current_value = _as_ratio(current_value) or 0
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
            "interpretation": t("ws.sens.no_breakeven", label=label),
        }

    # ── 安全边际 ──
    if variable == "variable_cost_ratio":
        # 变动成本率越低越好，margin = 当前值 - 盈亏平衡值（负的margin=已超平衡点）
        margin = breakeven_value - current_value
        direction = (t("ws.sens.direction_up") if margin > 0
                     else t("ws.sens.direction_exceeded"))
    else:
        margin = current_value - breakeven_value
        direction = (t("ws.sens.direction_down") if margin > 0
                     else t("ws.sens.direction_exceeded"))

    margin_pct = abs(margin) / current_value if current_value > 0 else 0

    # ── 敏感度曲线（11 个点）──
    curve = []
    for i in range(11):
        pct = -0.5 + i * 0.1  # -50% 到 +50%
        v = breakeven_value * (1 + pct) if breakeven_value > 0 else 0
        if variable == "daily_traffic":
            # 月营收公式唯一出处在 field_model（曾在本文件手写 `v * price * 30`，
            # 与保本侧的 365 口径并存 → 见 F1）。DAYS_PER_MONTH 已在函数首行导入。
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
        _dir = (t("ws.sens.word_up") if variable == "variable_cost_ratio"
                else t("ws.sens.word_down"))
        interp = t(
            "ws.sens.interp_safe", label=label, be=f"{breakeven_value:g}",
            cur=f"{current_value:g}", margin=f"{abs(margin):g}",
            pct=f"{margin_pct:.0%}", dir=_dir,
        )
    else:
        _verb = (t("ws.sens.verb_exceed") if variable == "variable_cost_ratio"
                 else t("ws.sens.verb_below"))
        interp = t("ws.sens.interp_loss", label=label, cur=f"{current_value:g}",
                   verb=_verb, be=f"{breakeven_value:g}")

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
        # 出口 params 只含**输入字段**，派生值在 core_metrics 里；归因靠 monthly_revenue
        # 算变动成本/总成本，缺了就永远回落成「补充：月营收」，功能形同不可用。
        _cm = scan.get("core_metrics") or {}
        if filled_params.get("monthly_revenue") is None and _cm.get("monthly_revenue") is not None:
            filled_params["monthly_revenue"] = _cm["monthly_revenue"]
        # 同 sensitivity 的陷阱：出口 vcr 是展示串 "60%"，乘进 Variable_cost 会崩
        _vcr = _as_ratio(filled_params.get("variable_cost_ratio"))
        if _vcr is not None:
            filled_params["variable_cost_ratio"] = _vcr
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
        # 确定分析哪个变量：优先取引擎给的 driver_codes（机器可读），
        # 回退到「有客流分析客流、否则分析租金」的默认，不猜文案关键词
        variable = _infer_sensitivity_variable(scenarios, filled_params)
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
        # 行业兜底值是**数据键**（引擎按它查模板），只有展示时才映射成展示名
        industry = merged_params.get("industry") or _DEFAULT_INDUSTRY
        tool_data = benchmark_tool.invoke({"industry": industry})
        return {"intent": intent, "data": _safe_json_loads(tool_data, "search_industry_benchmarks") if isinstance(tool_data, str) else tool_data, "params": merged_params}

    elif intent == "market":
        # market 与 benchmark 共享行业基准数据；提取可选指标关键词提升查询精度。
        # 关键词是**输入层匹配数据**：拿中文词表去匹配英文提问，命中率恒为 0。
        industry = merged_params.get("industry") or _DEFAULT_INDUSTRY
        metric = ""
        lowered = (user_text or "").lower()
        for kw in rules.market_metrics():
            if kw.lower() in lowered:
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
    name: str = Field(default=t("ws.task.new"), max_length=100)


class TaskRename(BaseModel):
    name: str = Field(max_length=100)


# ─── FastAPI 应用 ──────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(t("ws.log.startup"))
    yield
    # R3/R4 修复：服务关闭时释放资源（文件描述符 + 数据库连接）
    store = get_store()
    if hasattr(store, 'close'):
        store.close()
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)
    logger.info(t("ws.log.shutdown"))


app = FastAPI(title=t("ws.app_title"), lifespan=lifespan)

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


@app.middleware("http")
async def no_cache_runtime_assets(request: Request, call_next):
    """动态/版本化资源强制回源校验（Cache-Control: no-cache）。

    背景（2026-10-03 报障「Failed to load task list」的缓存侧根因）：
    `/static/*` 与 `/i18n.js` 此前只有 ETag/Last-Modified、没有 Cache-Control，
    浏览器按「启发式新鲜度」(≈10%×(now−Last-Modified)) 直接用旧副本，长开的
    标签页更是永不重取 JS——app.js 修好的 bug 在用户页面上照旧复现。

    no-cache ≠ no-store：仍可拿 ETag/Last-Modified 走 304（省流量），
    只杜绝「不回源就用旧副本」。`/` 是动态外壳（locale/模型名/版本号都在
    渲染时注入），同样必须每次校验，否则旧外壳会指向旧的 ?v= 资源 URL。
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path in ("/", "/i18n.js"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response

# M3：前端静态资源（app.css / app.js）从 CHAT_HTML 内联抽取为独立文件，
# 由 FastAPI StaticFiles 挂载到 /static。抽取后 CHAT_HTML 仅剩 HTML 骨架。
import os as _os
_STATIC_DIR = _os.path.join(SCRIPT_DIR, "src", "web_static")
if _os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


def _static_ver(rel: str) -> str:
    """静态资源版本号 = 文件 mtime（整秒），拼进 `?v=` 做缓存失效。

    以前 `?v=20260413a` 写死、从不更新：改了 app.js，浏览器拿到的 URL 却
    一个字节没变 → 旧缓存/旧标签页继续跑修复前的代码（2026-10-01 的
    翻译函数遮蔽 bug 就是这样在用户页面上「阴魂不散」的）。
    现在文件一改 → mtime 变 → URL 变 → 缓存条目天然失效，无需手工 bump。
    """
    try:
        return str(int(os.path.getmtime(os.path.join(_STATIC_DIR, rel))))
    except (OSError, TypeError, ValueError):
        return "0"  # 文件读不到也不把 ?v=__APP_JS_VER__ 这种字面量吐给浏览器


# ─── 聊天界面 HTML ─────────────────────────────────────────────────────────
# 文案以 __T[ws.html.xxx]__ 占位，由 index() 在返回前按 locale 替换。
# 不能把文案写死进模板：HTML 是静态的，写死就等于把语言冻结在中文。
# 也不能用 f-string —— CSS 的 {} 会和占位符冲突（历史上踩过）。
CHAT_HTML = """<!DOCTYPE html>
<html lang="__LOCALE__">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__T[ws.html.title]__</title>
<link rel="stylesheet" href="/static/app.css?v=__APP_CSS_VER__">
</head>
<body>
<header>
  <h1>__T[ws.html.title]__ <span id="model-name" style="opacity:0.5;font-size:12px;">· __MODEL_NAME__</span></h1>
  <div style="display:flex;align-items:center;gap:10px;">
    <button id="settings-btn" title="__T[ws.html.settings_btn_title]__">⚙️</button>
    <span class="meta" id="status" title="__T[ws.html.status_hint]__">__T[ws.html.status_connecting]__</span>
  </div>
</header>
<div id="layout">
  <aside id="sidebar">
    <div id="sidebar-head"><span>__T[ws.html.sidebar_head]__</span><button id="new-task">__T[ws.html.new_task]__</button></div>
    <div id="task-list"></div>
  </aside>
  <div id="main">
    <div id="chat">
      <div class="empty" id="empty">
        <h2>__T[ui.empty_title]__</h2>
        <p>__T[ui.empty_desc]__</p>
        <div class="examples">
          <button class="example" data-q="__T[ws.html.example_q_coffee]__">__T[ui.example_coffee]__</button>
          <button class="example" data-q="__T[ws.html.example_q_saas]__">__T[ui.example_saas]__</button>
          <button class="example" data-q="__T[ws.html.example_q_quick]__">__T[ui.example_quick]__</button>
          <button class="example" data-q="__T[ws.html.example_q_benchmark]__">__T[ui.example_benchmark]__</button>
        </div>
      </div>
    </div>
    <div id="cat-bar">
      <button class="cat-btn active" data-cat="all">__T[ws.html.cat_all]__<span class="cat-badge"></span></button>
      <button class="cat-btn" data-cat="analyze">__T[ws.html.cat_analyze]__<span class="cat-badge"></span></button>
      <button class="cat-btn" data-cat="params">__T[ws.html.cat_params]__<span class="cat-badge"></span></button>
      <button class="cat-btn" data-cat="decide">__T[ws.html.cat_decide]__<span class="cat-badge"></span></button>
      <button class="cat-btn" data-cat="compare">__T[ws.html.cat_compare]__<span class="cat-badge"></span></button>
    </div>
    <div id="analyze-status"></div>
    <div id="analyze-controls">
      <button class="panel-btn" data-q="__T[ws.html.panel_trend]__">__T[ws.html.panel_trend]__</button>
      <button class="panel-btn" data-q="__T[ws.html.panel_cashflow]__">__T[ws.html.panel_cashflow]__</button>
      <button class="panel-btn" data-q="__T[ws.html.panel_turnaround]__">__T[ws.html.panel_turnaround]__</button>
      <button class="panel-btn" data-q="__T[ws.html.panel_benchmark]__">__T[ws.html.panel_benchmark]__</button>
      <button class="panel-btn" data-q="__T[ws.html.panel_reanalyze]__">__T[ws.html.panel_reanalyze]__</button>
    </div>
    <div id="decide-prompt"></div>
    <div id="decide-controls">
      <button class="panel-btn" data-q="__T[ws.html.decide_q1]__">__T[ws.html.decide_q1]__</button>
      <button class="panel-btn" data-q="__T[ws.html.decide_q2]__">__T[ws.html.decide_q2]__</button>
      <button class="panel-btn" data-q="__T[ws.html.decide_q3]__">__T[ws.html.decide_q3]__</button>
    </div>
    <div id="params-controls">
      <div class="pc-row" id="pc-fields"></div>
      <div class="pc-row"><button class="pc-recalc">__T[ws.html.recalc]__</button></div>
    </div>
    <div id="compare-controls">
      <div class="cc-row" id="cc-row-a"><span style="color:#666;">__T[ws.html.compare_a]__</span></div>
      <div class="cc-row" id="cc-row-b"><span>__T[ws.html.compare_b]__</span><select id="cc-field"><option value="monthly_rent">__T[ws.html.compare_monthly_rent]__</option><option value="daily_traffic">__T[ws.html.compare_daily_traffic]__</option><option value="price_per_unit">__T[ws.html.compare_price_per_unit]__</option><option value="employee_count">__T[ws.html.compare_employee_count]__</option><option value="total_investment">__T[ws.html.compare_total_investment]__</option><option value="avg_salary">__T[ws.html.compare_avg_salary]__</option><option value="variable_cost_ratio">__T[ws.html.compare_variable_cost_ratio]__</option></select><input id="cc-value" placeholder="__T[ws.html.compare_value_ph]__"><button class="cc-run">__T[ws.html.compare_run]__</button></div>
    </div>
    <div id="input-area">
      <textarea id="input" rows="1" placeholder="__T[ws.html.input_placeholder]__"></textarea>
      <button id="send">__T[ws.html.send]__</button>
    </div>
  </div>
  <aside id="params-panel">
    <div id="params-head">
      <div id="params-tabs">
        <button class="params-tab active" data-tab="params">__T[ws.html.tab_params]__</button>
        <button class="params-tab" data-tab="advisor">__T[ws.html.tab_advisor]__</button>
      </div>
    </div>
    <div id="export-btns"><button class="export-btn" id="export-pdf" disabled>__T[ws.html.export_pdf]__</button><button class="export-btn" id="export-excel" disabled>__T[ws.html.export_excel]__</button></div>
    <div id="params-list"><p style="color:#999;font-size:13px;padding:12px 0;">__T[ws.html.params_placeholder]__</p></div>
    <div id="advisor-list" style="display:none;"></div>
  </aside>
</div>
<script src="/i18n.js"></script>
<script src="/static/app.js?v=__APP_JS_VER__"></script>
</body>
</html>
"""

SETTINGS_MODAL_HTML = """
<div class="modal-overlay" id="settings-modal" style="display:none;">
  <div class="modal-box">
    <div class="modal-title">__T[ws.modal.title]__</div>
    <label class="modal-label">__T[ws.modal.model]__</label>
    <input class="modal-input" id="cfg-model" placeholder="__T[ws.modal.model_ph]__">
    <label class="modal-label">__T[ws.modal.url]__</label>
    <input class="modal-input" id="cfg-base-url" placeholder="https://api.longcat.chat/openai">
    <label class="modal-label">__T[ws.modal.key]__</label>
    <input class="modal-input" id="cfg-api-key" type="password" placeholder="sk-...">
    <div class="modal-actions">
      <button class="modal-btn cancel" id="cfg-cancel">__T[ws.modal.cancel]__</button>
      <button class="modal-btn save" id="cfg-save">__T[ws.modal.save]__</button>
    </div>
  </div>
</div>
"""

_T_TOKEN_RE = None


def _render_html_template(html: str) -> str:
    """把 __T[key]__ 占位替换成当前 locale 的文案。

    缺失键会保留成 `[i18n:missing:key]`（t() 的既有约定），
    绝不留空 —— 空白按钮比乱码更难排查。
    """
    global _T_TOKEN_RE
    if _T_TOKEN_RE is None:
        import re as _re
        _T_TOKEN_RE = _re.compile(r"__T\[([^\]]+)\]__")
    return _T_TOKEN_RE.sub(lambda m: t(m.group(1)), html)


# ─── 路由 ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    # 配置单源：模型名运行时注入（占位符替换，避免 f-string 与 CSS 花括号冲突）
    # 动态读取，保证用户通过 /settings/llm 保存后刷新页面即看到新名，无需重启
    # 文案同样在返回前替换：页面骨架是静态的，语言只能在渲染时决定
    html = _render_html_template(CHAT_HTML)
    html = html.replace("__LOCALE__", get_locale())
    html = html.replace("__MODEL_NAME__", get_model_name())
    # 资源版本号：mtime 动态注入（见 _static_ver）。占位符名故意不用 __T[..]__
    # 形式，避免被 i18n 替换器当成文案键。
    html = html.replace("__APP_CSS_VER__", _static_ver("app.css"))
    return html.replace("__APP_JS_VER__", _static_ver("app.js"))


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
async def health(request: Request):
    uptime_seconds = round(time.time() - _START_TIME, 1)
    stats = session_stats()
    # 健康检查常被当作公开探活端点，默认不再回显内部详情，
    # 避免泄露：LLM 厂商完整地址、后端降级状态（PG vs 内存）、会话数。
    # 排查时按需取 /health?detail=1。
    include_detail = request.query_params.get("detail", "") in ("1", "true")
    body = {
        "status": "ok",
        "model": get_model_name(),
        "version": APP_VERSION,
        # 观测位：用户页面跑的是哪一版 app.js，不用开浏览器也能查到。
        # 这个值本来就在 HTML 的 ?v= 里公开，不属于需要藏的内部信息。
        "static_ver": _static_ver("app.js"),
        "uptime_seconds": uptime_seconds,
        "llm_configured": has_api_key(),
    }
    if include_detail:
        body.update({
            "endpoint": get_base_url(),
            "store_backend": type(get_store()).__name__,
            "sessions": stats,
        })
    return body


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
        return JSONResponse({"error": t("ws.err.body_json")}, status_code=400)

    model = body.get("model", "")
    base_url = body.get("base_url", "")
    api_key = body.get("api_key", "")

    if not str(model).strip():
        return JSONResponse({"error": t("ws.err.model_empty")}, status_code=400)
    if api_key and len(api_key.strip()) < 8:
        return JSONResponse({"error": t("ws.err.key_short", n=len(api_key.strip()))}, status_code=400)

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
        logger.exception(t("ws.err.save_failed"))
        return JSONResponse({"error": t("ws.err.save_failed_retry")}, status_code=500)

    return JSONResponse({"ok": True, **view})


@app.post("/settings/llm/test")
async def test_llm_settings(req: Request):
    """连通性探测：验证 model+base_url+key 是否可用。"""
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": t("ws.err.body_json")}, status_code=400)

    model = body.get("model", "").strip()
    base_url = body.get("base_url", "").strip()
    api_key = body.get("api_key", "")

    if not model:
        return JSONResponse({"error": t("ws.err.model_empty")}, status_code=400)
    if not base_url:
        return JSONResponse({"error": t("ws.err.url_empty")}, status_code=400)
    # key 为空时，尝试用现有配置中的 key（保存时"留空=不修改"，测试时需要用实际 key）
    if not api_key:
        try:
            from config.settings import load
            current = load()
            api_key = current.get("config", {}).get("api_key", "")
        except Exception:
            pass
    if not api_key:
        return JSONResponse({"error": t("ws.err.key_empty")}, status_code=400)

    try:
        result = test_llm_config(model, base_url, api_key)
    except Exception as e:
        logger.exception(t("ws.err.probe_failed"))
        # 安全审查 S3：同上，异常原文不透传
        return JSONResponse({"ok": False, "error": t("ws.err.probe_failed_retry")}, status_code=500)

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
        logger.error(t("ws.log.delete_task_failed", tid=task_id, err=e))
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})
    # F6：删除任务时同步清理内存中的 session_state，防 4h TTL 到期前内存泄漏
    try:
        from session_state import reset_state
        reset_state(task_id)
    except Exception as e:
        logger.warning(t("ws.log.clear_session_failed", err=e))
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
        return JSONResponse({"error": t("ws.err.invalid_tid")}, status_code=400)

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
        logger.warning(t("ws.log.advisor_llm_failed", err=e))
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
        return JSONResponse({"error": t("ws.err.invalid_tid"), "status": "error"}, status_code=400)

    snap = get_last_analysis(tid)
    if not snap:
        return JSONResponse({
            "status": "idle",
            "text": "",
            "ops": [],
            "ops_block": "",
            "reason": t("ws.err.no_analysis"),
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
            "reason": t("ws.err.stale_analysis"),
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
            "reason": t("ws.err.params_changed"),
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
        logger.warning(t("ws.err.advice_failed", err=e))
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
            "reason": t("ws.err.advice_timeout") if status == "timeout" else t("ws.err.advice_fail"),
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
        return JSONResponse({"error": t("ws.err.invalid_tid")}, status_code=400)

    try:
        op_dict = json.loads(op)
    except json.JSONDecodeError:
        return JSONResponse({"error": t("ws.err.bad_op")}, status_code=400)

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
        "reason": preview.get("reason") or t("ws.err.preview_failed"),
    })


@app.post("/advisor/apply")
async def apply_advisor_action(tid: str, request: Request):
    """执行顾问建议的动作（与 /chat「应用 A/B」共用同一入口）。

    请求体：{op: {...}}
    动作经过 validate_op → apply_op → apply_turn，与对话区同源。
    """
    if not tid or not _is_uuid(tid):
        return JSONResponse({"error": t("ws.err.invalid_tid")}, status_code=400)

    try:
        body = await request.body()  # noqa: F821
        data = json.loads(body) if body else {}
    except Exception:
        return JSONResponse({"error": t("ws.err.bad_body")}, status_code=400)

    op_dict = data.get("op")
    if not op_dict:
        return JSONResponse({"error": t("ws.err.missing_op")}, status_code=400)

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
        logger.warning(t("ws.log.persist_failed", err=e))


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
            task = store.create_task(t("ws.task.new"))
            tid = task["id"]
    elif tid in (None, "", "default"):
        # 无有效任务 id（default/空）：新建一个承载会话
        task = store.create_task(t("ws.task.new"))
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
            return JSONResponse({"error": t("ws.err.no_input"), "thread_id": tid}, status_code=400)

        if len(last_user_msg) > _MAX_INPUT_LENGTH:
            logger.warning(t("ws.err.input_too_long_log", n=len(last_user_msg), tid=tid))
            return JSONResponse({
                "error": t("ws.err.input_too_long", n=_MAX_INPUT_LENGTH),
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
        logger.info(t("ws.log.intent_log", intent=intent, score=f"{confidence:.1f}", text=last_user_msg[:50]))

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
                    content = t("ws.apply.ok_header") + format_response("quick_scan", new_scan)
                    content += t("ws.apply.ok_note")
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
                                  t("ws.apply.fail_header") + f"{reason}" + t("ws.apply.fail_note"))
                    return JSONResponse({
                        "content": t("ws.apply.fail_header") + f"{reason}" + t("ws.apply.fail_note"),
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
                logger.exception(t("ws.log.tool_call_failed", intent=intent))
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
                    "params": _project_view_params(routed.get("params") or merged),
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
            _fallback_content = t("ws.fallback.business")
            _persist_turn(store, tid, last_user_msg, _fallback_content)
            return JSONResponse({
                "content": _fallback_content,
                "thread_id": tid,
                "mode": "structured",
                "intent": intent,
                # 兜底响应也必须带 params：前端 F3 校验拿不到 params 会提示
                # 「未采纳（当前为-）」，而参数其实已写入 session —— 纯误导。
                "params": _project_view_params(merged),
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
                t("ws.llm.grounding_prefix")
                + f"{grounding}"
                + t("ws.llm.question_prefix") + user_text
            )
        advice_obj = {"text": "", "ops": []}
        try:
            # 主持人模式：传入 context；8s 硬超时，超时不阻断主流程
            advise_context = _build_advise_context(tid, scan)
            advice_obj = await _advise_with_timeout(scan, user_text, clean_view, advise_context)
        except Exception as e:  # noqa
            logger.warning(t("ws.log.llm_advise_skip", err=e))
        advice_text = advice_obj.get("text", "") if isinstance(advice_obj, dict) else (advice_obj or "")
        ops_proposals = advice_obj.get("ops", []) if isinstance(advice_obj, dict) else []
        content = advice_text
        if not content:
            # 无 API key 或无可解读内容时的友好降级（仍不引入自由 agent）
            content = t("ws.fallback.chitchat")
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
            # 同业务兜底：chitchat 也带 params。本轮补参（如「水电费改为500」）虽被
            # 判成闲聊，参数已进 session，不带 params 会让前端显示「未采纳」。
            "params": _project_view_params(snapshot.get("params", {})),
        })
    except Exception as e:
        # S1 修复：错误信息不泄露内部细节（堆栈/路径/版本号），仅内部日志记录
        logger.exception(t("ws.log.chat_failed"), tid, e)
        return JSONResponse(
            {
                "error": t("ws.err.internal"),
                "thread_id": tid,
            },
            status_code=500,
        )


# ─── 启动 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=t("ws.cli.desc"))
    parser.add_argument("-p", "--port", type=int, default=8081, help=t("ws.cli.port_help"))
    parser.add_argument("--host", default="127.0.0.1", help=t("ws.cli.host_help"))
    args = parser.parse_args()

    # 启动前确保输出目录存在（报告/Excel 本地降级写入）
    os.makedirs(os.path.join(SCRIPT_DIR, "output"), exist_ok=True)
    os.makedirs(os.path.join(SCRIPT_DIR, "logs"), exist_ok=True)

    llm_status = t("ws.banner.llm_ready") if has_api_key() else t("ws.banner.llm_missing")
    print("=" * 50)
    print(t("ws.banner.title"))
    print(t("ws.banner.addr", host=args.host, port=args.port))
    print(t("ws.banner.model", name=MODEL_NAME))
    print(t("ws.banner.version", v=APP_VERSION))
    print(t("ws.banner.llm", s=llm_status))
    print("=" * 50)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
