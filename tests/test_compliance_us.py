"""美国合规口径护栏（证照清单 + 英文匹配）。

两条曾经静默失效的点，各守一条：

1. **司法辖区**：证照名必须换成美国真实名称。旧清单是「食品经营许可证 /
   医疗机构执业许可证」——照着去美国申请，等于申请一个不存在的东西。
2. **英文匹配**：旧实现只在文本里找**中文行业键**（"餐饮"），英文部署下
   `if keyword in text` 永远不成立 → 合规检测恒返回 0 条，界面显示
   「未识别到特殊合规要求」，看着像"你不需要任何证照"。这是静默失效，
   不是报错，必须靠断言兜住。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from i18n import reset_locale, set_locale, t  # noqa: E402
from tools.compliance_map import (  # noqa: E402
    INDUSTRY_COMPLIANCE_KEYWORDS,
    INDUSTRY_COMPLIANCE_MAP,
)
from tools.pitfall_detector import _do_compliance_check  # noqa: E402

# 中文行业键：证照清单与关键词表必须一一对应（漏一个 = 该行业静默不检测）
def test_every_industry_has_licenses_and_keywords():
    assert INDUSTRY_COMPLIANCE_MAP.keys() == INDUSTRY_COMPLIANCE_KEYWORDS.keys(), (
        f"证照清单与英文关键词表不一致: "
        f"{set(INDUSTRY_COMPLIANCE_MAP) ^ set(INDUSTRY_COMPLIANCE_KEYWORDS)}"
    )
    for key, items in INDUSTRY_COMPLIANCE_MAP.items():
        assert items, f"{key} 的证照清单为空"
        assert INDUSTRY_COMPLIANCE_KEYWORDS[key], f"{key} 缺英文关键词"


def test_license_names_are_us_english():
    """证照名不得含中文——残留即说明还有中国口径没换干净。"""
    for key, items in INDUSTRY_COMPLIANCE_MAP.items():
        for name in items:
            assert not any("一" <= ch <= "鿿" for ch in name), f"{key}: {name}"


def test_no_china_only_license_names():
    """旧中国证照名不得残留（回退护栏）。"""
    china_names = ("食品经营许可证", "卫生许可证", "医疗机构执业许可证",
                   "办学许可证", "网络文化经营许可证", "道路运输经营许可证",
                   "特种行业许可证", "动物诊疗许可证", "医师执业证")
    for key, items in INDUSTRY_COMPLIANCE_MAP.items():
        for name in items:
            assert name not in china_names, f"{key} 仍是中国证照名: {name}"


# ── 英文匹配（旧实现静默失效的地方）──────────────────────────────────────

_EN_CASES = [
    ("open a coffee shop", "餐饮"),
    ("I want to open a restaurant", "餐饮"),
    ("mobile dog grooming business", "宠物"),
    ("a small trucking company", "物流"),
    ("online tutoring for kids", "教育"),
    ("a med spa and salon", "美容"),
]


def test_english_description_matches_licenses():
    """英文描述必须能命中证照——旧实现在这里恒返回 0。"""
    for desc, expected_key in _EN_CASES:
        r = _do_compliance_check(desc, "")
        assert r["pitfall_count"] > 0, f"{desc!r} 未命中任何证照"
        assert expected_key in r["analyzed_industry_keywords"], (
            f"{desc!r} 命中行业 {r['analyzed_industry_keywords']}，期望含 {expected_key}"
        )


def test_chinese_industry_key_still_matches():
    """传中文行业键（历史调用方式）仍要命中。"""
    r = _do_compliance_check("open a coffee shop", "餐饮")
    assert r["pitfall_count"] > 0
    assert r["analyzed_industry_keywords"] == ["餐饮"]


def test_non_regulated_industry_no_false_positive():
    """普通 SaaS 不该报出餐饮/医疗类证照（防关键词过宽）。"""
    r = _do_compliance_check("a SaaS product for project management", "")
    assert r["pitfall_count"] == 0, r["required_licenses"]


def test_warning_text_is_us_jurisdiction():
    """告警文案必须说明是美国口径 —— 旧文案写的是 China baseline。"""
    set_locale("en")
    try:
        warn = t("pt.license_required.warning", n=3)
        assert "US" in warn, warn
        assert "China" not in warn and "Chinese" not in warn, warn
        act = t("pt.license_none.action")
        assert "EIN" in act and "China" not in act, act
    finally:
        reset_locale()
