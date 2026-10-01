"""英文参数解析缺口回归（M-03）。

锁定两类曾失败的英文口语写法：
- P-01: 单数/无 at 的人力配对（"2 employee 2800"）
- P-02: 裸 cost 比例（"cost 55%"）

同时覆盖抢词回归，防止补齐关键词时误伤相邻字段。
这些断言是英文产品的 oracle —— 输入是英文，期望是英文产品的行为。

测试自带 locale 隔离（autouse fixture）：不依赖 SHANGZHU_LOCALE 环境变量，
也不受前序测试 set_locale/reset_locale 的影响。locale 是全局 ContextVar，
不自隔离的测试会随运行顺序/环境变量漂移（实测：单跑过、全量挂）。
"""
import pytest

from i18n import reset_locale, set_locale
from router.param_extractor import extract_params


@pytest.fixture(autouse=True)
def _en_locale():
    """每个用例都强制在 en 下跑，用完还原部署级默认。"""
    set_locale("en")
    yield
    reset_locale()

# ── P-01: 人力配对（人数 + 人均薪资）────────────────────────────────────────
LABOR_PAIRS = [
    ("2 employee 2800", 2.0, 2800.0),        # 单数 + 无 at（原失败）
    ("2 employees at 2800", 2.0, 2800.0),    # 复数 + at（原成功，防回归）
    ("3 staff 4500", 3.0, 4500.0),           # 单数 staff 无 at
    ("5 workers at 3000", 5.0, 3000.0),
    ("2 person 2800", 2.0, 2800.0),
    ("1 employee 12000", 1.0, 12000.0),
    ("4 hires 3500", 4.0, 3500.0),
    ("salary 5000 x 2", 2.0, 5000.0),        # 反序（薪 x 人）
]


@pytest.mark.parametrize("text,count,salary", LABOR_PAIRS)
def test_labor_pair_extracts_count_and_salary(text, count, salary):
    params = extract_params(text) or {}
    assert params.get("employee_count") == pytest.approx(count), params
    assert params.get("avg_salary") == pytest.approx(salary), params


# ── P-03: 千分位写法（"2,800"）——英文真实输入必带逗号 ──────────────────────
# 曾失败：labor_pair 正则不吃千分位 → "2,800" 只捕获到 "2" → 被合理性门禁
# （薪资须 ≥500）判废 → 人数与薪资**双双丢失**，人工成本整块缺。
THOUSANDS_LABOR = [
    ("2 employees at 2,800", 2.0, 2800.0),
    ("2 employees at $2,800 each", 2.0, 2800.0),
    ("2 employees, 2800 each", 2.0, 2800.0),          # 逗号分隔分句
    ("2 employees, 8000 per month", 2.0, 8000.0),
    ("3 staff at 10,000", 3.0, 10000.0),
]


@pytest.mark.parametrize("text,count,salary", THOUSANDS_LABOR)
def test_thousands_separator_labor_pair(text, count, salary):
    params = extract_params(text) or {}
    assert params.get("employee_count") == pytest.approx(count), params
    assert params.get("avg_salary") == pytest.approx(salary), params


def test_thousands_separator_also_works_for_other_fields():
    """千分位归一前置后，非人力字段同样受益（此前售价认得逗号、人力不认）。"""
    params = extract_params("monthly rent 12,500") or {}
    assert params.get("monthly_rent") == pytest.approx(12500.0), params


# ── P-04: 薪资不得被误抽成客单价（假数据进收入侧，比抽漏更危险）─────────────
SALARY_NOT_PRICE = [
    "I have 2 employees at $2,800 each",
    "2 employees, 2800 each",
    "2 employees at 6000 each",
]


@pytest.mark.parametrize("text", SALARY_NOT_PRICE)
def test_salary_is_not_captured_as_unit_price(text):
    """人工语境里的数字是薪资，不是客单价。

    曾失败：reject_context 护栏只看邻域前 10 字，把窗口内的 "employees" 截断，
    护栏失效 → 2800 变成 price_per_unit → 收入侧凭空多出一个售价。
    """
    params = extract_params(text) or {}
    assert params.get("avg_salary") is not None, params
    assert "price_per_unit" not in params, params


