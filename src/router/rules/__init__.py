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


def _empty_pack() -> Dict[str, Any]:
    """空包：任何一项缺失都不能让调用方崩。"""
    return {
        "industry": {},
        "fields": [],
        "boundary": re.compile(r"(?!x)x"),  # 永不匹配
        "intents": [],
        "intent_priority": {},
        "number_units": {},
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
        }
    except FileNotFoundError:
        logger.error("rules: 规则文件缺失 locale=%s path=%s", locale, path)
    except Exception as exc:  # yaml 解析失败 / 编码问题 / 结构异常
        logger.error("rules: 规则加载失败 locale=%s path=%s err=%s", locale, path, exc)

    _cache[locale] = pack
    return pack


def _pack(locale: Optional[str] = None) -> Dict[str, Any]:
    """取当前规则包；目标语言无内容时回退中文（保住抽取能力）。"""
    loc = locale or get_locale()
    pack = _load(loc)
    if not pack["fields"] and loc != DEFAULT_LOCALE:
        logger.warning("rules: locale=%s 规则不可用，回退 %s", loc, DEFAULT_LOCALE)
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
