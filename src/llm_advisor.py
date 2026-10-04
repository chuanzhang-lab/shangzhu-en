"""Phase 2 · LLM 协作层（轻量、被动、只读）

把 quick_scan / trend / compare 的「结构化输出」（置信层、情景、叙事、假设清单）
接进项目已有的 DeepSeek 通道，生成自然语言解读与建议。

设计原则（护栏）：
- 被动：仅在业务结果生成后调用，不主动发起、不在用户沉默时骚扰。
- 只读：只消费结构化输出 JSON，不调任何工具、不写文件、不修改状态。
- 复用：沿用 config/agent_llm_config.json 的 DeepSeek 配置与 DEEPSEEK_API_KEY。
- 兜底：无 key 或调用失败 → 返回空字符串，绝不阻断主流程（规则渲染照常返回）。

注意：本模块的 prompt 与 agent.py 中「不聊天/不给建议」的系统提示词是**两套独立通道**——
agent.py 管 chitchat 闲聊路径，本模块管「基于标注给建议」的业务解读路径，互不干扰。
"""
import os
import json
import logging
from typing import Optional
import copy
import re
import tempfile
import threading
from pathlib import Path

import requests
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from i18n import t

# 必须挂到 "web.*" 命名空间：web_server 把 handler 装在 logging.getLogger("web") 上。
# 用 __name__ 会得到一个与 "web" 无血缘的 logger（"llm_advisor"），继承不到任何
# handler → 只走 lastResort 打一行到 stderr，**日志文件里查无此项**。LLM 401 这类
# 故障因此在排查时完全隐形（症状：点 AI 解读一片空白，日志却干净得像没事）。
logger = logging.getLogger("web.llm_advisor")
# HTTP 客户端上限：配置 timeout 不得超过此值（防 300s 拖垮并发）。
# 各入口由 web_server wait_for 分层：闲聊 steward 8s，顾问/按需解读 30s。
_LLM_TIMEOUT = 45
_llm_cache_lock = threading.Lock()

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"  # 默认值，实际优先从 config 读取


def _api_key_from_keyring(service: str, account: str) -> str:
    """从 macOS Keyring 读取 API key，读不到返回空串。"""
    import subprocess
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _api_key_from_env() -> str:
    """从环境变量读取 API key（兼容多厂商变量名）。"""
    for var in ("DEEPSEEK_API_KEY", "LONGCAT_API_KEY", "LLM_API_KEY"):
        v = os.getenv(var, "").strip()
        if v:
            return v
    return ""


def _api_key_from_config() -> str:
    """从 config JSON 读取 API key（配置单源，与 model/base_url 同源）。"""
    try:
        cfg = _load_llm_config()
        return (cfg.get("config", {}) or {}).get("api_key", "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_advisor: api key read from config failed, returning empty: %s", e)
        return ""


def _api_key() -> str:
    """运行期读取 API key，配置单源优先（config → env → keyring）。

    1) config/agent_llm_config.json（用户在设置页保存的 key，与 model/base_url 同源）
    2) 环境变量 (DEEPSEEK_API_KEY / LONGCAT_API_KEY / LLM_API_KEY)
    3) macOS Keyring (service="shangzhu-llm", account="api_key")
    4) 空串（未配置）

    config 必须优先：model / base_url 已经「配置单源」从 config 读取，若 key 反而
    优先取环境变量，会出现「config 指向 LongCat、而 env 残留 DEEPSEEK_API_KEY(sk-…)」
    的跨厂商错配，导致 401 invalid_api_key（无效的AppId）。env / keyring 仅在 config
    未写 key 时兜底，兼容纯环境变量 / Keyring 部署。
    """
    # 1) config（与 model/base_url 同源）
    key = _api_key_from_config()
    if key:
        return key

    # 2) env（兜底，兼容未在设置页存 key 的部署）
    key = _api_key_from_env()
    if key:
        return key

    # 3) keyring
    key = _api_key_from_keyring("shangzhu-llm", "api_key")
    if key:
        return key

    return ""

