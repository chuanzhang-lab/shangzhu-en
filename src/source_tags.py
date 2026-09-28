"""参数来源标注（source tag）：**状态码**与**展示文案**分离。

为什么必须拆（M2 审计发现，M4 落实）：

引擎历来把来源标注写成单个中文串 ``"[用户] 未提供"``，而展示层、决策层、
置信层都用 ``startswith("[用户]")`` 对它做**语义判断** —— 一份数据同时充当
状态码和展示文案。M4 一旦把文案翻译成英文，``"[User]"`` 不再匹配 ``"[用户]"``，
这些分支会**静默走错**（不报错、不崩溃，只是结论错了），与本项目已发生过
的「静默降级丢能力」是同一类事故。

拆解后的两条通道：

- **状态码**（本模块常量）：``user`` / ``derived`` / ``candidate`` / ``missing`` /
  ``conflict``。语言无关，只给机器读，**永不展示给用户**。
- **展示文案**：``t("src.<key>")``。只给人读，**永不参与语义判断**。

``src`` 字典同时携带两份：``字段 → 展示串``（照旧，展示层零改动），
``_codes → {字段: 状态码}``。``_`` 前缀内部键，与既有 ``_guard`` 同例，
不参与业务计算，也不进展示表格。

兼容：``code_of()`` 在 ``_codes`` 缺失时回退到旧中文标记解析，
这样「外部构造的 src」（测试手写、历史存档）不会静默变成未知码。
"""

from __future__ import annotations

from typing import Any, Dict

from i18n import t

# ── 状态码（语言无关，永不变）────────────────────────────────────────────
USER = "user"            # 用户直接给出
DERIVED = "derived"      # 由用户给出的基础值按公式推导（[推算] / [推导]）
CANDIDATE = "candidate"  # 行业模板候选（[候选]）
MISSING = "missing"      # 缺失，不虚构（[缺失]）
CONFLICT = "conflict"    # 自相矛盾，待澄清（[矛盾]）

# src 字典里承载状态码表的内部键
CODES_KEY = "_codes"

# 旧中文标记 → 状态码。仅用于「没有 _codes 的 src 字典」的兼容回退。
# ⚠️ 这里不是文案，是**协议常量**：改动它等价于改接口，必须与历史存档对齐。
_LEGACY_MARKS = (
    ("[用户]", USER),
    ("[缺失]", MISSING),
    ("[候选]", CANDIDATE),
    ("[推算]", DERIVED),
    ("[推导]", DERIVED),
    ("[矛盾]", CONFLICT),
)


def legacy_code(tag: str) -> str:
    """从旧中文标记解析状态码；解析不出返回空串（未知就是未知，不冒充）。"""
    for mark, code in _LEGACY_MARKS:
        if tag.startswith(mark):
            return code
    return ""


def code_of(param_sources: Dict[str, Any], key: str) -> str:
    """取字段状态码：优先读 ``_codes``，缺失时回退旧中文标记解析。

    回退是为了兼容外部构造的 src（测试手写、历史会话存档）；
    引擎自身产出的 src 一定带 ``_codes``，不会走到回退分支。
    """
    if not isinstance(param_sources, dict):
        return ""
    codes = param_sources.get(CODES_KEY)
    if isinstance(codes, dict):
        got = codes.get(key)
        if isinstance(got, str) and got:
            return got
    return legacy_code(str(param_sources.get(key) or ""))


def mark(code: str) -> str:
    """状态码 → 方括号标记文案（``user`` → ``[用户]`` / ``[User]``）。

    只用于需要**拼装**标记的场景（如固定成本组件串 ``租金[用户] + 人工[推算]``）；
    完整句式文案请把标记直接写进 yaml 值，避免拼装产生语言间的语序差异。
    """
    return t(f"src.mark.{code}")


def set_tag(
    param_sources: Dict[str, Any],
    key: str,
    code: str,
    copy_key: str,
    **kwargs: Any,
) -> str:
    """写入展示文案 + 状态码，返回展示文案（供调用方直接使用）。"""
    text = t(f"src.{copy_key}", **kwargs)
    param_sources[key] = text
    codes = param_sources.get(CODES_KEY)
    if not isinstance(codes, dict):
        codes = {}
        param_sources[CODES_KEY] = codes
    codes[key] = code
    return text
