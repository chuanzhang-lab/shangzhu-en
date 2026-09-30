"""参数解析器缺陷修复的回归守护。

覆盖 2026-07-13 审计中由「开羊肉汤馆」场景暴露的 4 个解析缺陷：
  F1. employee_count 误把紧跟「员工」后的薪资数字(6000)当人数（应取带单位「个」的 2）
  F2. daily_traffic 漏抓「日均卖80碗 / 日售50杯」（碗/份 单位 + 日均卖/日售 关键词缺失）
  F3. 变动成本率漏抓「食材成本占营业额45%」（被误当「每份成本45元」）
  F4. 「厨师6000、服务员4500」角色薪资未被识别为 avg_salary（均值 5250）

同时含回归用例，确保修复不破坏既有「人工2人8000 / 5人团队 / 月租8000 /
总投资30万 / strict_units 允许裸数字」等行为。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from router.param_extractor import extract_params


def _get(text, key):
    return extract_params(text).get(key)


# ── F1: employee_count 不被薪资数字污染 ────────────────────────────────
def test_fix_employee_count_not_grab_salary():
    p = extract_params("请2个员工（厨师6000、服务员4500），月租金8000")
    assert p.get("employee_count") == 2.0, f"employee_count 应=2，实际 {p.get('employee_count')}"


# ── F2: daily_traffic 支持 碗/份 单位与 日均卖/日售 关键词 ──────────────
def test_fix_daily_traffic_bowl():
    assert _get("日均卖80碗", "daily_traffic") == 80.0
    assert _get("日售50杯", "daily_traffic") == 50.0
    assert _get("每天卖120份", "daily_traffic") == 120.0


# ── F3: 占营业额/成本率 百分比 → variable_cost_ratio ──────────────────
def test_fix_cost_ratio_from_percentage():
    p = extract_params("食材成本占营业额45%，客单价25元")
    assert p.get("variable_cost_ratio") == 0.45, p
    # 不应被误当成「每份成本45元」
    assert "unit_variable_cost" not in p, f"unit_variable_cost 不应被设置: {p}"


# ── F4: 角色薪资短语 → avg_salary 均值 ────────────────────────────────
def test_fix_role_salary_average():
    p = extract_params("请2个员工（厨师6000、服务员4500）")
    assert p.get("avg_salary") == 5250.0, f"avg_salary 应=5250，实际 {p.get('avg_salary')}"


# ── 回归：既有行为不被破坏 ────────────────────────────────────────────
def test_reg_labor_pair_still_works():
    p = extract_params("人工2人8000")
    assert p.get("employee_count") == 2.0
    assert p.get("avg_salary") == 8000.0


def test_fix_labor_per_person():
    """「员工2人,每人3000元」：人数与「每人/人均」薪资都要抽到，不得落行业默认。"""
    for text in (
        "员工2人,每人3000元",
        "员工2人，每人3000元",
        "员工2人，人均3000元",
        "员工2人 每人3千",
    ):
        p = extract_params(text)
        assert p.get("employee_count") == 2.0, f"{text!r} count={p.get('employee_count')}"
        assert p.get("avg_salary") == 3000.0, f"{text!r} salary={p.get('avg_salary')}"


def test_fix_investment_shorthand_投():
    """「投20万」口语短写也能抽到 total_investment（修「投」漏抓）。"""
    cases = {"投20万": 200000.0, "投了30万": 300000.0, "总共投25万": 250000.0}
    for text, exp in cases.items():
        p = extract_params(text)
        assert p.get("total_investment") == exp, f"{text!r} -> {p.get('total_investment')}"


def test_fix_vc_noise_from_adjacent_traffic():
    """「日售50杯变动成本率55%」：不得把相邻客流 50 误当变动成本率。"""
    p = extract_params("日售50杯变动成本率55%")
    assert p.get("daily_traffic") == 50.0, p
    assert p.get("variable_cost_ratio") == 0.55, p
    assert p.get("variable_cost_rate") is None, f"不应残留通用噪声: {p}"


def test_fix_labor_salary_times_count():
    """「人工3500*2 / 人工3500×2」= 2人×3500，绝不能抽成 employee_count=3500。"""
    for text in ("人工3500*2", "人工3500×2", "人工3500x2", "人工 3500 * 2",
                 "人工2*3500", "2人*3500", "工资3500*2人"):
        p = extract_params(text)
        assert p.get("employee_count") == 2.0, f"{text!r} count={p.get('employee_count')}"
        assert p.get("avg_salary") == 3500.0, f"{text!r} salary={p.get('avg_salary')}"


def test_fix_labor_not_inflate_with_context():
    """完整句里「人工3500*2」仍正确，不被「人工」关键词通用规则覆盖。"""
    p = extract_params("月租金1500，日售50杯，单价15，变动成本率60%，人工3500*2")
    assert p.get("employee_count") == 2.0, p
    assert p.get("avg_salary") == 3500.0, p
    assert p.get("monthly_rent") == 1500.0, p


def test_reg_team_keyword():
    assert _get("我们5人团队", "employee_count") == 5.0


def test_reg_rent_bare_number():
    assert _get("月租8000", "monthly_rent") == 8000.0


def test_reg_investment_wan():
    assert _get("总投资30万", "total_investment") == 300000.0


def test_reg_strict_units_allows_bare_arabic():
    # P4-4 语义：strict_units 仍允许无单位阿拉伯数字（固定成本2500 → 2500），
    # 只禁中文量词兜底。修复改动不得破坏此契约。
    assert _get("固定成本2500", "monthly_expense") == 2500.0


def test_reg_full_mutton_scenario():
    text = ("我想开一家羊肉汤馆，总投资30万（装修15万+设备8万+首批原料7万），"
            "月租金8000，请2个员工（厨师6000、服务员4500），"
            "羊肉等食材成本占营业额45%，客单价25元，日均卖80碗，"
            "水电杂费每月2500")
    p = extract_params(text)
    assert p.get("employee_count") == 2.0
    assert p.get("avg_salary") == 5250.0
    assert p.get("daily_traffic") == 80.0
    assert p.get("variable_cost_ratio") == 0.45
    assert p.get("monthly_rent") == 8000.0
    assert p.get("total_investment") == 300000.0
    assert "unit_variable_cost" not in p
    # 「水电杂费」复合词不得被 utilities 与 other_fixed 各抓一次（双重计数）
    assert p.get("utilities") == 2500.0, f"utilities 应=2500，实际 {p.get('utilities')}"
    assert "other_fixed" not in p, f"other_fixed 应去重移除，实际 {p.get('other_fixed')}"


def test_dedup_utilities_vs_other_fixed():
    """「水电杂费2500」只应计入一次；分开给「水电2000、杂费1000」则各自保留。"""
    p1 = extract_params("水电杂费每月2500")
    assert p1.get("utilities") == 2500.0
    assert "other_fixed" not in p1, "复合词应去重，other_fixed 不得重复计入"

    p2 = extract_params("水电2000、杂费1000")
    assert p2.get("utilities") == 2000.0
    assert p2.get("other_fixed") == 1000.0, "分别给出时应各自保留"


# ── F5: labor_pair 支持「各 / 每人」修饰词 ──────────────────────────────
def test_fix_labor_pair_each_keyword():
    """「人工2人各5000」必须同时抽出人数与薪资。

    缺「各」字的后果不是少一个字段：avg_salary 漏抽 → monthly_labor 算不出
    → 人工成本整体不进固定成本（实测月利润 -1,000 vs 正确的 -11,000）。
    """
    for text, exp_n, exp_s in [
        ("人工2人各5000", 2.0, 5000.0),
        ("2人各5000", 2.0, 5000.0),
        ("员工2人各8000", 2.0, 8000.0),
        ("人工3人各7000", 3.0, 7000.0),
        ("2人每人5000", 2.0, 5000.0),
        ("人工2人各5000元", 2.0, 5000.0),
    ]:
        p = extract_params(text)
        assert p.get("employee_count") == exp_n, f"{text}: employee_count={p.get('employee_count')}"
        assert p.get("avg_salary") == exp_s, f"{text}: avg_salary={p.get('avg_salary')}"


def test_reg_labor_pair_without_each_still_works():
    """补「各」不得破坏既有不含修饰词的写法。"""
    assert extract_params("2人8000").get("avg_salary") == 8000.0
    assert extract_params("人工2人8000").get("avg_salary") == 8000.0
    assert extract_params("人工3500*2").get("avg_salary") == 3500.0


def test_reg_labor_pair_no_false_positive():
    """无人工语义的输入不得被误抽成人数/薪资。"""
    p = extract_params("日售50杯")
    assert p.get("employee_count") is None, p
    assert p.get("avg_salary") is None, p