# 配置路径自愈：优先 SHANGZHU_WORKSPACE_PATH，其次本文件上两级仓库根，
# 最后 cwd。不依赖 web_server 是否先设环境变量，import 即用。
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_llm_config_path() -> str:
    candidates = []
    env_ws = os.getenv("SHANGZHU_WORKSPACE_PATH", "").strip()
    if env_ws:
        candidates.append(Path(env_ws) / "config" / "agent_llm_config.json")
    candidates.append(_REPO_ROOT / "config" / "agent_llm_config.json")
    candidates.append(Path.cwd() / "config" / "agent_llm_config.json")
    for p in candidates:
        try:
            if p.is_file():
                return str(p)
        except OSError:
            continue
    # 兜底：返回首选路径（_load 失败时用内置默认）
    return str(candidates[0] if candidates else "config/agent_llm_config.json")


LLM_CONFIG_PATH = _resolve_llm_config_path()

# 系统提示词（交互主持人 / 决策解说员）已外置到 src/i18n/{zh,en}.yaml 的 llm.system /
# llm.system_decision —— 随 locale 取，英文部署下 LLM 才能用英文回复。

def _is_decision_scan(scan: dict) -> bool:
    """识别结构化决策结果（decision_engine.decide 产物）。"""
    if not isinstance(scan, dict):
        return False
    return ("type" in scan and "confidence" in scan
            and ("options" in scan or "conclusion" in scan or "gaps" in scan))



def _load_llm_config() -> dict:
    # 每次解析路径，支持运行期切换 SHANGZHU_WORKSPACE_PATH / 工作目录
    path = _resolve_llm_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.debug("LLM config load failed (%s): %s", path, e)
        return {
            "config": {
                "model": "deepseek-v4-flash",
                "temperature": 0.3,
                "timeout": _LLM_TIMEOUT,
            }
        }


def get_model_name() -> str:
    """配置单源：模型名统一从 config/agent_llm_config.json 读取（web_server / 本模块共用）。"""
    return _load_llm_config().get("config", {}).get("model", "deepseek-v4-flash")


def get_base_url() -> str:
    """配置单源：LLM 端点，优先从 config 读，fallback 到默认常量。"""
    try:
        cfg = _load_llm_config()
        url = (cfg.get("config", {}) or {}).get("base_url", "")
        if url:
            return url.strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_advisor: base_url read failed, falling back to DEEPSEEK_BASE_URL: %s", e)
    return DEEPSEEK_BASE_URL


def has_api_key() -> bool:
    """是否已配置 API Key（四级优先级：config 单源 → 环境变量 → Keychain → 空；供 /health 可观测性）。"""
    return bool(_api_key())


def _build_brief(scan: dict, changes: dict = None) -> str:
    """把结构化分析结果压成给 LLM 的紧凑简报。

    changes：本轮参数变更（来自 session_state._last_changes），让 LLM
    基于「变化」讲解，而不是去翻历史原文猜（对账幻觉的根因）。
    """
    lines: list[str] = []

    # 本轮变更置顶——这是用户当下最在意的，也是避免 LLM 翻历史原文的关键
    if changes:
        lines.append(t("llm.brief.changes_header"))
        for k, d in changes.items():
            kind = d.get("kind", "changed")
            frm = d.get("from")
            to = d.get("to")
            if kind == "added" and to is not None and not str(to).startswith("_"):
                lines.append(t("llm.brief.added", k=k, to=to))
            elif kind == "changed" and to is not None and not str(to).startswith("_"):
                lines.append(t("llm.brief.changed", k=k, frm=frm, to=to))
            elif kind == "removed":
                lines.append(t("llm.brief.removed", k=k, frm=frm))
        lines.append("")

    pt = scan.get("project_type") or scan.get("industry_name") or t("llm.brief.unknown_industry")
    lines.append(t("llm.brief.project_type", pt=pt))

    if scan.get("insufficient"):
        lines.append(t("llm.brief.insufficient"))
        gaps = scan.get("gaps", [])
        if gaps:
            lines.append(t("llm.brief.gaps", gaps=t("llm.brief.sep").join(gaps)))
    else:
        cm = scan.get("core_metrics", {})
        if cm:
            lines.append(t("llm.brief.core_metrics"))
            for k in ("monthly_revenue", "monthly_fixed_cost", "monthly_profit", "runway_months"):
                if k in cm:
                    lines.append(f"  - {k}: {cm[k]}")
        # 人工分解（让 LLM 看到权威值，不再去翻历史原文猜）
        params = scan.get("params") or {}
        if params.get("monthly_labor") is not None:
            lines.append(
                t("llm.brief.labor",
                  cash=params.get("monthly_labor_cash"),
                  labor=params.get("monthly_labor"),
                  rate=params.get("labor_burden_rate"))
            )

    # 情景区间
    sc = scan.get("scenarios")
    if sc and sc.get("has_uncertainty"):
        mp = sc.get("monthly_profit", {})
        lines.append(
            t("llm.brief.scenario", best=mp.get("best"), base=mp.get("base"), worst=mp.get("worst"))
        )
        drivers = sc.get("drivers") or []
        if drivers:
            lines.append(t("llm.brief.drivers", drivers=t("llm.brief.sep").join(drivers)))

    # 叙事（已含风险杠杆，直接复用）
    if scan.get("narrative"):
        lines.append(t("llm.brief.narrative", narrative=scan["narrative"]))

    # 假设清单
    asm = scan.get("assumptions") or []
    if asm:
        lines.append(t("llm.brief.assumptions"))
        for a in asm:
            lines.append(t("llm.brief.assumption_line", field=a.get("field"), value=a.get("value"), source=a.get("source")))

    # 置信概览
    if scan.get("confidence"):
        lines.append(t("llm.brief.confidence", confidence=scan["confidence"]))

    return "\n".join(lines)


