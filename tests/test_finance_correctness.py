# -*- coding: utf-8 -*-
"""第三轮链路审查：财务正确性 + 行业覆盖 + 0 值语义（F1~F9）

与 tests/test_extractor_coverage.py 的分工：
- 那个文件管「用户说的话有没有被听见」（抽取层）
- 这个文件管「听见之后算得对不对」（计算层）+ 行业覆盖 + 0 值语义

**为什么单独立一个文件**：前两轮的探针判据都是「抽取是否命中」，
而财务正确性需要**外部判据**——保本的定义是「月利润 = 0」，
回收期的定义是「累计利润 ≥ 总投资」，这些判据不来自代码，来自业务定义。
把它们和抽取测试混在一起，会让人误以为"抽取对了"就等于"算对了"。

运行：并入 tests/run_all.py；也可 .venv/bin/python3 tests/test_finance_correctness.py
"""
import sys
import os
import io
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from router.param_extractor import extract_params
from tools.workflow_engine import quick_scan

TOL = 1e-6


def _scan(params):
    return json.loads(quick_scan.invoke(
        {"params_json": json.dumps(params, ensure_ascii=False)}))


def _cm(params):
    d = _scan(params)
    assert "core_metrics" in d, f"quick_scan 未返回 core_metrics: {d}"
    return d["core_metrics"]


# ── F1：保本客流的口径（365 天 vs 月营收的 30 天）─────────────────────────
#
# 外部判据：月营收 = 客流 × 客单价 × **30** 天，所以「每天保本客流」必须
# 用同一个 30 天口径：be = 月固定成本 ÷ 单位贡献毛益 ÷ 30。
#
# 旧实现：_calc_breakeven 返回**年度**保本单位数，除以 **365** 得日均，
# 而营收按 360 天/年（12×30）算 —— 两个口径混用，保本客流被系统性低估
# 约 1.0~1.2%。后果：按系统给的保本客流经营，实际**月亏 200~1240 元**，
# 系统却标「可达保本」。

_BE_CASES = [
    # (月租, 员工数, 人均薪, 客单价, 单件变动成本)
    (8000, 2, 6000, 18, 6),     # 小面馆：真值 55.56
    (12000, 3, 4500, 15, 5),    # 奶茶店：真值 85.00
    (50000, 8, 7000, 30, 12),   # 高客流：真值 196.30（365 口径会差到 194）
]


def _truth_be(rent, n, salary, price, vc, days=30):
    """外部真值：月固定成本 ÷ 单位贡献毛益 ÷ 每月天数。"""
    return (rent + n * salary) / (price - vc) / days


def test_fin_f1_breakeven_matches_30day_basis():
    """保本客流必须与月营收同为 30 天口径（误差仅限取整的 0.5）。"""
    for rent, n, sal, price, vc in _BE_CASES:
        base = dict(industry="餐饮", monthly_rent=rent, employee_count=n,
                    avg_salary=sal, price_per_unit=price, unit_variable_cost=vc)
        be = _cm(dict(base, daily_traffic=1)).get("daily_breakeven")
        truth = _truth_be(rent, n, sal, price, vc)
        assert be is not None, base
        assert abs(be - truth) <= 0.5, (
            f"保本客流 {be} 与 30 天口径真值 {truth:.2f} 不符（差 {be - truth:+.2f}）")


def test_fin_f1_running_at_breakeven_does_not_lose_money():
    """端到端：按系统给的保本客流经营，亏损不得超过「一天份」的贡献毛益。"""
    for rent, n, sal, price, vc in _BE_CASES:
        base = dict(industry="餐饮", monthly_rent=rent, employee_count=n,
                    avg_salary=sal, price_per_unit=price, unit_variable_cost=vc)
        be = _cm(dict(base, daily_traffic=1)).get("daily_breakeven")
        profit = _cm(dict(base, daily_traffic=be)).get("monthly_profit")
        one_day = (price - vc) * 30          # 客流差 1 碗/天 → 月利润差这么多
        assert abs(profit) <= one_day, (
            f"保本客流 {be} 经营却月利润 {profit:+.0f}（超过一天贡献 {one_day:.0f}）")


# ── F2：投资回收期 ≠ 盈亏平衡月 ────────────────────────────────────────────
#
# 概念区分：
#   盈亏平衡月 (breakeven_month)  = 累计利润首次转正 → 多快**不再亏**
#   投资回收期 (payback_months)   = 累计利润 ≥ 总投资 → 多快**回本**
# 旧实现直接 `payback_months = breakeven_month`，把两者混为一谈：
# 20 万投资、月利 1.6 万 → 真值 12.5 个月，系统说 **1 个月**。

def test_fin_f2_payback_recovers_total_investment():
    """回收期必须是「累计利润 ≥ 总投资」的月份，不是「利润首次转正」的月份。"""
    d = _scan(dict(industry="餐饮", total_investment=200000, monthly_rent=8000,
                   employee_count=2, avg_salary=6000, daily_traffic=100,
                   price_per_unit=18, unit_variable_cost=6))
    im = d.get("investment_metrics") or {}
    payback = im.get("payback_months")
    profit = d["core_metrics"]["monthly_profit"]
    truth = 200000 / profit                     # 12.5 个月
    assert payback is not None, "未提供回收期"
    assert payback >= truth - 1, (
        f"回收期 {payback} 个月，但 {truth:.1f} 个月才能累计赚回总投资 20 万")


