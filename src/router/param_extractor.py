"""参数提取器 — 自然语言→JSON 参数

纯正则 + 关键词 map，不调 LLM。
目标延迟: <100ms。

支持字段：
- industry: 行业
- total_investment: 总投资
- monthly_rent: 月租金
- employee_count: 员工人数
- avg_salary: 人均月薪
- daily_traffic: 日均客流
- price_per_unit: 客单价
- monthly_revenue: 月营收
- variable_cost_rate: 变动成本率

策略:
1. 按"，" / "。" / "；" / 空格切分文本为段
2. 每段独立提取参数，避免跨段数字污染
3. 段内根据 position 找数字（before/after/any）
"""

import re
from typing import Dict, Optional, List, Tuple

from . import rules


# ─── 行业关键词 map ───────────────────────────────────────────────────────
# ─── 行业关键词 ────────────────────────────────────────────────────────────
# 数据化：12 个行业的词表已迁到 src/router/rules/{zh,en}.yaml。
# 两条仍然生效的规则（原注释保留，供维护者对照）：
# 1. **大小写不敏感**（F7）：「做 MCN」与「做 mcn」都应命中「内容」。
# 2. **dict 顺序即优先级**（F5）：命中即返回，更具体的行业排在更泛的之前
#    ——「宠物医院」同时含「宠物」和「医院」，宠物必须排在医疗前面。
# 由 test_cov_f4_every_template_industry_is_reachable 守住 12 行业可达。
#
# 此处保留 **zh 快照**绑定（该测试 import 本名 + 向后兼容）；
# 实际抽取走 rules.industry_keywords()，按当前 locale 取。
INDUSTRY_KEYWORDS = rules.industry_keywords("zh")


def _detect_industry(text: str) -> Optional[str]:
    """根据关键词检测行业。

    两条容易踩的规则：

    1. **大小写不敏感**（F7）：用户输入「做 MCN」和「做 mcn」都应命中「内容」。
       旧实现直接 `kw in text`，大写 MCN 匹配不到词表里的小写 "mcn"。
    2. **dict 顺序即优先级**（F5）：命中即返回，所以**更具体的行业要排在更泛的之前**。
       「宠物医院」里既有「宠物」也有「医院」——宠物必须排在医疗前面，
       否则宠物医院会被判成医疗。
    """
    low = (text or "").lower()
    for industry, keywords in rules.industry_keywords().items():
        for kw in keywords:
            if kw.lower() in low:
                return industry
    return None


# ─── 数字解析 ─────────────────────────────────────────────────────────────

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_SMALL = {"十": 10, "百": 100, "千": 1000}
_CN_BIG = {"万": 10000, "亿": 100000000}