def _emit_anomaly_report(scan: dict):
    """引擎健康监控（运维闭环）：发现参数矛盾 / 极端异常，输出结构化 AnomalyReport 至日志。

    这是「提议权」而非「执行权」——绝不运行时 exec/eval/写文件，仅供人工 review 后落地。
    返回 report dict（若有异常）或 None。
    """
    src = scan.get("param_sources") or {}
    anomalies = []
    # 冲突信号在展示文案里（fixed_cost_sum_conflict 的 _codes 仍是 user/derived），
    # 且随 locale（中文「矛盾」/英文「conflicts with」），故用 locale 分桶词表匹配，
    # 不能只查状态码、也不能只匹配中文「矛盾」（英文下会静默失效——M4 同类事故）。
    from tools.pitfall_markers import conflict_markers
    _conflict_kws = conflict_markers()
    for k, v in src.items():
        if not isinstance(v, str):
            continue  # 跳过 _codes / _guard 等结构化元数据
        lower = v.lower()
        if any(kw.lower() in lower for kw in _conflict_kws):
            anomalies.append({"field": k, "source": v})
    # 极端固定成本（疑似抽取误抓，如被误抓成 1.0）
    fc = (scan.get("params") or {}).get("monthly_fixed_cost")
    if isinstance(fc, (int, float)) and fc == 1.0:
        anomalies.append({"field": "monthly_fixed_cost", "source": f"abnormal value {fc} (likely extraction mis-parse)"})
    if not anomalies:
        return None
    report = {
        "type": "AnomalyReport",
        "detected_at": "runtime",
        "anomalies": anomalies,
        "proposed_fix": "Cross-check user input and the extractor; confirm component-aggregation (C1) priority or explicit-total handling",
        "should_cover_test": "tests/test_phase4_workbench.py",
    }
    logger.warning("Engine anomaly detected: %s", json.dumps(report, ensure_ascii=False))
    return report


_llm_cache = None


def _close_llm(client) -> None:
    """R3 修复：显式关闭旧 LLM client 的底层连接（openai/httpx 连接池），
    避免配置切换时旧实例仅靠 GC __del__ 延迟释放 socket。"""
    if client is None:
        return
    try:
        inner = getattr(client, "_client", None)
        if inner is not None and hasattr(inner, "close"):
            inner.close()
        if hasattr(client, "close") and callable(client.close):
            client.close()
    except Exception as e:  # noqa: BLE001 — 关闭失败无害（缓存已作废），debug 级留痕
        logger.debug("llm_advisor: llm client close failed: %s", e)


