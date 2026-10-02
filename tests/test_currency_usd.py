"""币种口径护栏：本部署是 **USD**，不是 CNY。

为什么要独立成文件：
币种是**产品事实**不是文案偏好。改错不会抛异常、不会崩，只会让每个金额
悄悄错一个量级（或错一个符号）——典型的「不报错，只是结论错了」。

三条防线：
1. 展示层不得残留 CNY 字面量（含生成器脚本，否则下次重生成会把它带回来）；
2. 行业模板的金额字段必须落在 USD 合理区间（人民币量级回写 = 立刻红）；
3. 文案里硬编码的金额必须与代码里的常量一致（配置漂移护栏）。

locale：conftest.py 把 SHANGZHU_LOCALE 钉成 zh，故英文断言用
`set_locale("en")` 会话级覆盖。
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import yaml  # noqa: E402

from i18n import reset_locale, set_locale, t  # noqa: E402
from tools.param_advisor import TYPICAL_UNIT_PRICE_RANGES  # noqa: E402
from tools.workflow_engine import quick_scan  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


# ── 1. 展示层：不得残留 CNY ───────────────────────────────────────────────

def test_no_cny_literal_in_en_copy():
    """英文文案里不得再有 CNY —— 残留意味着某个金额还标着人民币。"""
    assert "CNY" not in _read(os.path.join("src", "i18n", "en.yaml"))


def test_no_cny_literal_in_zh_copy():
    """中文侧同样不得残留人民币单位。

    币种是**产品事实**不是语言偏好：中英两份文案若一个写 元 一个写 $，
    同一份报表切个语言就换一种货币。
    ⚠️ 只扫 i18n/zh.yaml —— `router/rules/zh.yaml` 里的「元」是**中文输入
    解析规则**（用户说「5000元」要能抽出来），必须保留。
    """
    zh = _read(os.path.join("src", "i18n", "zh.yaml"))
    leftovers = [
        (i, l.strip()) for i, l in enumerate(zh.split("\n"), 1)
        if re.search(r"(?<![美国])元(?![月年单件])", l) and "美元" not in l
    ]
    assert not leftovers, f"zh.yaml 仍有人民币单位「元」: {leftovers}"


def test_zh_money_template_renders_dollar():
    """中文金额文案渲染出来必须是美元，不是「元」。"""
    set_locale("zh")
    try:
        assert t("fmt.common.money", v="1,000") == "$1,000", t("fmt.common.money", v="1,000")
        assert t("field.unit.yuan_per_month") == "美元/月"
        assert t("pg.unit.cny") == "美元"
        assert t("ss.fmt.yuan") == "美元"
    finally:
        reset_locale()


def test_input_rules_keep_chinese_yuan():
    """中文抽取规则必须保留「元」——那是用户输入，不是展示币种。

    与上面两条正好相反，放在一起是为了让「哪些 元 该留、哪些该改」有断言兜着，
    别有人看到护栏就顺手把规则文件也改了。
    """
    rules = _read(os.path.join("src", "router", "rules", "zh.yaml"))
    assert "元" in rules, "中文输入规则里的「元」被误删——「5000元」将抽不出来"


def test_bench_generator_emits_usd_basis():
    """bench 生成脚本必须与 en.yaml 同口径。

    否则下次跑 `_gen_bench_yaml.py` 重生成，「USD basis」会被悄悄改回 CNY。
    """
    src = _read(os.path.join("scripts", "_gen_bench_yaml.py"))
    assert "CNY" not in src, "生成器仍在输出 CNY 口径"
    assert "USD basis" in src


def test_money_template_renders_dollar_sign():
    """金额文案渲染出来必须带 $ 在前（美元语序），不是「1000 $」。"""
    set_locale("en")
    try:
        assert t("fmt.common.money", v="1,000") == "$1,000", t("fmt.common.money", v="1,000")
        assert t("field.unit.yuan_per_month") == "$/month"
        assert t("pg.unit.cny") == "$"
    finally:
        reset_locale()


# ── 2. 行业模板金额：必须落在 USD 合理区间 ────────────────────────────────

# 美国全职月薪（税前）合理带：联邦最低时薪全职 ≈ $1,250，高薪专业岗 ≈ $15,000。
_USD_SALARY_BAND = (2000, 15000)

# 逐行业的既定取值（USD/月·人）。
# 为什么用「金样值」而不是区间：人民币量与美元量在 2000~15000 这个带里
# **重叠**（旧值 4500~20000 大半落在带内），区间护栏抓不到回退；
# 金样值把「我们决定用哪个数」钉死，任何改动都必须显式更新这里。
_EXPECTED_SALARY = {
    "餐饮": 3200, "零售": 3000, "SaaS": 9500, "教育": 3800,
    "电商": 4200, "制造": 3800, "宠物": 3200, "医疗": 6000,
    "金融": 8000, "内容": 5000, "房地产": 4800, "企业服务": 7800,
    "<fallback>": 4000,
}


def test_template_salary_is_usd_golden():
    cfg = yaml.safe_load(_read(os.path.join("config", "industry_templates.yaml")))
    salaries = {}
    for name, entry in (cfg.get("industry_templates") or {}).items():
        if not isinstance(entry, dict):
            continue
        v = (entry.get("hypotheses") or {}).get("avg_salary")
        if v is not None:
            salaries[name] = v
    fb = (cfg.get("fallback_template") or {}).get("hypotheses") or {}
    if fb.get("avg_salary") is not None:
        salaries["<fallback>"] = fb["avg_salary"]

    assert salaries == _EXPECTED_SALARY, (
        f"行业默认薪资与既定 USD 取值不一致（回退到人民币量级？）\n"
        f"  实际: {salaries}\n  期望: {_EXPECTED_SALARY}"
    )
    for name, v in salaries.items():
        assert _USD_SALARY_BAND[0] <= v <= _USD_SALARY_BAND[1], (
            f"{name} avg_salary={v} 超出美国月薪合理区间 {_USD_SALARY_BAND}"
        )


# ── 3. 文案硬编码金额 必须 与代码常量一致 ─────────────────────────────────

def test_low_price_copy_matches_typical_range():
    """pa.issue.low_price 里写的「典型 $lo-$hi」必须等于代码里的行业区间。

    两处各写一遍金额 = 改币种时必然只改一处、另一处悄悄失真（本项目已发生过
    多起同型漂移）。文案侧改成从常量渲染更好，但先守住「不一致即红」。
    """
    en = yaml.safe_load(_read(os.path.join("src", "i18n", "en.yaml")))
    copy = en["pa"]["issue"]["low_price"]
    m = re.search(r"\$(\d+)-(\d+)", copy)
    assert m, f"文案里找不到 $lo-$hi 形式: {copy}"
    in_copy = (int(m.group(1)), int(m.group(2)))
    assert in_copy == TYPICAL_UNIT_PRICE_RANGES["餐饮"], (
        f"文案写 {in_copy}，代码常量是 {TYPICAL_UNIT_PRICE_RANGES['餐饮']} —— 配置漂移"
    )


def test_typical_price_ranges_are_usd_scale():
    """客单价区间必须是美元量级。

    ¥20-35 ≈ $2.8-4.9，在美国餐饮语境下是荒谬低价 —— 若有人按汇率折算回写，
    这里会立刻红。
    """
    assert TYPICAL_UNIT_PRICE_RANGES["餐饮"] == (12, 22)
    for ind, (lo, hi) in TYPICAL_UNIT_PRICE_RANGES.items():
        assert lo >= 5, f"{ind} 下限 {lo} 不像美元客单价（汇率折算回写？）"
        assert hi > lo


# ── 4. 端到端：报表里是美元 ───────────────────────────────────────────────

def _scan(params_dict):
    return json.loads(quick_scan.invoke(
        {"params_json": json.dumps(params_dict, ensure_ascii=False)}))


def test_report_shows_dollar_and_no_cny():
    """真实报表输出：金额带 $，通篇没有 CNY / 元。"""
    set_locale("en")
    try:
        d = _scan({"monthly_rent": 6000, "monthly_revenue": 30000,
                   "variable_cost_ratio": 0.5, "total_investment": 100000,
                   "employee_count": 3, "avg_salary": 3200})
        blob = json.dumps(d, ensure_ascii=False)
        assert "CNY" not in blob
        assert "元" not in blob
        assert "$" in blob or "USD" in blob
    finally:
        reset_locale()