def _parse_cn_number(s: str):
    """解析中文数字串（含 万/亿 与口语缩写），返回 int 或 None。

    覆盖：
    - 基础：一/十二/二十/一百/一千
    - 大单位：一万/两万/二十万/一百二十万/一亿
    - 口语缩写：一万二→12000、一千五→1500、一万二千→12000
      （大单位后紧跟的裸数字按「单位/10」计：万二=2000、千二=200）

    旧实现只认单个字符（"一万二"→只取"一"=1.0），是「房租一万二」被抽成
    rent=1.0 的根因。
    """
    if not s or not re.fullmatch(r"[零一二两三四五六七八九十百千万亿]+", s):
        return None

    total = 0          # 最终结果（跨 万/亿 段累计）
    section = 0        # 当前 万/亿 段内累计
    current = 0        # 当前正在读取的数字
    last_unit = None   # 上一个遇到的小/大单位量纲（用于缩写尾数字）

    for ch in s:
        if ch in _CN_DIGITS:
            if ch == "零":
                # 显式零：打断缩写链（"一万零二"=10002，而非 12000）
                current = 0
                last_unit = None
            else:
                # 遇到新数字前，先把上一段数字落袋
                if current != 0:
                    section += current
                    current = 0
                current = _CN_DIGITS[ch]
                # 注意：不在此重置 last_unit——大单位后的裸数字（万二→2000）
                # 是缩写，需要记住上一个单位量纲。
        elif ch in _CN_SMALL:
            u = _CN_SMALL[ch]
            c = current if current != 0 else 1   # "十" 开头按 10 处理
            section += c * u
            current = 0
            last_unit = u
        elif ch in _CN_BIG:
            u = _CN_BIG[ch]
            section += current
            total += section * u
            section = 0
            current = 0
            last_unit = u   # 保留大单位，供缩写尾数字（万二→2000）

    # 收尾：末尾可能残留数字（可能是缩写尾，也可能是普通尾）
    if current != 0:
        if last_unit is not None:
            section += current * (last_unit // 10)  # 万二→2*(10000/10)=2000
        else:
            section += current
    total += section
    return total


def _cn_to_num(text: str) -> Optional[float]:
    """中文数字转数字（委托给完整解析器，兼容旧调用点）。"""
    n = _parse_cn_number(text)
    return float(n) if n is not None else None


def _parse_number(raw: str) -> Optional[float]:
    """解析带单位的数字: '1.5万' → 15000, '2千' → 2000, '两' → 2,
    '一万二' → 12000, '二十万' → 200000, '-5000' → -5000。

    优化（P3-1）：纯中文数字串（含 万/亿）优先走完整解析器，解决旧实现
    把「一万二」错抽成 1.0 的问题。

    修复（B2）：捕获数字前的负号（- 或 负 前缀），避免「月利润-5000」被抽成
    +5000（亏损变盈利）。
    """
    raw = raw.strip()
    if not raw:
        return None

    # 检测负号（在数字/中文数字前）
    negative = False
    if raw.startswith("-") or raw.startswith("负"):
        negative = True
        raw = raw[1:].strip()

    # 纯中文数字（含 万/亿 与缩写），如 "一万二" "两万" "一千五"
    if re.fullmatch(r"[零一二两三四五六七八九十百千万亿]+", raw):
        n = _parse_cn_number(raw)
        if n is not None:
            return -float(n) if negative else float(n)

    # 中文数字（兼容其它含中文的写法）
    cn = _cn_to_num(raw)
    if cn is not None:
        return -cn if negative else cn

    # 万 / 千：口语缩写「1万5」= 15000、「1千5」= 1500（尾数按「单位/10」计）
    # E4 修复：旧实现只取 `\d+` 第一段，把「1万5」算成 10000 —— 丢尾数是**静默算错**。
    for unit, scale in (("万", 10000), ("w", 10000), ("W", 10000),
                        ("千", 1000), ("k", 1000), ("K", 1000)):
        if unit in raw:
            m_abbr = re.match(rf"^(\d+(?:\.\d+)?)\s*{unit}\s*(\d)", raw)
            if m_abbr:
                val = float(m_abbr.group(1)) * scale + float(m_abbr.group(2)) * (scale // 10)
                return -val if negative else val
            num = re.search(r"\d+(?:\.\d+)?", raw)
            if num:
                val = float(num.group(0)) * scale
                return -val if negative else val
    # 普通数字
    num = re.search(r"\d+(?:\.\d+)?", raw)
    if num:
        val = float(num.group(0))
        return -val if negative else val
    return None


# ─── 字段定义 ─────────────────────────────────────────────────────────────
# position: "before" 数字在关键词后 / "after" 数字在关键词前 / "any"
# units: 必须有单位才匹配（None 表示无单位约束）

# ─── 字段抽取模式 ──────────────────────────────────────────────────────────
# 数据化：16 个字段的模式已迁到 src/router/rules/{zh,en}.yaml。
# 引擎（position 搜索 / 单位换算 / 量纲护栏）只有一份，语言差异只在数据文件。
# 此处保留 zh 快照绑定供向后兼容；实际抽取走 rules.field_patterns()。
_FIELD_PATTERNS = rules.field_patterns("zh")


# ─── 文本分段 ─────────────────────────────────────────────────────────────
# 按中文/英文逗号分号句号切分，每段独立提取

# 无标点连写时，在字段关键词前插入分隔，避免「投资30万租金8000」整段共享第一个「万」
# 数据化：整条正则迁到 rules/{zh,en}.yaml 的 boundary.pattern（含 lookahead，
# 拆成词表即变语义，故整条存储）。实际使用走 rules.boundary_re() 按 locale 取。
_BOUNDARY_RE = rules.boundary_re("zh")
_KW_WINDOW = 24  # 关键词邻域：禁止用整段 after_text 的第一个「万」


def _inject_field_boundaries(text: str) -> str:
    """在字段关键词前插入逗号，把无标点连写切成可独立抽取的段。"""
    if not text:
        return text

    def _repl(m: re.Match) -> str:
        if m.start() == 0:
            return m.group(0)
        prev = text[m.start() - 1]
        if prev in "，。；,;、 \t":
            return m.group(0)
        return "，" + m.group(0)

    return rules.boundary_re().sub(_repl, text)


def _split_segments(text: str) -> List[str]:
    """按分隔符切分文本（保留空格）"""
    return re.split(r"[,，;。；]+", text)


def _extract_from_segment(segment: str, field_def: dict) -> Optional[float]:
    """在单个段内提取一个字段

    position (默认):
    - "before": 关键词在数字前, 优先搜 after_text（找不到时回退 before）
    - "after": 数字在关键词前, 优先搜 before_text（找不到时回退 after）
    - "any": 都搜

    kw_position (按关键词覆盖):
    - 某关键词的位置定义
    """
    keywords = field_def["keywords"]
    default_position = field_def.get("position", "any")
    units = field_def.get("units")
    strict = field_def.get("strict_units", False)
    kw_position = field_def.get("kw_position", {})
    # D4：率字段的量纲护栏。units 是「期望单位」（%），reject_units 是「量纲不符单位」。
    # 「变动成本6元」的 6 带元 → 是**金额**不是率，必须拒收，否则会被当成 6%
    # （引擎按「>1 则 /100」归一），毛利率凭空变 94%。
    reject_units = field_def.get("reject_units")
    # 语境护栏：关键词后紧跟的是其他概念的语境词（不是本字段要的数字）。
    # 用于 daily_traffic 的「每天」兜底——「每天营业额3000」里「营业额」表明
    # 这是钱不是客流，即使 3000 不带「元」也应拒收。
    reject_context = field_def.get("reject_context")
    # F8：上限护栏。命中但超限时**不返回**，让它继续尝试下一侧/下一阶段。
    # 「2个员工每人6000」里，阶段1 会把「每**人**6000」的「人」当人数单位抓成
    # 6000 人；旧实现直接返回 6000，再被后处理「>200 且无 N人 写法」丢弃 →
    # 员工数整个丢失（而同一句用逗号分开就正常）。加上限后才会落到正确的「2个」。
    max_value = field_def.get("max_value")

    for kw in keywords:
        idx = segment.find(kw)
        if idx == -1:
            continue

        # 取该关键词的位置
        position = kw_position.get(kw, default_position)

        before_text = segment[:idx][-_KW_WINDOW:]
        after_text = segment[idx + len(kw):][:_KW_WINDOW]

        # 确定搜索「侧」的顺序（before/after），按 position 决定
        if not before_text and after_text:
            sides = [after_text]
        elif not after_text and before_text:
            sides = [before_text]
        else:
            sides = []
            if position in ("before", "any"):
                sides.append(after_text)
            if position in ("after", "any"):
                sides.append(before_text)

        # 阶段1（仅对带单位的字段）：优先匹配「带单位的数字」。
        # 这能避免把相邻的其他数字（如紧跟在「员工」后的薪资 6000）
        # 误当成「人数」——先找带「人/个」单位的数字，找不到再兜底。
        if units:
            for s in sides:
                if reject_context and _has_context_word(s, reject_context):
                    continue
                if reject_units and _has_number_with_unit(s, reject_units):
                    continue
                value = _find_number(s, units, unit_only=True)
                # F8：超限视为误抓，继续找下一个候选（见 max_value 注释）
                if value is not None and (max_value is None or value <= max_value):
                    return value

        # 阶段2：兜底（无单位/中文/纯数字），沿用原逻辑
        for s in sides:
            # D4：量纲不符 → 该数字带金额单位，不是本率字段的值，跳过而非误收。
            if reject_units and _has_number_with_unit(s, reject_units):
                continue
            # 语境不符 → 关键词后紧跟其他概念的语境词（如「营业额」），跳过。
            if reject_context and _has_context_word(s, reject_context):
                continue
            value = _find_number(s, units, strict=strict)
            if value is not None:
                # 「3个月」不是金额：数字与「月」之间可夹「个」
                if reject_units and "月" in reject_units:
                    vn = int(value) if float(value).is_integer() else None
                    if vn is not None and re.search(rf"{vn}\s*个?月", s):
                        continue
                return value

    return None


def _has_number_with_unit(text: str, units) -> bool:
    """text 中是否存在「数字 + 指定单位」的写法（D4 率字段量纲护栏用）。

    与 `_find_number` 的取值不同：这里只判**量纲是否出现**，不取值，
    因为「变动成本6元」要的是「认出它是元、从而拒收」，而不是把 6 读出来。
    """
    if not text:
        return False
    return re.search(r"\d+(?:\.\d+)?\s*(?:" + "|".join(units) + r")",
                     text) is not None


def _has_context_word(text: str, words) -> bool:
    """text 开头附近是否出现指定语境词（reject_context 护栏用）。

    只检查 text 前 10 个字符（关键词与数字之间的区域），避免跨段误判。
    例如「每天」后紧跟「营业额」→ 是日营收，不是客流；
    但「每天卖80杯，营业额不错」里「营业额」在逗号后，属于下一分句，不应触发。
    """
    if not text:
        return False
    # 只看关键词到第一个数字之间的区域（最多前 10 字）
    head = text[:10]
    for w in words:
        if w in head:
            return True
    return False

def _find_number(text: str, units, strict: bool = False,
                unit_only: bool = False) -> Optional[float]:
    """在 text 里找数字

    策略:
    1. 带单位的数字（数字+单位 或 单位+数字 都找）
    2. 数字 + 元/万/千
    3. 中文数字
    4. 纯数字

    strict=True（P4-4 防误抓，用于总额类字段）：仅允许步骤1/2 的阿拉伯数字，
    禁止 3/4 中文量词兜底——避免修辞句「每一项都给你了」误捞"一"→1.0。
    注意 strict 仍允许无单位阿拉伯数字（如「固定成本2500」→2500）。

    unit_only=True（供 _extract_from_segment 阶段1 优先匹配「带单位数字」用）：
    只在步骤1（带单位数字）命中时返回，否则直接 None，绝不进入 2/3/4 兜底。
    用于避免把紧跟在「员工」后的薪资数字(6000)误当成人数。

    text: 已是关键词左/右的子串。B2 修复：支持负号前缀（如 "-5000元"、"-10%"）。
    """
    if not text:
        return None

    # 负号前缀：允许「- / － / − / 负」，且**紧贴数字**（中间可含空格）。
    #
    # D1 修复：旧实现只在 text **以负号开头**时才认符号，于是「月租金-8000」被短关键词
    # 「月租」切成 after_text="金-8000" —— 开头是「金」不是「-」，符号就此丢失，
    # 静默把 -8000 变成 8000。**这等于抽取器抢在 param_guard 之前替用户"修正"了数据**：
    # guard 本来对 -8000 有正确的 critical 校验（低于物理下限 0、需确认、从 cleaned 剔除），
    # 但永远收不到这个值，防线被短路。
    # 改法：让每条数字正则自己捕获紧贴的负号，符号传播不再依赖子串开头位置。
    # (?<!\d)：负号前不得紧跟数字，否则「每天卖100-150碗」的区间分隔符会被当成负号
    #          → 抽成 -150 被 guard 判 critical 剔除，反而丢了正常客流（D1 修复的连带回归）。
    _SIGN = r"(?<!\d)(?:-|－|−|负)\s*"

    negative = False
    if text.startswith("-") or text.startswith("负"):
        negative = True
        text = text[1:].strip()

    # 0. 中文金额缩写「A万B / A千B」（E4 修复：「1万5」→15000，旧实现丢尾数得 10000）
    #    仅当请求的单位含 万/千 时启用；尾数后不得紧跟量词/时间词，
    #    否则「月租2万5人」的 5（人数）会被吞进金额。
    if units:
        units_list = units if isinstance(units, list) else [units]
        for u in ("万", "千"):
            if u not in units_list:
                continue
            m = re.search(
                rf"((?:{_SIGN})?\d+(?:\.\d+)?\s*{u})(\d)"
                r"(?!\s*[人个位名杯碗份件瓶只条张袋盒串盘年月天日])",
                text,
            )
            if m:
                val = _parse_number(m.group(1) + m.group(2))
                if val is not None:
                    return -val if negative else val

    # 1. 带单位的数字
    if units:
        units_list = units if isinstance(units, list) else [units]
        for u in units_list:
            # 数字 + 单位
            m = re.search(rf"((?:{_SIGN})?\d+(?:\.\d+)?\s*{u})", text)
            if m:
                val = _parse_number(m.group(1))
                if val is not None:
                    return -val if negative else val
            # 单位 + 数字（「一杯25」的「杯」前是「一」，不是客流单位）
            m = re.search(rf"((?<![一每]){u}\s*(?:{_SIGN})?\d+(?:\.\d+)?)", text)
            if m:
                val = _parse_number(m.group(1))
                if val is not None:
                    return -val if negative else val
        # unit_only：仅要带单位的数字，绝不进入兜底步骤
        if unit_only:
            return None

    # 2. 普通数字 + 元/万/千（元可选：覆盖「固定成本2500」无单位阿拉伯数字）
    m = re.search(rf"((?:{_SIGN})?\d+(?:\.\d+)?(?:\s*[万千]|\s*元?))", text)
    if m:
        val = _parse_number(m.group(1))
        if val is not None:
            return -val if negative else val

    # strict_units（P4-4）：到此仍未命中带单位/阿拉伯数字 → 禁止中文数字兜底，
    # 仅接受真实意图的阿拉伯数字，杜绝修辞句「每一项都给你了」误捞"一"→1.0
    if strict:
        return None

    # 3. 中文数字（含 万/千/百/亿，作为缩写兜底）
    cn_match = re.search(rf"((?:{_SIGN})?[零一二两三四五六七八九十百千万亿]+)\s*(?:个|位|名)?", text)
    if cn_match:
        num = _parse_number(cn_match.group(1))
        if num is not None:
            return -num if negative else num

    # 4. 纯数字
    m = re.search(rf"((?:{_SIGN})?\d+(?:\.\d+)?)", text)
    if m:
        val = _parse_number(m.group(1))
        if val is not None:
            return -val if negative else val

    return None


# ─── 主入口 ───────────────────────────────────────────────────────────────

_LABOR_VERB = r"(?:改为|改成|调成|调到|调为|换成|变为|变成|设为|定为|调成|调整?为?|改|调|换)?\s*"


def _extract_labor_pair(text: str) -> Tuple[Optional[float], Optional[float]]:
    """从人力连写短语同时抽出人数与人均薪资。

    支持：
    - 「2人8000 / 人工3人1万 / 员工 5 人 6000」
    - 「人工3500*2 / 人工3500×2 / 工资3500x2人」（薪 × 人）
    - 「人工2*3500 / 2人*3500」（人 × 薪）
    - 「人工改为2*3000 / 人工调成2人3000」（含「改为/调成」等动词）

    返回 (employee_count, avg_salary)，任一侧缺失则为 None。
    靠通用字段循环无法区分「2人」的 2（人数）和「8000」（薪资），
    故用专门的正则一次性捕获两者，避免薪资被错抽成人数。
    """
    # 1) 标准：N人 + 薪资（可无乘号）
    m = re.search(
        r"(\d+)\s*人\s*(?:[*×xX]\s*)?(?:月薪|工资|薪资|人均)?\s*"
        r"(\d+(?:\.\d+)?\s*[万千]?)",
        text,
    )
    if m:
        count = float(m.group(1))
        salary = _parse_number(m.group(2).strip())
        if salary is not None and _plausible_labor(count, salary):
            return (count, salary)

    # 2) 薪×人：人工3500*2 / 工资3500×2 / 月薪3500x2人 / 人工改为3500*2
    m = re.search(
        r"(?:人工|员工|雇|工资|月薪|薪资|人均)\s*" + _LABOR_VERB +
        r"(\d+(?:\.\d+)?\s*[万千]?)\s*[*×xX]\s*(\d+)\s*人?",
        text,
    )
    if m:
        salary = _parse_number(m.group(1).strip())
        count = float(m.group(2))
        if salary is not None and _plausible_labor(count, salary):
            return (count, salary)

    # 3) 人×薪（无「人」字）：人工2*3500 / 人工改为2*3500
    m = re.search(
        r"(?:人工|员工|雇)\s*" + _LABOR_VERB +
        r"(\d+)\s*[*×xX]\s*(\d+(?:\.\d+)?\s*[万千]?)",
        text,
    )
    if m:
        count = float(m.group(1))
        salary = _parse_number(m.group(2).strip())
        if salary is not None and _plausible_labor(count, salary):
            return (count, salary)

    return (None, None)


def _plausible_labor(count: float, salary: float) -> bool:
    """人数/人均薪资合理性门禁，挡住「3500人×默认薪」类误抓。"""
    if count is None or salary is None:
        return False
    # 人数：早期创业团队通常 < 200；薪资：月薪区间
    if count <= 0 or count > 200:
        return False
    if salary < 500 or salary > 500_000:
        return False
    # 若「人数」看起来像薪资、而「薪资」像人数 → 不合理（如 3500 人 × 2 元）
    if count >= 500 and salary < 100:
        return False
    return True


def _extract_cost_ratio(text: str, params: dict) -> None:
    """从「食材成本占营业额45% / 成本率40% / 变动成本(率)改为60%」抽取变动成本率(0~1)。

    这种「占比」说法表达的是比例，与「每份成本(元)」是两回事；单独处理
    并转成 0~1 比例，避免被 unit_variable_cost 误当成「每份 45 元」。

    B2 修复：正则允许前导负号（如「变动成本率-20%」）。
    """
    if params.get("variable_cost_ratio") is not None:
        return
    pats = [
        # E1 修复（2026-09-12）：对象词「营业额/营收/收入」改为**可省略**——
        # 「食材大概35% / 食材成本35% / 原料成本大概 40%」——口语不等于术语
        r"(?<!固定)(?<!总)(?:食材|原料|材料)(?:成本)?\s*"
        r"(?:占|为|是|到)?\s*(?:大概|大约|约)?\s*(-?\d+(?:\.\d+)?)\s*%",
        # 「食材成本占40%」是口语里最自然的说法，旧实现要求显式对象词而漏抽。
        # 负向断言排除「固定成本占…」「总成本占…」：那是**固定成本占比**，
        # 不是变动成本率（不能把「月固定成本占40%」算成 vcr=0.4）。
        r"(?<!固定)(?<!总)(?:食材|原料|材料|变动|可变|运营)?成本占"
        r"(?:(?:营业额|营收|收入)\s*)?(-?\d+(?:\.\d+)?)\s*(?!成)%?",
        r"成本率\s*(?:为|是|到|改成|改为)?\s*(-?\d+(?:\.\d+)?)\s*(?!成)%?",
        # F1：变动成本/可变成本 + 明确比例语义（含「改为/改成/为/是/到」等动词，
        # 后面直接跟 0~100 的百分数）→ 归一化为 ratio。避免误抓「每份成本 45 元」。
        r"(?:变动成本率|可变成本率|变动成本|可变成本)\s*(?:为|是|到|改成|改为|变成)?\s*(-?\d{1,3}(?:\.\d+)?)\s*%",
    ]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            val = float(m.group(1))
            if 0 < abs(val) < 100:           # 百分数（0<|v|<100），归一化；极小/极大视为噪声
                # D5 修复：未写 % 且 |v|<=1 的裸小数**本身已是比例**，不再 /100。
                # 旧实现一律 /100，「变动成本率0.6」被算成 0.006（0.6%），
                # 与通用字段归一出的 60% 自相矛盾——同一句话两个答案，
                # 引擎取 variable_cost_ratio → 毛利率凭空变 99.4%。
                has_pct = "%" in (m.group(0) or "")
                params["variable_cost_ratio"] = (
                    val if (not has_pct and abs(val) <= 1) else val / 100
                )  # 保留符号
            return


# ── E2：中文分数「X成」────────────────────────────────────────────────────
# 计量单位的量词（供 E3/E5 的独立正则使用，不进 _FIELD_PATTERNS 的 keyword 列表）
_QUANTIFIERS = "碗|杯|份|位|件|瓶|个|只|条|张|袋|盒|串|盘"
# 客流单位（D9）：比售价量词多几个只用于计数的单位（桌/单/台/人次）。
# 不并入 _QUANTIFIERS —— 「一桌」进售价量词会让「一桌菜300元」被当客单价。
_TRAFFIC_UNITS = _QUANTIFIERS + "|桌|单|台|人次"

_CN_FRACTION_DIGIT = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}


def _cn_fraction_ratio(raw: str) -> Optional[float]:
    """「X成」的 X → 比例 0~1（十成 = 1.0；>10 视为噪声返回 None）。"""
    if raw in _CN_FRACTION_DIGIT:
        return _CN_FRACTION_DIGIT[raw] / 10.0
    try:
        v = float(raw)
    except ValueError:
        return None
    return v / 10.0 if 0 < v <= 10 else None


def _extract_cn_fraction(text: str, params: dict) -> None:
    """中文分数「X成」→ 0.1×X（E2 修复）。

    旧行为是**静默算错**（比漏抽更危险）：
    - 「食材成本占4成」→ 通用字段兜底把「4」当阿拉伯数字 → guard 归一 0.04（差 10 倍）
    - 「毛利率六成」→ 0.06

    语义绑定（避免误伤「完成 / 成员 / 成为」里的「成」）：
    - 变动成本率侧：必须紧邻「成本」（可前置 食材/原料/材料/变动/可变/运营），
      且「成」后不得紧跟「本」。
    - 毛利率侧：必须紧邻「毛利 / 毛利率 / 毛利润率」（前置或后置）。
    """
    num = r"(\d+(?:\.\d+)?|[一二两三四五六七八九十])"

    if params.get("variable_cost_ratio") is None:
        m = re.search(
            r"(?<!固定)(?<!总)(?:食材|原料|材料|变动|可变|运营)?成本"
            r"(?:占|为|是|到)?\s*(?:(?:营业额|营收|收入)\s*)?" + num + r"\s*成(?!本)",
            text,
        )
        if m:
            ratio = _cn_fraction_ratio(m.group(1))
            if ratio is not None:
                params["variable_cost_ratio"] = ratio

    # 毛利率侧：通用字段会把「六成」抽成 6 → guard 归一 0.06，故此处必须**覆盖**。
    m = re.search(
        r"(?:毛利率|毛利润率|毛利)\s*(?:为|是|有|到)?\s*" + num + r"\s*成", text)
    if not m:
        m = re.search(
            num + r"\s*成\s*(?:的)?\s*(?:毛利率|毛利润率|毛利)", text)
    if m:
        ratio = _cn_fraction_ratio(m.group(1))
        if ratio is not None:
            params["gross_margin"] = ratio


def _extract_unit_cost_alias(text: str, params: dict) -> None:
    """单位变动成本同义词（E5）：`食材成本每份8元` / `每碗成本5元` / `单位成本7元`。

    为什么走独立正则而不进 `_FIELD_PATTERNS` 的 keyword 列表？
    因为通用 keyword 的兜底会把「食材成本占营业额45%」里的 45 抽成
    「每份 45 元」——那是**占比**，不是单位成本。这里强制数字后紧跟「元/块」，
    从结构上排除百分比写法。
    """
    if params.get("unit_variable_cost") is not None:
        return
    m = re.search(
        r"(?:食材|原料|材料|变动|可变|单位|单份|单件|每碗|每杯|每份|每件|每瓶|每个|单个"
        # D3：口语里「一碗成本6元」比「每碗成本6元」常见，量词前缀补上「一X」；
        # 允许量词与「成本」之间夹 ≤8 字商品名（一碗牛肉面成本6元），
        # 但中间段不含分隔符，因此不会跨句误抓。
        r"|一碗|一杯|一份|一件|一瓶|一个)"
        r"[^\s，。；、]{0,8}?成本\s*(?:为|是|要|约|大约|大概)?\s*"
        r"(?:每碗|每杯|每份|每件|每瓶|每个|单份|单件)?\s*"
        r"(\d+(?:\.\d+)?)\s*[元块]",
        text,
    )
    if m:
        val = _parse_number(m.group(1))
        if val and val > 0:
            params["unit_variable_cost"] = val


# 日营收/日营业额语境词：这些词出现时，数字是钱不是客流。
_DAILY_REVENUE_WORDS = "营业额|营收|流水|收入|销售额|业绩"

def _extract_daily_revenue(text: str, params: dict) -> None:
    """日营收提取（F3 遗留边界修复）：「每天营业额3000」/「日均营收3000元」→ 月营收.

    问题：不带「元」的「每天营业额3000」旧版会被 daily_traffic 的「每天」兜底
    抽成客流 3000（撑出假营收=3000×DAYS_PER_MONTH×客单价）；带「元」已靠 reject_units 挡住，
    但挡住后没有正向提取——用户说了日营收，系统完全没记住。

    修复：独立正则（与 _extract_traffic_unit 对称），匹配「时间词 + 营收词 + 数字」，
    转月营收 = 日值 × DAYS_PER_MONTH（field_model 唯一出处）。

    以 `_daily_revenue_src` 标注来源，供 param_sources 使用。
    """
    if params.get("monthly_revenue") is not None:
        return  # 已有月营收，不覆盖
    _num = r"(\d+(?:\.\d+)?)"
    _money_unit = r"(?:\s*[元块])?"   # 单位可选（正是不带「元」的漏抽场景）
    _scale = r"(?:\s*(万|千))?"       # 可选大单位
    _time = r"(?:每天|每日|日均|一天|一天大概|每天大概|每天能|日均大概|日)"
    for pat in (
        rf"{_time}\s*(?:{_DAILY_REVENUE_WORDS})\s*{_num}{_money_unit}{_scale}",
    ):
        m = re.search(pat, text)
        if not m:
            continue
        # 直接拼数字+单位串让 _parse_number 解析（它能处理「3万」「3千」「3000」）
        num_str = m.group(1) + (m.group(2) or "")
        val = _parse_number(num_str)
        if val and val > 0:
            # 延迟导入避免循环（param_guard 同模式）
            from field_model import DAYS_PER_MONTH
            params["monthly_revenue"] = round(val * DAYS_PER_MONTH, 2)
            params["_daily_revenue_src"] = f"日{val:g}元 × {DAYS_PER_MONTH}天"
        return

def _extract_traffic_unit(text: str, params: dict) -> None:
    """客流与客流单位（D8/D9）：「每天卖100碗」→ 100 碗。

    D8 单位：行业模板只到「餐饮」粒度，而餐饮模板默认「杯」——面馆于是看到
    「盈亏平衡客流 22 杯/天」，用户说的是碗、系统回的是杯。用户口中的量词
    是最权威的口径，优先于行业默认。

    D9 补漏：「日均80桌」「一天卖80杯」旧表抽不到——
    `daily_traffic` 的 keyword 有「每天卖」却没有「一天卖」，
    「日均」也只认「日均客流/日均订单…」而不认裸「日均 + 数字 + 量词」。
    （之所以不把裸「日均」加进 keyword：那会让「日均营业额3000」被当成客流 3000。
      这里用「数字后必须紧跟量词」的结构约束规避。）

    以 `_` 前缀存单位（同 `_revenue_series` / `_user_gross_margin` 惯例），
    不参与业务计算，只供呈现层取用。
    """
    _num = r"(\d+(?:\.\d+)?)"          # 区间取后段（「100-150碗」按 150 计）
    _tail = r"(?:\s*[-~－—]\s*\d+(?:\.\d+)?)?"
    for pat in (
        rf"(?:每天|每日|日均|一天|一天大概|每天大概|每天能|日均大概)\s*"
        rf"(?:卖|售|销|能卖|客流|大概|约|大约|平均)?\s*"
        rf"{_num}{_tail}\s*({_TRAFFIC_UNITS})",
        rf"{_num}{_tail}\s*({_TRAFFIC_UNITS})\s*(?:/|每)\s*天",
    ):
        m = re.search(pat, text)
        if not m:
            continue
        if params.get("daily_traffic") is None:
            val = _parse_number(m.group(1))
            if val and val > 0:
                params["daily_traffic"] = val
        params["_traffic_unit"] = m.group(2)
        return


def _extract_unit_price_alias(text: str, params: dict) -> None:
    """量词别名客单价（E3）：`每碗18元` / `每杯15元` / `每位30元` / `每个20块`。

    同理走独立正则（`_FIELD_PATTERNS` 是先到先得，`每份` 会与 `每份成本` 抢词）。
    两重护栏：
    1. 数字后必须紧跟「元/块」→「每份成本7元」天然不匹配；
    2. 量词前 4 字内出现「成本」则跳过 →「食材成本每份8元」不会被当售价。
    另：「每天卖80杯」的「每 + 天」不在量词表内，不会误伤客流。
    """
    if params.get("price_per_unit") is not None:
        return
    # D3 修复：旧正则只认「量词紧邻数字」（每碗18元），而口语里量词与数字之间
    # 几乎总夹着商品名和动词（一碗牛肉面卖18元 / 一碗卖18元），或量词后置
    # （牛肉面卖18元一碗）。实测这三种最高频说法全部漏抽 → 用户报了价，
    # 系统记不住，还回头追问「客单价」，属于静默丢弃用户输入。
    #
    # 中间段 `_gap` 的三重约束（缺一不可，否则会误抓）：
    #   ① 不含分隔符（，。；、空格）→ 不跨句、不跨并列成分；
    #   ② 不含 人/员/月/薪 等角色与时间字 →「每个员工6000元」不会被当客单价；
    #   ③ 上限 8 字 → 不吞掉整句。
    _gap = r"[^\s，。；、人员月薪年薪周资]{0,8}?"
    _money = r"(\d+(?:\.\d+)?)\s*[元块]"
    _pats = [
        # 形态 A：量词(每|一) + [商品名/动词] + 金额 —— 一碗牛肉面卖18元 / 一杯拿铁28元
        rf"(?:每|一)\s*(?:{_QUANTIFIERS})\s*{_gap}\s*{_money}",
        # 形态 B：金额 + (一|每) + 量词 —— 牛肉面卖18元一碗 / 定价18元一杯
        # 尾部负向断言（D3 回归护栏）：量词后若接 月/天/年/周/时/人/员，
        # 那是**时间单位或人数**，不是销售单位——
        # 「月租金8000元一个月」的「一个月」不能当成客单价 8000。
        rf"{_money}(?:钱)?\s*(?:一|每)\s*(?:{_QUANTIFIERS})(?!\s*[月天日年周时人员])",
    ]
    for _pat in _pats:
        for m in re.finditer(_pat, text):
            if "成本" in text[max(0, m.start() - 4):m.start()]:
                continue
            # 护栏2（D3 新增）：形态 A 允许量词与数字间夹任意字符，
            # E3 原有的「前置 4 字」护栏会被绕过（一碗牛肉面成本6元 里
            # 「成本」在量词之后），故补查命中串自身。
            if "成本" in m.group(0):
                continue
            val = _parse_number(m.group(1))
            if val and val > 0:
                params["price_per_unit"] = val
                return


# 常见的「角色+薪资」措辞，用于从「厨师6000、服务员4500」取人均薪资
_ROLE_WORDS = [
    "厨师", "服务员", "店长", "收银", "帮厨", "学徒", "经理",
    "主管", "技师", "普工", "保洁", "司机",
]


def _extract_role_salary(text: str, params: dict) -> None:
    """从「厨师6000、服务员4500」这类「角色+数字」短语取薪资均值作 avg_salary。

    仅当通用字段未抽到明确薪资时启用；数字须在合理月薪区间(500~200000)，
    避免误抓人数/投资额等无关数字。
    """
    if params.get("avg_salary") is not None:
        return
    found = []
    for role in _ROLE_WORDS:
        for m in re.finditer(rf"{role}\s*(\d+(?:\.\d+)?)", text):
            num = float(m.group(1))
            if 500 <= num <= 200000:
                found.append(num)
    if found:
        params["avg_salary"] = sum(found) / len(found)


_LABOR_TOTAL_MARK = r"(?:一共|合计|加起来|总共|共计)"
_HEADCOUNT_RE = re.compile(
    r"(?<![每万])([两一二三四五六七八九十]|[0-9]{1,3})\s*个?人(?!均|次)"
)


def _extract_headcount(text: str, params: dict) -> None:
    """「两个人 / 2个人」→ employee_count。不把「每人 / 万人」当人数。"""
    if params.get("employee_count") is not None:
        return
    m = _HEADCOUNT_RE.search(text or "")
    if not m:
        return
    raw = m.group(1)
    n = float(raw) if raw.isdigit() else _parse_cn_number(raw)
    if n is not None and 0 <= n <= 200:
        params["employee_count"] = float(n)


def _extract_labor_total(text: str, params: dict) -> None:
    """「两个人工资一共1万2」是总额，人均 = 总额 / 人数。

    avg_salary 仍是人均。没有人数时宁缺勿填，避免把总额当人均。
    """
    if not text or not re.search(_LABOR_TOTAL_MARK, text):
        return
    if params.get("employee_count") is None:
        _extract_headcount(text, params)

    m = re.search(
        r"(?:工资|薪资|月薪|人工).{0,16}" + _LABOR_TOTAL_MARK + r"\s*"
        r"([0-9一二两三四五六七八九十]+(?:\.\d+)?\s*[万千]?[0-9一二两三四五六七八九十]?)",
        text,
    )
    if not m:
        m = re.search(
            r"([两一二三四五六七八九十\d]+)\s*个?人.{0,16}" + _LABOR_TOTAL_MARK + r"\s*"
            r"([0-9一二两三四五六七八九十]+(?:\.\d+)?\s*[万千]?[0-9一二两三四五六七八九十]?)",
            text,
        )
        total_raw = m.group(2) if m else None
    else:
        total_raw = m.group(1)
    if not total_raw:
        return
    total = _parse_number(total_raw.strip())
    if total is None or total < 500:
        return
    count = params.get("employee_count")
    if isinstance(count, (int, float)) and count > 0:
        params["avg_salary"] = round(float(total) / float(count), 2)
    else:
        params.pop("avg_salary", None)


def _extract_annual_rent(text: str, params: dict) -> None:
    """年租金 → 月租 = 年额/12。不把年额当月租。"""
    if not text:
        return
    pats = (
        r"(?:年租金|年租)\s*"
        r"((?:-?\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:\s*[万千])?)",
        r"(?:房租|租金)[^，。；0-9一二两三四五六七八九十]{0,6}一年\s*"
        r"((?:-?\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:\s*[万千])?)",
        r"一年(?:的)?(?:房租|租金)\s*"
        r"((?:-?\d+(?:\.\d+)?|[一二两三四五六七八九十]+)(?:\s*[万千])?)",
    )
    annual = None
    for pat in pats:
        m = re.search(pat, text)
        if not m:
            continue
        annual = _parse_number(m.group(1).strip())
        if annual:
            break
    if not annual or annual <= 0:
        return
    params["monthly_rent"] = round(annual / 12.0, 2)
    params["_rent_from_annual"] = True


def extract_params(text: str) -> Dict:
    """
    从自然语言提取项目参数。
    返回标准字段 dict（缺失字段不出现）。
    """
    params: Dict = {}
    text = text.strip()
    # 无标点连写先切段，避免「投资30万租金8000」共享第一个「万」
    text = _inject_field_boundaries(text)

    # 行业
    industry = _detect_industry(text)
    if industry:
        params["industry"] = industry

    # 分段处理数值字段
    segments = _split_segments(text)
    for field_def in rules.field_patterns():
        for seg in segments:
            if not seg.strip():
                continue
            val = _extract_from_segment(seg, field_def)
            if val is not None:
                params[field_def["field"]] = val
                break  # 找到第一个就够了

    # 人力连写短语（"人工2人8000" / "人工3500*2"）在通用抽取之后再跑，
    # 覆盖「人工」关键词把薪资误抓成人数的结果（专用正则优先级更高）。
    lab_count, lab_salary = _extract_labor_pair(text)
    if lab_count is not None:
        params["employee_count"] = lab_count
    if lab_salary is not None:
        params["avg_salary"] = lab_salary

    # ── 补充抽取：变动成本率（「食材成本占营业额45%」「成本率40%」）──
    _extract_cost_ratio(text, params)

    # ── 补充抽取：中文分数「X成」（「食材成本占4成」「毛利率六成」）──
    #    必须在通用字段之后跑：「六成」会被通用兜底抽成 6，此处覆盖修正。
    _extract_cn_fraction(text, params)

    # ── 补充抽取：单位成本同义词 / 量词别名（走独立正则，不进 keyword 列表）──
    #    顺序：先成本后售价——「食材成本每份8元」两个正则都会看到「每份8元」，
    #    但 E5 认成本、E3 靠「成本」前置词护栏跳过，互不抢词。
    _extract_unit_cost_alias(text, params)
    _extract_unit_price_alias(text, params)

    # ── 补充抽取：日营收（F3 边界「每天营业额3000」→ 月营收）──
    #    必须在 _extract_traffic_unit 之前：日营收词明确时，数字是钱不是客流。
    _extract_daily_revenue(text, params)

    # ── 补充抽取：客流单位（D8「每天卖100碗」→「碗」）──
    _extract_traffic_unit(text, params)

    # ── 补充后去噪：通用字段 variable_cost_rate 可能误抓相邻客流数字
    #    （如「日售50杯变动成本率55%」→ 50），而精确的 variable_cost_ratio
    #    已由 _extract_cost_ratio 给出 0.55 → 丢弃这份通用噪声，避免污染引擎。 ──
    if params.get("variable_cost_rate") is not None and params.get("variable_cost_ratio") is not None:
        # 精确 ratio（0~1）优先；通用 rate 无论是否与 ratio 同量纲都丢掉，避免双字段
        params.pop("variable_cost_rate", None)

    # ── 补充抽取：角色薪资（「厨师6000、服务员4500」→ 人均 5250）──
    _extract_role_salary(text, params)

    # ── 「两个人工资一共1万2」：总额÷人数写人均，覆盖通用字段把总额当人均 ──
    _extract_headcount(text, params)
    _extract_labor_total(text, params)

    # ── 年租金/房租一年 → 月租 = 年额/12（覆盖把年额当月租）──
    _extract_annual_rent(text, params)

    # 人数合理性：无「N人」证据却抽出超大人数（如 3500）时丢弃，避免污染 C1
    ec = params.get("employee_count")
    if isinstance(ec, (int, float)) and ec > 200:
        # 若原文确实写了「3500人/位/名」则保留；否则视为误抓
        if not re.search(rf"{int(ec)}\s*[人位名个]", text):
            params.pop("employee_count", None)

    # ── 后处理：固定成本组件去重 ──
    # 「水电杂费」这类复合词会被 utilities（匹配「水电」）与 other_fixed
    # （匹配「杂费」）各抓取一次，导致固定成本被重复计入。仅当原文含「水电杂费」
    # 且两字段取值相等时，丢弃 other_fixed（保留 utilities），避免重复记账。
    # 若用户分别给出「水电2000、杂费1000」（值不等），则不触发去重，各自保留。
    if "水电杂费" in text and params.get("utilities") is not None:
        if params.get("other_fixed") == params.get("utilities"):
            params.pop("other_fixed", None)

    # ── 守门层：归一化 + 即时校验（把「6000%」拦在抽取出口）──
    from param_guard import guard_extracted
    cleaned, _guard = guard_extracted(params, industry=params.get("industry"))
    # 把校验信息挂到返回值（web_server 可读取），不改变原有字段结构。
    # 注意：guard 摘要不得含 cleaned（否则 cleaned["_guard"]["cleaned"]=cleaned 循环引用）。
    if _guard.get("issues") or _guard.get("needs_confirmation") or _guard.get("contradictions"):
        cleaned["_guard"] = {
            "issues": _guard.get("issues", []),
            "contradictions": _guard.get("contradictions", []),
            "needs_confirmation": _guard.get("needs_confirmation", []),
            "has_critical": _guard.get("has_critical", False),
        }
    return cleaned


def is_valid_project_input(text: str) -> bool:
    """判断文本是否包含足够的项目信息（至少有行业或一个数值）"""
    params = extract_params(text)
    return bool(params.get("industry")) or len(params) >= 1