def _get_llm() -> ChatOpenAI:
    global _llm_cache
    # 加锁：web_server 用线程池并发调用时，多个首调可能同时触发初始化竞态。
    with _llm_cache_lock:
        key = _api_key()
        cfg = _load_llm_config().get("config", {})
        model = cfg.get("model", "deepseek-v4-flash")
        base_url = get_base_url()
        # 配置变更时重建客户端（key/model/base_url 任一变化即刷新）
        cache_sig = (key, model, base_url)
        if _llm_cache is None or getattr(_llm_cache, "_shangzhu_sig", None) != cache_sig:
            cfg = _load_llm_config().get("config", {})
            # timeout 上限钳制，防止配置写成 300s 拖垮并发
            timeout = cfg.get("timeout", _LLM_TIMEOUT)
            try:
                timeout = min(float(timeout), float(_LLM_TIMEOUT))
            except (TypeError, ValueError):
                timeout = _LLM_TIMEOUT

            # 绕过代理：当 HTTP_PROXY 环境变量设置时，LLM API 请求可能因代理
            # 无法到达 api.longcat.chat 而超时。将 API 域名加入 NO_PROXY 绕过代理。
            from urllib.parse import urlparse
            _parsed = urlparse(base_url)
            _domain = _parsed.hostname
            if _domain:
                for _var in ("NO_PROXY", "no_proxy"):
                    _old = os.environ.get(_var, "")
                    if _domain not in _old:
                        os.environ[_var] = f"{_domain},{_old}" if _old else _domain

            client = ChatOpenAI(
                model=model,
                api_key=key,
                base_url=base_url,
                temperature=cfg.get("temperature", 0.3),
                timeout=timeout,
                max_completion_tokens=cfg.get("max_completion_tokens"),
                streaming=False,
            )
            client._shangzhu_sig = cache_sig  # type: ignore[attr-defined]
            _close_llm(_llm_cache)  # R3 修复：重建前关闭旧连接，防 socket 泄漏
            _llm_cache = client
        return _llm_cache  # 审查修复 F5：return 入锁，避免锁外读到半初始化状态

def invalidate_llm_cache() -> None:
    """锁内显式失效 LLM client 缓存（配置保存后调用，修复 F7 窗口）。"""
    global _llm_cache
    with _llm_cache_lock:
        _close_llm(_llm_cache)  # R3 修复：失效时同步关闭底层连接
        _llm_cache = None


