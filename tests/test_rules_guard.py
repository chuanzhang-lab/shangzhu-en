"""抽取规则包护栏（环境无关，不要求服务启动 / 不依赖网络）。

要防的三类事故：
1. **中英规则漂移**：两套规则包的字段/意图集合不一致 → 英文界面能抽 A 抽不到 B，
   或反过来。属「静默少抽一个字段」，测试不守就会一直漂。
2. **抽取结果不等价**：中英同义句必须抽出**完全相同**的数值。
   这是「英文输入要匹配引擎计算」的核心断言——数值不一致 = 算出来的账不一样。
3. **英文单位正则地雷**：`$` 是正则行尾锚点、`80/month` 含字母 m。
   这两类都曾静默算错（×1e6 / 只取到末尾 000），必须有回归测试钉死。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import i18n  # noqa: E402
from router import rules  # noqa: E402
from router.intent import detect_intent  # noqa: E402
from router.param_extractor import extract_params  # noqa: E402


def _locale(locale):
    """切 locale 的上下文：进入时设置，退出时复位（避免污染其它测试）。"""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        prev = i18n.get_locale()
        i18n.set_locale(locale)
        try:
            yield
        finally:
            i18n.set_locale(prev)

    return _cm()


def _canon(params: dict) -> dict:
    """把抽取结果规范成可比形式。

    两处**已知且合理**的中英差异，规范化掉再比：
    - `variable_cost_rate`(0~100) 与 `variable_cost_ratio`(0~1)：引擎侧
      `param_guard` 本就有 `normalize: "percent_or_ratio"`，两种写法都合法；
      中文走 `_extract_cost_ratio`（直接给 ratio），英文走字段模式（给 rate）。
      统一折算成 ratio 后再比。
    - `_traffic_unit`（客流单位，如「杯」）：由中文专用过程式抽取器
      `_extract_traffic_unit` 产出，英文侧尚未实现（P2-B2 待办），
      故本测试**不比较**该项——不是"它不重要"，而是"英文还没做"，
      待 P2-B2 补上后应把这项从排除列表移除。
    """
    out = {k: v for k, v in params.items() if k not in ("_guard", "_traffic_unit")}
    if "variable_cost_rate" in out and "variable_cost_ratio" not in out:
        out["variable_cost_ratio"] = round(out.pop("variable_cost_rate") / 100.0, 6)
    return out


def _extract(locale: str, text: str) -> dict:
    with _locale(locale):
        return _canon(extract_params(text))


# ── 1. 规则包对等 ──────────────────────────────────────────────────────────

def test_rule_pack_field_and_intent_parity_between_zh_and_en():
    """两套规则包的字段集合与意图集合必须完全一致。"""
    zh_fields = [f["field"] for f in rules.field_patterns("zh")]
    en_fields = [f["field"] for f in rules.field_patterns("en")]
    assert zh_fields, "zh 规则包未加载到字段"
    assert en_fields, "en 规则包未加载到字段"
    assert sorted(zh_fields) == sorted(en_fields), (
        f"字段集合不一致：zh 独有 {sorted(set(zh_fields) - set(en_fields))}；"
        f"en 独有 {sorted(set(en_fields) - set(zh_fields))}"
    )

    zh_intents = [r[0] for r in rules.intent_rules("zh")]
    en_intents = [r[0] for r in rules.intent_rules("en")]
    assert sorted(zh_intents) == sorted(en_intents), (
        f"意图集合不一致：zh 独有 {sorted(set(zh_intents) - set(en_intents))}；"
        f"en 独有 {sorted(set(en_intents) - set(zh_intents))}"
    )


def test_industry_values_are_chinese_data_keys_in_both_packs():
    """行业的**值**必须是中文数据键，不能翻译成英文。

    它是 config/industry_templates.yaml 的键——翻译成 "Food & Beverage"
    会让引擎查不到模板、静默拿不到行业默认值（与「数据值不翻译」同一原则）。
    """
    zh_keys = set(rules.industry_keywords("zh").keys())
    en_keys = set(rules.industry_keywords("en").keys())
    assert zh_keys, "zh 行业词表为空"
    assert en_keys == zh_keys, (
        f"行业键集合不一致（英文包的键被翻译了？）："
        f"en 独有 {sorted(en_keys - zh_keys)}；zh 独有 {sorted(zh_keys - en_keys)}"
    )


# ── 2. 中英同义句对：抽取结果必须等价 ──────────────────────────────────────

# (中文句, 英文句)。语义相同 → 数值必须完全相同。
SENTENCE_PAIRS = [
    ("月租8000", "monthly rent 8000"),
    ("总投资30万", "total investment 300000"),
    ("员工2人", "2 employees"),
    ("人均月薪5000", "average salary 5000"),
    ("日均客流100", "daily traffic 100"),
    ("客单价25", "average ticket 25"),
    ("月营收6万", "monthly revenue 60000"),
    ("月利润2万", "monthly profit 20000"),
    ("毛利率60%", "gross margin 60%"),
    ("变动成本率35%", "variable cost rate 35%"),
    ("水电费每月2000", "utilities 2000 per month"),
    ("包装费每月3000", "packaging 3000 per month"),
    ("提成每月5000", "commission 5000 per month"),
    ("每天卖80杯", "80 orders per day"),
]


def test_zh_en_sentence_pairs_extract_identical_values():
    """中英同义句必须抽出完全相同的参数与数值。

    这条是「英文输入匹配引擎计算」的核心 oracle：数值不一致，
    喂进 financial_calculator 后算出的账就不一样。
    """
    for zh, en in SENTENCE_PAIRS:
        got_zh = _extract("zh", zh)
        got_en = _extract("en", en)
        assert got_zh == got_en, (
            f"中英抽取不等价：\n  中文「{zh}」→ {got_zh}\n  英文「{en}」→ {got_en}"
        )
        # 两侧都不能是空的（否则这条断言形同虚设）
        assert got_zh, f"中文句「{zh}」没抽到任何参数——语料失效，断言无意义"


# ── 3. 英文数字能力的回归（中文侧从不触发的地雷）────────────────────────────

def test_english_thousand_separator_is_not_a_clause_separator():
    """「$300,000」必须是 300000，不是 300 / 0。

    逗号曾同时被当作句子分隔符（切成 "$300"+"000"），且数字正则不吃逗号分组。
    """
    got = _extract("en", "Total investment $300,000")
    assert got.get("total_investment") == 300000.0, got
    got2 = _extract("en", "average salary $5,000")
    assert got2.get("avg_salary") == 5000.0, got2


def test_english_m_unit_is_million_but_month_is_not():
    """「1.2m」= 120 万；「80/month」= 80，**不能**被当成 80×1e6。

    倍率匹配必须是锚定的（数字紧邻单位）："80/month" 里的 m 前面是 "/"，
    锚定匹配会打断；子串匹配则会静默 ×1e6。
    """
    assert _extract("en", "Total investment 1.2m").get("total_investment") == 1200000.0
    assert _extract("en", "Rent 80/month").get("monthly_rent") == 80.0


def test_english_keyword_match_is_case_insensitive():
    """英文大小写不敏感：「Rent」「MONTHLY RENT」都要能命中。"""
    assert _extract("en", "Rent 8000").get("monthly_rent") == 8000.0
    assert _extract("en", "MONTHLY RENT 8000").get("monthly_rent") == 8000.0


# ── 4. 意图路由的中英对应 ──────────────────────────────────────────────────

INTENT_PAIRS = [
    ("帮我生成PDF", "export a pdf report", "report_pdf"),
    ("怎么才能扭亏为盈", "how to turn profitable", "suggest"),
    ("行业平均毛利率是多少", "industry average margin", "benchmark"),
]


def test_zh_en_intent_pairs_route_to_same_intent():
    """中英同义提问必须路由到同一意图（意图名是数据键，不翻译）。"""
    for zh, en, expected in INTENT_PAIRS:
        with _locale("zh"):
            got_zh = detect_intent(zh)[0]
        with _locale("en"):
            got_en = detect_intent(en)[0]
        assert got_zh == got_en == expected, (
            f"意图不一致：中文「{zh}」→{got_zh}；英文「{en}」→{got_en}；期望 {expected}"
        )
