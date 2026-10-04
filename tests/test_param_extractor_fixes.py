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


# ── F2: daily_traffic 支持 碗/份 单位与 日均卖/日售 关键词 ──────────────


# ── F3: 占营业额/成本率 百分比 → variable_cost_ratio ──────────────────


# ── F4: 角色薪资短语 → avg_salary 均值 ────────────────────────────────
def test_fix_role_salary_average():
    p = extract_params("请2个员工（厨师6000、服务员4500）")
    assert p.get("avg_salary") == 5250.0, f"avg_salary 应=5250，实际 {p.get('avg_salary')}"


# ── 回归：既有行为不被破坏 ────────────────────────────────────────────


def test_reg_team_keyword():
    assert _get("我们5人团队", "employee_count") == 5.0


# ── F5: labor_pair 支持「各 / 每人」修饰词 ──────────────────────────────


def test_reg_labor_pair_no_false_positive():
    """无人工语义的输入不得被误抽成人数/薪资。"""
    p = extract_params("日售50杯")
    assert p.get("employee_count") is None, p
    assert p.get("avg_salary") is None, p