def test_fin_f2_payback_absent_without_investment():
    """没给总投资 → 回收期不可算，必须是 None（不得用盈亏平衡月冒充）。"""
    d = _scan(dict(industry="餐饮", monthly_rent=8000, employee_count=2,
                   avg_salary=6000, daily_traffic=100, price_per_unit=18,
                   unit_variable_cost=6))
    im = d.get("investment_metrics") or {}
    assert im.get("payback_months") is None, im


# ── F4：两个行业有模板却无法识别（模板死代码）─────────────────────────────
#
# industry_templates.yaml 有 12 个行业，而 INDUSTRY_KEYWORDS 只有 10 个：
# 「房地产」「企业服务」的完整模板永远走不到。


def test_cov_f4_every_template_industry_is_reachable():
    """护栏：模板里的每个行业都必须能被识别（防止再出现死模板）。"""
    import yaml
    from router.param_extractor import INDUSTRY_KEYWORDS
    tpl_path = os.path.join(os.path.dirname(__file__), "..",
                            "config", "industry_templates.yaml")
    with open(tpl_path, encoding="utf-8") as f:
        tpls = yaml.safe_load(f).get("industry_templates", {})
    unreachable = sorted(set(tpls) - set(INDUSTRY_KEYWORDS))
    assert not unreachable, f"有模板但无法识别的行业：{unreachable}"


# ── F5：宠物 vs 医疗（词表顺序导致误判）───────────────────────────────────
#
# _detect_industry 按 dict 顺序取首个命中。「宠物医院」里的「医院」在
# 医疗词表，而医疗排在宠物之前 → 宠物医院被判成医疗。


# ── F6/F7：零散漏抽 ────────────────────────────────────────────────────────


def test_cov_f7_mcn_case_insensitive():
    """行业关键词匹配必须大小写不敏感（用户输入「MCN」很常见）。"""
    for text in ("做MCN", "做mcn", "做 MCN 机构"):
        got = extract_params(text).get("industry")
        assert got == "内容", f"{text} → industry={got}（期望 内容）"


# ── F8：一句多参数时不能丢字段 ─────────────────────────────────────────────


# ── F9：0 是合法值，不是缺失 ───────────────────────────────────────────────
#
# `x or None` 会把 0 当 falsy 转成 None。财务场景里 0 是合法结果：
#   变动成本率 0（毛利率 100%）→ 月变动成本 = 0，不是"算不出来"
#   夫妻店无雇员 → 月人工 = 0，不是"未知"
# 旧实现让「能算但算成 0」被当成「算不出来」，利润显示"未知"。

def test_fin_f9_zero_variable_cost_ratio_is_zero_not_none():
    """变动成本率 0 → 月变动成本 0、利润 = 营收 − 固定成本，不是未知。"""
    p = dict(industry="餐饮", monthly_rent=8000, daily_traffic=100,
             price_per_unit=18, variable_cost_ratio=0.0)
    d = _scan(dict(p))
    cm = d["core_metrics"]
    assert cm.get("monthly_profit") is not None, f"利润被当成未知: {cm}"
    assert abs(cm["monthly_profit"] - (100 * 18 * 30 - 8000)) < 1, cm


def test_fin_f9_zero_employees_labor_is_zero():
    """无雇员 → 月人工 0，不是未知。"""
    from field_model import derive
    r = derive(dict(industry="餐饮", monthly_rent=8000, daily_traffic=100,
                    price_per_unit=18, variable_cost_ratio=0.33,
                    employee_count=0))
    d = r[0] if isinstance(r, tuple) else r
    assert d.get("monthly_labor") == 0, f"月人工应为 0，实得 {d.get('monthly_labor')}"


def test_fin_f9_missing_input_still_none():
    """护栏：真正的缺失仍是 None（不得因为放开 0 而把缺失变成 0）。"""
    from field_model import derive
    r = derive(dict(industry="餐饮", monthly_rent=8000))
    d = r[0] if isinstance(r, tuple) else r
    assert d.get("monthly_revenue") is None, d
    assert d.get("monthly_labor") is None, d


# ── 不变量：时间口径不得再被硬编码 ─────────────────────────────────────────
#
# F1 的根因不是「算错了」，而是「同一个量在两处各写了一遍不同的值」
# （营收 30 天/月、保本 365 天/年）。修完一个数字没用，必须禁止再写数字。
# 这条测试是**结构性护栏**：以后谁再写 `* 30` 或 `/ 365` 就红。

def test_invariant_no_hardcoded_time_basis():
    """时间口径只能来自 field_model.DAYS_PER_MONTH / DAYS_PER_YEAR。"""
    import glob
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # 显式白名单：与时间口径无关，但写法会命中正则。每条必须写清理由，
    # 这样以后有人再加「* 30」就得显式登记，不能悄悄混过去。
    allowlist = {
        # 归因条形图的像素长度，30 是画布宽度，不是「一个月多少天」
        "src/router/formatter.py: bar_len": True,
    }
    bad = []
    for path in glob.glob(os.path.join(root, "src", "**", "*.py"), recursive=True) + \
                [os.path.join(root, "web_server.py")]:
        rel = os.path.relpath(path, root)
        for ln, line in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            if "DAYS_PER_MONTH" in line or "DAYS_PER_YEAR" in line:
                continue
            if re.search(r'\*\s*30\b|\b30\s*天|/\s*365|\*\s*365', line):
                key = f"{rel}: {line.strip().split('=')[0].strip()}"
                if allowlist.get(key):
                    continue
                bad.append(f"{rel}:{ln}: {line.strip()[:90]}")
    assert not bad, "发现硬编码的时间口径，请改用 field_model 的常量：\n" + "\n".join(bad)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa
            print(f"ERROR {t.__name__}: {e!r}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
