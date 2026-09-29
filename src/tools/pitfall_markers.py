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
    },
}


def tam_huge_markers() -> list:
    return list(_MARKERS.get(get_locale(), _MARKERS["zh"])["tam_huge"])


def no_competitor_markers() -> list:
    return list(_MARKERS.get(get_locale(), _MARKERS["zh"])["no_competitor"])
