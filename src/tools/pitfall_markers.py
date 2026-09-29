"""陷阱检测用的**文本匹配标记**（输入层数据，不是展示文案）。

为什么单独放：这些串是用来在**用户原文**里做 `in` 匹配的，属于输入层，
与展示文案无关；放这里可以让 i18n 护栏不必为一堆关键词开白名单。

为什么按 locale 分两套、而不是只留中文：
只留中文 = 英文版用户写 "the whole world" / "no competitors" 时，
这两个检查**永远不触发**——不是报错，是**静默少给结论**（"缺失不冒充"的同类隐患）。
所以英文必须有自己的一套词表。

未做的事（不是忘了）：词表只覆盖中英两种，且仍是简单子串匹配；
更复杂的语义判定（如否定句"并非没有竞争"）未处理。
将来若要接入 LLM 做语义判定，应替换本模块而非在调用点加分支。
"""

from i18n import get_locale

_MARKERS = {
    "zh": {
        # TAM 描述「过大」的夸饰词
        "tam_huge": ["万亿", "千亿", "所有人", "全部", "任何人", "全民", "全球"],
        # 「声称无竞争对手」的信号词
        "no_competitor": ["没有竞争", "没有对手", "无竞争", "蓝海", "空白市场", "独一无二"],
        # LLM 输出里的「风险句」起始词（advisor_formatter 用它从解读文本抽风险段）
        "risk": ["风险", "注意", "警告", "警惕", "小心", "谨防"],
        # 引擎来源标注里的「冲突」信号（fixed_cost_sum_conflict 的文案、staff_out_of_range 的 [矛盾] 标记）
        "conflict": ["矛盾", "冲突"],
    },
    "en": {
        "tam_huge": [
            "everyone", "everybody", "anyone", "all of", "the whole world",
            "whole world", "global", "billions", "trillions", "entire population",
        ],
        "no_competitor": [
            "no competition", "no competitors", "without competition",
            "no rival", "no rivals", "blue ocean", "untapped market",
            "no one else", "nobody else", "unique in the market",
        ],
        "risk": [
            "risk", "warning", "caution", "beware", "danger",
            "watch out", "be careful", "alert", "heads up",
        ],
        "conflict": ["conflict", "contradict", "conflicts with", "mismatch"],
    },
}


def tam_huge_markers() -> list:
    return list(_MARKERS.get(get_locale(), _MARKERS["zh"])["tam_huge"])


def no_competitor_markers() -> list:
    return list(_MARKERS.get(get_locale(), _MARKERS["zh"])["no_competitor"])


def risk_markers() -> list:
    """LLM 输出风险句起始词（随 locale；英文部署匹配英文 LLM 输出的 risk/warning…）。"""
    return list(_MARKERS.get(get_locale(), _MARKERS["zh"])["risk"])


def conflict_markers() -> list:
    """引擎来源标注里的「冲突」信号词（随 locale）。

    用于 _emit_anomaly_report 检测参数矛盾：fixed_cost_sum_conflict 的展示文案把
    「与显式总数矛盾/conflicts with explicit total」写进字段来源串（其 _codes 仍是
    user/derived），所以不能只看状态码，还得匹配文案里的冲突信号。
    """
    return list(_MARKERS.get(get_locale(), _MARKERS["zh"])["conflict"])
