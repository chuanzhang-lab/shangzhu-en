"""多语言文案层（i18n）— 输出层展示文案的唯一出处。

设计约定（详见 docs/PLAN_I18N_EN.md）：

1. 文案外置到 `src/i18n/{zh,en}.yaml`，业务代码只按键取值，不再硬编码中文。
   语言是**部署级 profile**（环境变量 `SHANGZHU_LOCALE`，默认 `en`），
   不做运行时 UI 切换 —— 运行时切会让已抽取参数进入混合语言状态，
   与项目「缺失不冒充 0」是同一类隐患。

2. locale 用 `contextvars.ContextVar` 承载：默认取环境变量，可被会话级覆盖。
   这样将来若要支持「每会话一种语言」，无需改动任何函数签名。

3. 缺失处理遵循「缺失不冒充」哲学，绝不静默返回空串：
   - 目标语言缺键 → 回退默认语言（en）；
   - 默认语言也缺 → 返回显式标记 `[i18n:missing:<key>]`，日志 ERROR。
   文案层永不抛异常中断业务链路（format 失败时返回未填充原文并记 ERROR）。

4. 资源在首次使用时加载并进程内缓存，禁止每次调用读盘。
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# 与 src/storage/local_store.py 一致：挂到 web.* 家族，继承已配置的 handler
logger = logging.getLogger("web.i18n")

_LANG_DIR = Path(__file__).resolve().parent
# 部署级默认语言。此处是 SSOT：rules 层的 DEFAULT_LOCALE 从这里导入，
# 不得再各自定义一份（曾有两处定义，会漂移）。
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "zh")
_ENV_KEY = "SHANGZHU_LOCALE"

# None 表示「未显式设置」，此时回退到环境变量（部署级 profile）
_locale_ctx: ContextVar[Optional[str]] = ContextVar("shangzhu_locale", default=None)

_cache: Dict[str, Dict[str, str]] = {}

# t() 的缺失标记前缀，供调用方判断「这个键有没有真的取到」
_MISSING_PREFIX = "[i18n:missing:"


def _env_locale() -> str:
    """读部署级 locale 配置；非法值一律回退默认语言（不静默用错语言）。"""
    raw = (os.environ.get(_ENV_KEY) or "").strip().lower()
    if raw in SUPPORTED_LOCALES:
        return raw
    if raw:
        logger.warning("i18n: unsupported %s=%r, falling back to %s", _ENV_KEY, raw, DEFAULT_LOCALE)
    return DEFAULT_LOCALE


def get_locale() -> str:
    """当前 locale：会话级覆盖 > 部署级环境变量 > 默认语言（en）。"""
    return _locale_ctx.get() or _env_locale()


def set_locale(locale: str) -> None:
    """设置会话级 locale（仅影响当前上下文）。非法值不生效并告警。"""
    if locale not in SUPPORTED_LOCALES:
        logger.warning("i18n: set_locale got unsupported locale=%r, ignored", locale)
        return
    _locale_ctx.set(locale)


def reset_locale() -> None:
    """清除会话级覆盖，回归部署级配置。"""
    _locale_ctx.set(None)


def _flatten(raw: Any, prefix: str = "") -> Dict[str, str]:
    """把嵌套 yaml 摊平成点分键（{'a': {'b': 'x'}} -> {'a.b': 'x'}）。"""
    out: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(val, dict):
            out.update(_flatten(val, full))
        else:
            out[full] = str(val)
    return out


def _load(locale: str) -> Dict[str, str]:
    """加载并缓存某语言资源；失败时返回空表并记 ERROR（绝不崩溃）。"""
    cached = _cache.get(locale)
    if cached is not None:
        return cached
    path = _LANG_DIR / f"{locale}.yaml"
    table: Dict[str, str] = {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        table = _flatten(raw)
    except FileNotFoundError:
        logger.error("i18n: resource file missing locale=%s path=%s", locale, path)
    except Exception as exc:  # yaml 解析失败 / 编码问题等
        logger.error("i18n: resource load failed locale=%s path=%s err=%s", locale, path, exc)
    _cache[locale] = table
    return table


def reload() -> None:
    """清空缓存（测试或热更新用）。"""
    _cache.clear()


def has(key: str) -> bool:
    """键在当前 locale（或中文回退）下是否存在。

    给「有才显示」的场景用：例如行业基准只覆盖了 12 个行业，
    「自定义 / 其他」没有基准条目 —— 调用方应先问再取，
    否则会拿到 `[i18n:missing:...]` 标记串直接漏给用户。
    """
    locale = get_locale()
    return key in _load(locale) or key in _load(DEFAULT_LOCALE)


def industry_name(key: str) -> str:
    """行业**数据键** → 展示名（只在渲染时映射，数据面原样不动）。

    为什么不能直接把 industry 翻成英文：
    `industry` 在引擎里是**数据值**（餐饮 / SaaS / …），行业模板查找、
    `is_tech_project` 判定、别名解析全靠它。翻译键本身 = 让
    `industry_templates` 查不到模板（同类事故：把行业名当文案翻）。
    所以这里做的是**展示名映射**，键本身永远不动。

    缺映射时回退数据键本身并记 ERROR —— 宁可露出原始键，也不冒充空白
    （与「缺失不冒充 0」同一条原则）。
    """
    if not key:
        return ""
    name = t(f"industry.name.{key}")
    if name.startswith(_MISSING_PREFIX):
        logger.error("i18n: industry display name missing industry=%s", key)
        return key
    return name


# 行业基准的四项展示维度。键名与 bench.* 的子块一一对应，
# 顺序即渲染顺序，改这里等于改输出结构（前端按这个顺序画）。
_BENCH_GROUPS = ("traffic", "margin", "breakeven", "warning")

# 结构化输出里的展示字段名（历史契约，前端在读，不能改）。
_BENCH_OUT_FIELDS = {
    "traffic": "daily_traffic_range",
    "margin": "typical_profit_margin",
    "breakeven": "avg_breakeven_months",
    "warning": "key_warning",
}


def benchmark_view(industry_key: str, bench: dict) -> dict:
    """行业基准的**出口视图**：数值来自配置，词来自 i18n。

    为什么必须有这一层：
    `config/industry_templates.yaml` 里曾经同时躺着数值和展示串（"80-250 杯"、
    "新店前 3 个月客流…"），结构化输出直接把整个 dict 倒给用户 —— 英文部署
    每次扫描都漏中文，而 en.yaml 干干净净、静态守卫全绿（查不到这种泄漏）。

    现在数据文件只留数值（locale-free），四项展示串由本函数按
    `bench.<group>.<行业键>` 取；行业没被覆盖（未识别 / 自定义）时走
    `bench.fallback.*`，绝不把缺失键标记串倒给用户。

    数值字段原样透传 —— 它们是 `_benchmark_check` 的输入，与语言无关。
    """
    data = dict(bench or {})
    view = {k: v for k, v in data.items() if k not in _BENCH_OUT_FIELDS.values()}
    for group in _BENCH_GROUPS:
        key = f"bench.{group}.{industry_key}" if industry_key else ""
        if key and has(key):
            value = t(key)
        else:
            value = t(f"bench.fallback.{group}")
        view[_BENCH_OUT_FIELDS[group]] = value
    return view


def t(key: str, **kwargs: Any) -> str:
    """取文案。

    回退链：当前 locale → 默认语言（en）→ 显式缺失标记。
    任何情况下都不抛异常、不返回空串。
    """
    locale = get_locale()
    template = _load(locale).get(key)
    if template is None and locale != DEFAULT_LOCALE:
        template = _load(DEFAULT_LOCALE).get(key)
    if template is None:
        logger.error("i18n: missing key locale=%s key=%s", locale, key)
        return f"{_MISSING_PREFIX}{key}]"
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except Exception as exc:
        logger.error(
            "i18n: placeholder fill failed key=%s kwargs=%s err=%s", key, kwargs, exc
        )
        return template
