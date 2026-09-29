"""抽取规则包加载器 — 输入层规则（行业词表 / 字段模式 / 边界正则 / 意图规则）的唯一出处。

为什么要有这一层：
英文版需要**第二套抽取规则**。若规则继续硬编码在 `param_extractor.py` /
`intent.py` 里，加英文就会长出两份逻辑、必然漂移。数据化后：引擎
（position 搜索 / 单位换算 / `%` 归一化）**只有一份**，语言差异只体现在
`rules/{zh,en}.yaml`。

与 `src/i18n/` 的分工（边界必须清楚）：
- `src/i18n/` = **展示文案**（给人读，走 `t()`）；
- `src/router/rules/` = **抽取规则**（给机器读：关键词、正则、单位、位置）。
两者共用同一个 locale 解析（`i18n.get_locale()`），但数据不混放——
正则住进文案层会让两边都难维护。

可靠性约定（对齐 i18n 与项目「缺失不冒充」哲学）：
- 进程内缓存，禁止每次调用读盘；
- 目标语言规则缺失/损坏 → **回退中文规则**并记 ERROR（保住能力，不丢功能）；
- 绝不抛异常中断抽取链路。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from i18n import get_locale

# 挂到 web.* 家族，继承已配置的 handler（否则降级日志只会走 lastResort 打到
# stderr，日志文件里查无此项——历史教训）。
logger = logging.getLogger("web.router.rules")

_RULES_DIR = Path(__file__).resolve().parent  # 本包目录：{zh,en}.yaml 与 __init__.py 同级
DEFAULT_LOCALE = "zh"

_cache: Dict[str, Dict[str, Any]] = {}


def _compile(pattern: str):
    """编译单条正则；失败返回永不匹配的正则并记 ERROR（绝不中断抽取链路）。"""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        # 规则是数据：写错不会抛到调用方，只会静默失配 —— 必须留下日志
        logger.error("rules: regex compile failed pattern=%r err=%s", pattern, exc)
        return re.compile(r"(?!x)x")


def _empty_pack() -> Dict[str, Any]:
    """空包：任何一项缺失都不能让调用方崩。"""
    return {
        "industry": {},
        "fields": [],
        "boundary": re.compile(r"(?!x)x"),  # 永不匹配
        "intents": [],
        "intent_priority": {},
        "number_units": {},
        "compare_markers": [],
        "market_metrics": [],
        "focus_fields": {},
        "continuation_hints": [],
        "reset_hints": [],
        "vc_ratio_patterns": [],
        "annual_rent_patterns": [],
        "labor_pair_patterns": [],
        "strong_compare_markers": [],
        "weak_compare_markers": [],
        "decision_markers": [],
        "cashflow_markers": [],
        "decision_subtypes": {},
    }


def _load(locale: str) -> Dict[str, Any]:
    """加载并缓存某语言的规则包；失败返回空包并记 ERROR（绝不抛异常）。"""
    cached = _cache.get(locale)
    if cached is not None:
        return cached

    pack = _empty_pack()
    path = _RULES_DIR / f"{locale}.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        boundary_pattern = (raw.get("boundary") or {}).get("pattern") or ""
        pack = {
            "industry": raw.get("industry") or {},
            "fields": raw.get("fields") or [],
            # 整条正则存储（含 lookahead），不拆词表——拆开即变语义
                        # 英文需大小写不敏感；中文无大小写差异，不受影响。
            "boundary": (re.compile(boundary_pattern, re.IGNORECASE)
                         if boundary_pattern else pack["boundary"]),
            "intents": [
                (
                    item.get("name"),
                    item.get("keywords") or [],
                    item.get("regexes") or [],
                )
                for item in (raw.get("intents") or [])
                if item.get("name")
            ],
            "intent_priority": raw.get("intent_priority") or {},
            "number_units": raw.get("number_units") or {},
            "compare_markers": raw.get("compare_markers") or [],
            "market_metrics": raw.get("market_metrics") or [],
            "focus_fields": raw.get("focus_fields") or {},
            "continuation_hints": raw.get("continuation_hints") or [],
            "reset_hints": raw.get("reset_hints") or [],
            # 流程型抽取器正则：编译一次缓存，避免每次抽取都 re.compile
            "vc_ratio_patterns": [
                _compile(p["regex"]) for p in (raw.get("vc_ratio_patterns") or [])
                if p.get("regex")
            ],
            "annual_rent_patterns": [
                _compile(p["regex"]) for p in (raw.get("annual_rent_patterns") or [])
                if p.get("regex")
            ],
            "labor_pair_patterns": [
                {
                    "re": _compile(p["regex"]),
                    "count": int(p.get("count_group", 1)),
                    "salary": int(p.get("salary_group", 2)),
                }
                for p in (raw.get("labor_pair_patterns") or [])
                if p.get("regex")
            ],
            "strong_compare_markers": raw.get("strong_compare_markers") or [],
            "weak_compare_markers": raw.get("weak_compare_markers") or [],
            "decision_markers": raw.get("decision_markers") or [],
            "cashflow_markers": raw.get("cashflow_markers") or [],
            "decision_subtypes": raw.get("decision_subtypes") or {},
        }
    except FileNotFoundError:
        logger.error("rules: rules file missing locale=%s path=%s", locale, path)
    except Exception as exc:  # yaml 解析失败 / 编码问题 / 结构异常
        logger.error("rules: rules load failed locale=%s path=%s err=%s", locale, path, exc)

    _cache[locale] = pack
    return pack


def _pack(locale: Optional[str] = None) -> Dict[str, Any]:
    """取当前规则包；目标语言无内容时回退中文（保住抽取能力）。"""
    loc = locale or get_locale()
    pack = _load(loc)
    if not pack["fields"] and loc != DEFAULT_LOCALE:
        logger.warning("rules: locale=%s rules unavailable, falling back to %s", loc, DEFAULT_LOCALE)
        pack = _load(DEFAULT_LOCALE)
    return pack


def reload() -> None:
    """清空缓存（测试或热更新用）。"""
    _cache.clear()


# ── 对外 API ──────────────────────────────────────────────────────────────

def industry_keywords(locale: Optional[str] = None) -> Dict[str, List[str]]:
    """行业词表（dict 顺序即优先级：命中即返回）。"""
    return _pack(locale)["industry"]


def field_patterns(locale: Optional[str] = None) -> List[Dict[str, Any]]:
    """字段抽取模式列表（顺序即优先级，先到先得）。"""
    return _pack(locale)["fields"]


def boundary_re(locale: Optional[str] = None):
    """无标点连写的切分边界正则（已编译）。"""
    return _pack(locale)["boundary"]


def intent_rules(locale: Optional[str] = None) -> List[Tuple[str, List[str], List[str]]]:
    """意图规则三元组列表：(意图名, 关键词列表, 正则列表)。"""
    return _pack(locale)["intents"]


def intent_priority(locale: Optional[str] = None) -> Dict[str, int]:
    """意图优先级（数字越大越优先）。"""
    return _pack(locale)["intent_priority"]


def number_units(locale: Optional[str] = None) -> Dict[str, int]:
    """数量级倍率表（如 万=1e4、k=1e3、million=1e6）。

    顺序即优先级：缩写写法（「1万5」）由第一个命中的单位处理。
    """
    return _pack(locale)["number_units"]


def compare_markers(locale: Optional[str] = None) -> List[str]:
    """引出「假设/变更方案」的引导词（用于从用户文本里切出方案 B 子句）。

    这是**输入层匹配数据**，不是展示文案：英文部署里若只剩中文词表，
    「如果…改成…」这类对比句永远切不出子句，对比意图静默退化成重算当前参数。
    """
    return _pack(locale)["compare_markers"]


def market_metrics(locale: Optional[str] = None) -> List[str]:
    """行业指标关键词（毛利率/获客成本/…），用于 market 意图的精度提升。

    同样是输入层匹配数据：拿中文词表去匹配英文提问，命中率恒为 0。
    """
    return _pack(locale)["market_metrics"]


def focus_fields(locale: Optional[str] = None) -> Dict[str, List[str]]:
    """关注字段关键词（关键词 → 字段名），供 LLM 视图做相关性过滤。"""
    return _pack(locale)["focus_fields"]


def continuation_hints(locale: Optional[str] = None) -> List[str]:
    """续算意图线索词（命中即强制走 merge，不重新抽取、不进 chitchat）。"""
    return _pack(locale)["continuation_hints"]


def reset_hints(locale: Optional[str] = None) -> List[str]:
    """重置命令词（清空当前会话、开启新项目）。"""
    return _pack(locale)["reset_hints"]


def vc_ratio_patterns(locale: Optional[str] = None) -> List[Any]:
    """变动成本率正则（已编译）：「占营收45% / 成本率40%」。

    必须专门处理：通用字段会把「45」抽成 45（而非 0.45），
    或被 unit_variable_cost 当成「每份 45 元」。
    """
    return _pack(locale)["vc_ratio_patterns"]


def annual_rent_patterns(locale: Optional[str] = None) -> List[Any]:
    """年租金正则（已编译）。年额当月租是 **12 倍量级** 的错误。"""
    return _pack(locale)["annual_rent_patterns"]


def strong_compare_markers(locale: Optional[str] = None) -> List[str]:
    """强对比标记词（intent.py 的 L2 裁定用）。语义开关，非文案。"""
    return _pack(locale)["strong_compare_markers"]


def weak_compare_markers(locale: Optional[str] = None) -> List[str]:
    """弱对比标记词：仅当会话已有 base 或句内确有对比结构时才保留 compare。"""
    return _pack(locale)["weak_compare_markers"]


def decision_markers(locale: Optional[str] = None) -> List[str]:
    """决策问句标记（夹带参数也应路由 decide，给「选项+风险+验证」）。"""
    return _pack(locale)["decision_markers"]


def cashflow_markers(locale: Optional[str] = None) -> List[str]:
    """现金流明细问句标记（夹带参数也应路由 cashflow；「撑多久」归 decide）。"""
    return _pack(locale)["cashflow_markers"]


def labor_pair_patterns(locale: Optional[str] = None) -> List[Dict[str, Any]]:
    """人力连写正则（已编译）：一次捕获 (人数, 薪资)，避免两者互相错抽。

    每项形如 {"re": <compiled>, "count": <组号>, "salary": <组号>}。
    """
    return _pack(locale)["labor_pair_patterns"]


def decision_subtypes(locale: Optional[str] = None) -> Dict[str, List[str]]:
    """决策子类型 → 标记词映射（intent.decide_type_of 用，顺序即优先级）。

    turnaround / validate_first / go_no_go / continue_stop / runway / choose。
    英文部署若只剩中文词表，所有英文决策问句都会落回默认 turnaround（静默错配）。
    """
    return _pack(locale)["decision_subtypes"]