# ── 抢词回归：放宽护栏后，真售价语境不能被误伤 ──────────────────────────────
@pytest.mark.parametrize(
    "text,field,value",
    [
        ("15 each", "price_per_unit", 15.0),
        ("unit price 15 each", "price_per_unit", 15.0),
        ("each bowl 12", "price_per_unit", 12.0),
        ("average ticket 25", "price_per_unit", 25.0),
        ("80 customers a day", "daily_traffic", 80.0),
        ("salary 6000", "avg_salary", 6000.0),
    ],
)
def test_price_and_traffic_not_hurt_by_wider_context_guard(text, field, value):
    params = extract_params(text) or {}
    assert params.get(field) == pytest.approx(value), params


def test_employee_clause_does_not_steal_neighbouring_number():
    """"2 employees, 80 customers a day"：80 是客流，不该被当薪资。"""
    params = extract_params("2 employees, 80 customers a day") or {}
    assert params.get("daily_traffic") == pytest.approx(80.0), params
    assert "avg_salary" not in params, params


def test_legit_price_same_as_salary_number_is_kept():
    """去噪只丢弃「被人力正则认领的那个数」，真售价不受影响。"""
    params = extract_params("unit price 2800, 2 employees") or {}
    assert params.get("price_per_unit") == pytest.approx(2800.0), params


# ── P-02: 变动成本率（0~1 归一）────────────────────────────────────────────
VC_RATIOS = [
    ("cost 55%", 0.55),                       # 裸 cost（原失败）
    ("costs 55%", 0.55),                      # 裸 costs
    ("variable cost ratio 55%", 0.55),        # 完整短语（原成功）
    ("variable cost 60%", 0.60),
    ("cost at 45%", 0.45),
    ("costs are 30%", 0.30),
    ("cost ratio 25%", 0.25),
]


@pytest.mark.parametrize("text,ratio", VC_RATIOS)
def test_variable_cost_ratio_extracts_and_normalises(text, ratio):
    params = extract_params(text) or {}
    assert params.get("variable_cost_ratio") == pytest.approx(ratio), params


def test_bare_cost_not_stolen_by_unit_cost_field():
    """裸 cost 是比例，不该被 unit_variable_cost 抢成「每份 X 元」。"""
    params = extract_params("cost 55%") or {}
    assert "unit_variable_cost" not in params, params


def test_unit_cost_still_routes_to_unit_variable_cost():
    """"unit cost 55" 属于单位成本，不是比例 —— 补齐 cost 后不能误伤。"""
    params = extract_params("unit cost 55") or {}
    assert params.get("unit_variable_cost") == pytest.approx(55.0), params
    assert "variable_cost_ratio" not in params, params


def test_fixed_cost_percentage_is_not_a_ratio():
    """"fixed cost 55%" 是固定成本额，不是变动成本率。"""
    params = extract_params("fixed cost 55%") or {}
    assert "variable_cost_ratio" not in params, params


# ── 完整输入：用户的 coffee 场景 ────────────────────────────────────────────
def test_coffee_scenario_extracts_all_key_fields():
    """端到端：英文口语一句话应抽出建模所需的核心字段。"""
    text = (
        "I want to sell coffee, daily traffic 80, price 12, "
        "investment 180000, 2 employee 2800, cost 55%, roomrent 1800"
    )
    params = extract_params(text) or {}
    assert params.get("daily_traffic") == pytest.approx(80.0), params
    assert params.get("price_per_unit") == pytest.approx(12.0), params
    assert params.get("total_investment") == pytest.approx(180000.0), params
    assert params.get("employee_count") == pytest.approx(2.0), params
    assert params.get("avg_salary") == pytest.approx(2800.0), params
    assert params.get("variable_cost_ratio") == pytest.approx(0.55), params
    assert params.get("monthly_rent") == pytest.approx(1800.0), params


def test_regression_sentence_still_parses():
    """原成功的多字段句子不能被新关键词改坏。"""
    text = (
        "I plan to open a noodle shop. Monthly rent 8000, avg daily traffic 80, "
        "avg ticket 25, 2 employees at 5000/mo, variable cost ratio 35%"
    )
    params = extract_params(text) or {}
    assert params.get("monthly_rent") == pytest.approx(8000.0), params
    assert params.get("daily_traffic") == pytest.approx(80.0), params
    assert params.get("price_per_unit") == pytest.approx(25.0), params
    assert params.get("employee_count") == pytest.approx(2.0), params
    assert params.get("avg_salary") == pytest.approx(5000.0), params
    assert params.get("variable_cost_ratio") == pytest.approx(0.35), params
