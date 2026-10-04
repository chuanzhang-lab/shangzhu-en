"""参数来源标注（source tag）：**状态码**与**展示文案**分离。

为什么必须拆（M2 审计发现，M4 落实）：

引擎历来把来源标注写成单个中文串 ``"[用户] 未提供"``，而展示层、决策层、
置信层都用 ``startswith("[用户]")`` 对它做**语义判断** —— 一份数据同时充当
状态码和展示文案。M4 一旦把文案翻译成英文，``"[User]"`` 不再匹配 ``"[用户]"``，
这些分支会**静默走错**（不报错、不崩溃，只是结论错了），与本项目已发生过
的「静默降级丢能力」是同一类事故。

拆解后的两条通道：

- **状态码**（本模块常量）：``user`` / ``derived`` / ``candidate`` / ``missing`` /
  ``conflict`` / ``incomplete``。语言无关，只给机器读，**永不展示给用户**。
  ``incomplete`` 是 2026-10 补的第六个码：值算出来了，但**缺核心组件**，
  因此系统性偏低（典型：只给租金、人工未提供 → 固定成本缺 3~5 成）。
  它必须单列而不能并入 ``derived``：并入就等于「算出来了=算全了」，
  下游（报表、LLM、门禁）再也看不出成本被低估。
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
INCOMPLETE = "incomplete"  # 有值但不完整（缺核心组件）→ 值偏低，非「算全了」

# src 字典里承载状态码表的内部键
CODES_KEY = "_codes"

# 引擎的「跑道无限」哨兵：靠**等值比较**识别，不是文案。
# ⚠️ 它是历史协议值（存档里已有该字符串），改它等价于改接口，故保持中文原样；
# 但**绝不能直接进输出结构** —— 出口必须映射成 t("wf.common.infinite")
# （中文侧该键的值就是「无限」，故中文输出逐字不变）。
# 放在 source_tags 而不是各模块各写一份：formatter / workflow_engine /
# decision_engine 三处都在比较它，散落定义 = 漂移来源。
INFINITE_MARK = "无限"

# ── 状态码全集（顺序固定，供展示文案反查遍历使用）──────────────────────────
CODING_ORDER_NOTE = "新增状态码必须写进 _ALL_CODES，否则展示文案反查会漏掉它"
_ALL_CODES = (USER, DERIVED, CANDIDATE, MISSING, CONFLICT, INCOMPLETE)

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


def code_of_display(display: str) -> str:
    """展示文案 → 状态码：**按当前 locale 反查**，再兜底历史中文标记。

    为什么需要它：`legacy_code()` 只能解析**中文**标记，en 部署下
    ``mark(MISSING) == "[Missing]"`` 一律解析不出来（返回 ""）。
    所以"展示串 → 状态码"这条逆路径必须由 ``mark()`` 这个 SSOT 生成端自己
    来反查 —— 逐个码生成当前语言的标记再比对，**新增语言无需改本函数**。

    顺序：先当前 locale 的 mark 值（绝大多数），再 legacy 中文标记
    （历史存档/手写 fixture）；两者都查不出返回 ""（未知就是未知，不冒充）。
    """
    tag = (display or "").strip()
    if not tag:
        return ""
    for code in _ALL_CODES:
        m = mark(code)
        if m and tag.startswith(m):
            return code
    return legacy_code(tag)


def legacy_code(tag: str) -> str:
    """从旧中文标记解析状态码；解析不出返回空串（未知就是未知，不冒充）。"""
    for mark_, code in _LEGACY_MARKS:
        if tag.startswith(mark_):
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
    return code_of_display(str(param_sources.get(key) or ""))


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
