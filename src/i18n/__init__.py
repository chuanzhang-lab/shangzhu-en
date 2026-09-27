"""多语言文案层（i18n）— 输出层展示文案的唯一出处。

设计约定（详见 docs/PLAN_I18N_EN.md）：

1. 文案外置到 `src/i18n/{zh,en}.yaml`，业务代码只按键取值，不再硬编码中文。
   语言是**部署级 profile**（环境变量 `SHANGZHU_LOCALE`，默认 `zh`），
   不做运行时 UI 切换 —— 运行时切会让已抽取参数进入混合语言状态，
   与项目「缺失不冒充 0」是同一类隐患。

2. locale 用 `contextvars.ContextVar` 承载：默认取环境变量，可被会话级覆盖。
   这样将来若要支持「每会话一种语言」，无需改动任何函数签名。

3. 缺失处理遵循「缺失不冒充」哲学，绝不静默返回空串：
   - 目标语言缺键 → 回退中文；
   - 中文也缺 → 返回显式标记 `[i18n:missing:<key>]`，日志 ERROR。
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
DEFAULT_LOCALE = "zh"
SUPPORTED_LOCALES = ("zh", "en")
_ENV_KEY = "SHANGZHU_LOCALE"

# None 表示「未显式设置」，此时回退到环境变量（部署级 profile）
_locale_ctx: ContextVar[Optional[str]] = ContextVar("shangzhu_locale", default=None)

_cache: Dict[str, Dict[str, str]] = {}


def _env_locale() -> str:
    """读部署级 locale 配置；非法值一律回退中文（不静默用错语言）。"""
    raw = (os.environ.get(_ENV_KEY) or "").strip().lower()
    if raw in SUPPORTED_LOCALES:
        return raw
    if raw:
        logger.warning("i18n: 不支持的 %s=%r，回退 %s", _ENV_KEY, raw, DEFAULT_LOCALE)
    return DEFAULT_LOCALE


def get_locale() -> str:
    """当前 locale：会话级覆盖 > 部署级环境变量 > zh。"""
    return _locale_ctx.get() or _env_locale()


def set_locale(locale: str) -> None:
    """设置会话级 locale（仅影响当前上下文）。非法值不生效并告警。"""
    if locale not in SUPPORTED_LOCALES:
        logger.warning("i18n: set_locale 收到不支持的 locale=%r，已忽略", locale)
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
        logger.error("i18n: 资源文件缺失 locale=%s path=%s", locale, path)
    except Exception as exc:  # yaml 解析失败 / 编码问题等
        logger.error("i18n: 资源加载失败 locale=%s path=%s err=%s", locale, path, exc)
    _cache[locale] = table
    return table


def reload() -> None:
    """清空缓存（测试或热更新用）。"""
    _cache.clear()


def t(key: str, **kwargs: Any) -> str:
    """取文案。

    回退链：当前 locale → 中文 → 显式缺失标记。
    任何情况下都不抛异常、不返回空串。
    """
    locale = get_locale()
    template = _load(locale).get(key)
    if template is None and locale != DEFAULT_LOCALE:
        template = _load(DEFAULT_LOCALE).get(key)
    if template is None:
        logger.error("i18n: 缺失键 locale=%s key=%s", locale, key)
        return f"[i18n:missing:{key}]"
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except Exception as exc:
        logger.error(
            "i18n: 占位符填充失败 key=%s kwargs=%s err=%s", key, kwargs, exc
        )
        return template