def advise(scan: dict, user_text: str = "", session_snapshot: dict = None, context: dict = None) -> dict:
    """基于结构化输出生成 LLM 解读（交互主持人模式）。

    返回 {"text": 解读文本, "ops": [op...], "meta": {asked_question, made_recommendation}}：
    - text：自然语言解读（含追问/类比/翻译/推荐）
    - ops：若 LLM 输出了 ```ops 块则解析出的编排提议，由 web_server 渲染为确认流
    - meta：告诉引擎侧 LLM 做了什么（用于更新计数器）

    护栏（C3 红线，执行机制而非口号）：
    - 入参 scan / session_snapshot 一律先做 `copy.deepcopy` 只读副本，函数体
      不持有任何写引用，绝不修改调用方数据 / SessionState / 计算结果。
    - 任何面向用户的数字必来自 scan（引擎算的）；本函数体内不得出现算术表达式。
    - 引擎健康巡检（AnomalyReport）仅读 + 告警，绝不运行时 exec/eval/写文件。
    - **关键防幻觉**：脱稿 raw_text——snap_ro 中含历史用户原文（如早期『人工3500*2』），
      喂给 LLM 会引发『引擎还在用旧值』式对账幻觉。这里只用 params/industry/last_changes，
      不喂原文。
    无 key 或失败时 text 为空字符串、ops 为空列表，绝不阻断主流程。
    """
    # 只读护栏：拿到独立副本，任何后续误改都不影响调用方
    scan_ro = copy.deepcopy(scan or {})
    snap_ro = copy.deepcopy(session_snapshot or {})

    # 引擎健康巡检（运维闭环，仅读 + 告警，不落地）
    _emit_anomaly_report(scan_ro)

    if not _api_key():
        return {"text": "", "ops": []}

    # 提取本轮变更（LLM 基于变化讲，不去翻历史原文）
    changes = snap_ro.get("last_changes") if isinstance(snap_ro, dict) else None

    brief = _build_brief(scan_ro, changes=changes)
    if not brief.strip():
        return {"text": "", "ops": []}

    # 只给 LLM 清洁会话视图：params/industry/turn/last_changes，去掉 raw_text 与内部字段
    # 这是上一轮「对账幻觉」根因：raw_text 含历史用户原文会诱导 LLM 翻历史猜
    clean_snap = {}
    if isinstance(snap_ro, dict):
        for k in ("params", "grouped_params", "params_summary",
                  "accepted_hypotheses", "industry", "turn", "last_changes"):
            if k in snap_ro:
                v = snap_ro[k]
                if k == "params" and isinstance(v, dict):
                    clean_snap[k] = {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                elif k == "grouped_params" and isinstance(v, dict):
                    # 分组视图：过滤掉下划线开头的内部键
                    clean_snap[k] = {
                        g: {kk: vv for kk, vv in gv.items() if not kk.startswith("_")}
                        for g, gv in v.items()
                    }
                else:
                    clean_snap[k] = v

    grounding = ""
    if clean_snap:
        try:
            grounding = t("llm.prompt.grounding_header") + json.dumps(
                clean_snap, ensure_ascii=False
            ) + "\n\n"
        except (TypeError, ValueError):
            grounding = ""

    is_decision = _is_decision_scan(scan_ro)
    system = t("llm.system_decision") if is_decision else t("llm.system")
    if is_decision:
        user_prompt = (
            t("llm.prompt.user_input", text=user_text)
            + grounding
            + t("llm.prompt.decision_result", brief=brief)
            + t("llm.prompt.decision_instr")
        )
    else:
        user_prompt = (
            t("llm.prompt.user_input", text=user_text)
            + grounding
            + t("llm.prompt.analysis_result", brief=brief)
            + t("llm.prompt.analysis_instr")
        )
    # 主持人模式：根据 context 调整策略
    meta = {"asked_question": False, "made_recommendation": False}
    context = context or {}
    available_actions = context.get("available_actions", [])
    recommendation_count = context.get("recommendation_count", 0)
    missing_params = context.get("missing_params", [])
    has_default = context.get("has_default", {})

    # 构建主持人策略提示
    strategy_hints = []
    if missing_params:
        # 有缺失参数 -> 追问 1-2 个核心参数（补参永远优先，不受推荐计数限制）
        priority_params = [p for p in missing_params if p in (
            "monthly_rent", "daily_traffic", "price_per_unit",
            "employee_count", "avg_salary", "total_investment", "variable_cost_ratio",
        )]
        if not priority_params:
            priority_params = missing_params[:2]
        defaults_hint = ""
        for p in priority_params:
            if p in has_default:
                defaults_hint += t("llm.prompt.default_line", p=p, v=has_default[p])
        strategy_hints.append(
            t("llm.prompt.ask",
              params=", ".join(priority_params),
              defaults=defaults_hint)
        )
        meta["asked_question"] = True
    elif recommendation_count < 2 and available_actions:
        # 参数充足且推荐次数未满 -> 推荐一个动作
        # 排除「重新扫描(quick_scan)」与「对比(compare_scenarios)」，取第一个真正的下一步分析
        actionable = [a for a in available_actions if a not in ("quick_scan", "compare_scenarios")]
        pick = actionable[0] if actionable else "quick_scan"
        strategy_hints.append(t("llm.prompt.recommend", pick=pick))
        meta["made_recommendation"] = True
    else:
        strategy_hints.append(t("llm.prompt.silent"))

    # 绝不分析的硬约束
    strategy_hints.append(t("llm.prompt.no_analyze"))

    strategy_block = "\n\n".join(strategy_hints)

    try:
        resp = _get_llm().invoke([
            SystemMessage(content=system),
            HumanMessage(content=strategy_block + "\n\n" + user_prompt),
        ])
        raw_text = resp.content if isinstance(resp.content, str) else str(resp.content)
        raw_text = raw_text.strip()
        ops = _parse_ops(raw_text)
        # 从解读里剥除 ops 代码块（人不看 JSON）
        if ops:
            clean_text = _strip_ops_blocks(raw_text)
            return {"text": clean_text.strip(), "ops": ops, "meta": meta}
        return {"text": raw_text, "ops": [], "meta": meta}
    except Exception as e:  # noqa
        logger.warning(t("llm.log.advise_fail") + f": {e}")
        return {"text": "", "ops": [], "meta": meta}


# ── ops 解析（```ops ... ```）─────────────────────────────────────────────

_OPS_BLOCK_RE = re.compile(r"```ops\s*\n(.*?)```", re.DOTALL)


def _parse_ops(text: str) -> list:
    """从 LLM 输出抽 ```ops 块并解析为 op 列表；解析失败返回 []。"""
    if not text:
        return []
    out: list = []
    for m in _OPS_BLOCK_RE.finditer(text):
        body = m.group(1).strip()
        try:
            ops = json.loads(body)
        except json.JSONDecodeError as e:
            logger.warning(t("llm.log.ops_parse_fail") + f": {e}")
            continue
        if isinstance(ops, dict):
            out.append(ops)
        elif isinstance(ops, list):
            out.extend(ops)
    # 规整：确保每个 op 是 dict 且含 propose 字段
    return [o for o in out if isinstance(o, dict) and "propose" in o]


def _strip_ops_blocks(text: str) -> str:
    """从 LLM 文本里删 ```ops 块（人看的解读不展示 JSON 骨架）。"""
    return _OPS_BLOCK_RE.sub("", text)


# ── 模型配置读写（运行时切换，供 web_server /settings/llm 使用）──────────────

# 配置写锁：并发 POST 时串行化，避免两个请求同时写坏 config JSON
_config_write_lock = threading.Lock()


def _mask_key(key: str) -> str:
    """脱敏 API key：保留头尾各 4 位，中间打码。过短则整体打码。"""
    k = (key or "").strip()
    if len(k) <= 8:
        return "*" * len(k) if k else ""
    return f"{k[:4]}****{k[-4:]}"


def get_llm_config_view() -> dict:
    """给 /settings/llm 的脱敏配置视图：只回显掩码，绝不暴露完整 key。

    审查修复 F6：与 config.settings 双实现并存时返回结构不一致（此处平铺、
    settings 包裹 config 键），且互不感知锁。现统一委托 config.settings
    （它有 _CONFIG_LOCK 保护的读-改-写），本函数仅作兼容入口保留。
    """
    try:
        from config.settings import get_llm_config_view as _settings_view
        return _settings_view()
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_advisor: settings view delegate failed, falling back to local read: %s", e)
    cfg = _load_llm_config().get("config", {})
    return {
        "model": cfg.get("model", "deepseek-v4-flash"),
        "base_url": cfg.get("base_url", DEEPSEEK_BASE_URL),
        "api_key_masked": _mask_key(cfg.get("api_key", "") or _api_key()),
        "llm_configured": has_api_key(),
    }


def save_llm_config(model: str, base_url: str, api_key: str) -> dict:
    """运行时保存模型配置到 agent_llm_config.json（原子写 + 锁）。

    - model / base_url 直接覆盖
    - api_key 为空字符串 → 不覆盖已存 key（允许只改名字/URL）
    - api_key 非空但 < 8 字符 → 抛 ValueError（前端已校验，后端双保险）
    - 保留 config 里其它字段（temperature/top_p/...）与顶层 sp/tools

    返回写入后的配置视图。

    审查修复 F6：主路径已统一走 config.settings.save_llm_config（web_server
    导入），为避免两套实现各持各的锁互不互斥，本函数委托 settings 实现，
    外层 _config_write_lock 保留作兼容（同一进程内先入 settings 锁再入此锁，
    顺序固定不会死锁）。
    """
    global _llm_cache
    try:
        from config.settings import save_llm_config as _settings_save
        view = _settings_save(model, base_url, api_key)
        # settings 写成功后同步失效 LLM client 缓存（修复 F7：缩短旧配置残留窗口）
        with _llm_cache_lock:
            _close_llm(_llm_cache)  # R3 修复：关闭旧连接
            _llm_cache = None
        return view
    except ValueError:
        raise  # 参数校验错误原样抛
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_advisor: config save delegate failed, falling back to local write: %s", e)
    with _config_write_lock:
        key_raw = (api_key or "").strip()
        if key_raw and len(key_raw) < 8:
            raise ValueError(t("llm.err.key_too_short", n=len(key_raw)))

        path = _resolve_llm_config_path()
        # 基底：读现有文件（保留 sp/tools 等非 config 字段）
        try:
            with open(path, "r", encoding="utf-8") as f:
                full = json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.warning("llm_advisor: llm config file unreadable, using default base: %s", e)
            full = _load_llm_config()  # 文件缺失/损坏 → 用默认基底

        if not isinstance(full, dict):
            full = {}
        cfg = full.get("config") or {}
        if not isinstance(cfg, dict):
            cfg = {}
            full["config"] = cfg

        if model is not None and str(model).strip():
            cfg["model"] = str(model).strip()
        if base_url is not None and str(base_url).strip():
            cfg["base_url"] = str(base_url).strip()
        if key_raw:
            cfg["api_key"] = key_raw

        full["config"] = cfg

        # 原子写：临时文件 + os.replace，避免半截写入损坏配置
        tmp = None
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d or ".", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(full, f, ensure_ascii=False, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # 强制重建 LLM client（缓存失效；虽然 _get_llm 有签名比对，显式置 None 更保险）
    with _llm_cache_lock:
        _llm_cache = None

    return get_llm_config_view()


def _validate_llm_url(base_url: str) -> Optional[str]:
    """安全审查 S6 修复：校验探测目标 URL，阻断 SSRF 内网探测。

    规则：仅允许 https；禁止指向内网/回环/链路本地/私有地址段。
    返回 None 表示通过，否则返回拒绝原因。
    """
    import ipaddress
    from urllib.parse import urlparse

    u = urlparse((base_url or "").strip())
    if u.scheme != "https":
        return t("llm.err.only_https", scheme=(u.scheme or "none"))
    host = u.hostname or ""
    if not host:
        return t("llm.err.no_host")
    # 回环/内网字面量
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return t("llm.err.banned_ip", host=host)
        return None  # 公网 IP 直接放行
    except ValueError:
        pass  # 是域名，继续检查
    # 内网域名后缀/惯用名
    _BANNED_HOSTS = ("localhost", "localhost.localdomain", "metadata.google.internal",
                     "169.254.169.254", "instance-data")
    if host.lower() in _BANNED_HOSTS or host.lower().endswith(".internal") or host.lower().endswith(".local"):
        return t("llm.err.banned_domain", host=host)
    return None

def test_llm_config(model: str, base_url: str, api_key: str) -> dict:
    """连通性探测：发一次最小请求验证 model+base_url+key 是否可用。

    返回：
    - {"ok": bool, "status_code": int|None, "error": str, "latency_ms": int}
    - 超时 5s，避免拖慢保存体验
    - 用 max_tokens=1 极简请求，几乎不消耗额度

    安全审查 S6 修复：仅允许 https 公网地址（防 SSRF 内网探测），
    错误信息不再回显响应体（防借探测读取内网服务内容）。
    """
    import time as _time
    # SSRF 防护：探测前校验目标
    reject = _validate_llm_url(base_url)
    if reject:
        return {"ok": False, "status_code": None, "error": t("llm.err.target_rejected", reason=reject), "latency_ms": 0}
    try:
        from requests import post, exceptions
    except ImportError:
        return {"ok": False, "status_code": None, "error": t("llm.err.requests_missing"), "latency_ms": 0}

    # 本次探测绕开代理。此处刻意保留 requests.post 调用（便于测试 monkeypatch），
    # 因此采用「临时改写 + 最终还原」而非原地追加：旧实现从不还原，且每次调用
    # 都往 NO_PROXY 里塞一个域名，长期运行会无限膨胀并把越来越多站点排除出代理。
    from urllib.parse import urlparse

    _domain = urlparse(base_url).hostname
    _saved_env: dict = {}
    if _domain:
        for _var in ("NO_PROXY", "no_proxy"):
            _saved_env[_var] = os.environ.get(_var)
            _old = _saved_env[_var] or ""
            if _domain not in _old:
                os.environ[_var] = f"{_domain},{_old}" if _old else _domain

    url = (base_url or "").strip().rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {(api_key or '').strip()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": (model or "").strip(),
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    t0 = _time.time()
    try:
        resp = post(url, headers=headers, json=payload, timeout=5, verify=True)
        latency = int((_time.time() - t0) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "status_code": 200, "error": "", "latency_ms": latency}
        # 安全审查 S6：不回显响应体（防借探测接口读取内网服务内容），只回状态码
        return {"ok": False, "status_code": resp.status_code, "error": t("llm.err.http_non_200", code=resp.status_code), "latency_ms": latency}
    except exceptions.Timeout:
        latency = int((_time.time() - t0) * 1000)
        return {"ok": False, "status_code": None, "error": t("llm.err.probe_timeout"), "latency_ms": latency}
    except exceptions.ConnectionError as e:
        latency = int((_time.time() - t0) * 1000)
        return {"ok": False, "status_code": None, "error": t("llm.err.conn_fail", e=e), "latency_ms": latency}
    except Exception as e:  # noqa: BLE001
        logger.warning("llm_advisor: connectivity probe failed: %s", e)
        latency = int((_time.time() - t0) * 1000)
        return {"ok": False, "status_code": None, "error": t("llm.err.probe_error", e=e), "latency_ms": latency}
    finally:
        # 还原 NO_PROXY：探测是一次性动作，不该留下永久性的环境副作用。
        for _var, _original in _saved_env.items():
            if _original is None:
                os.environ.pop(_var, None)
            else:
                os.environ[_var] = _original
