"""字段模型（field_model）验收：声明式公式唯一出处 + 拓扑求值 + 一致性规则。

覆盖（结构性重构的根基，模型层自身可独立测）：
- FM1 derive() 全输入 → 所有派生字段精确算出，值与手工公式一致
- FM2 缺依赖 → 该字段 missing(None)，能算的照算（部分求值）
- FM3 依赖链缺算 → missing 追溯到根因（缺哪个用户输入）
- FM4 一致性规则：月营收 vs 客流×单价 打架即报；一致不报；缺输入不报
- FM5 derived_values 展示：ok 带公式(实际数字)、missing 带中文根因
- FM6 公式唯一出处：DERIVED_SPECS 声明一次，derive/derived_values 同源

运行：并入 tests/run_all.py；也可 .venv/bin/python tests/test_field_model.py
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from field_model import (
    DERIVED_SPECS, INPUT_SPECS, derive, consistency_issues, derived_values, _DERIVED_ORDER,
    field_unit,
)


_FULL = {"daily_traffic": 60, "price_per_unit": 10, "employee_count": 2,
         "avg_salary": 3000, "labor_burden": 0.0, "variable_cost_ratio": 0.55,
         "monthly_fixed_cost": 7800, "total_investment": 200000}


def test_fm1_full_derive():
    """FM1：全输入 → 全部派生字段精确算出。"""
    d, m = derive(_FULL)
    assert d["monthly_revenue"] == 18000       # 60×10×30
    assert d["monthly_labor_cash"] == 6000     # 2×3000
    assert d["monthly_labor"] == 6000          # ×(1+0)
    assert d["monthly_variable_cost"] == 9900  # 18000×0.55
    assert d["monthly_profit"] == 300          # 18000-7800-9900
    assert d["variable_cost_per_unit"] == 5.5  # 10×0.55
    assert d["annual_fixed_cost"] == 93600     # 7800×12
    assert d["gross_margin"] == 0.45            # S3：统一 0~1 口径（1 − 0.55）
    assert d["available_cash"] == 200000
    # meta 验证
    assert m["monthly_revenue"]["source"] == "derived"
    assert m["variable_cost_ratio"]["source"] == "user"


def test_fm2_partial_when_missing():
    """FM2：缺依赖 → 该字段 missing，能算的照算。"""
    p = dict(_FULL); p.pop("variable_cost_ratio")
    d, m = derive(p)
    assert d["monthly_revenue"] == 18000       # 仍能算
    assert d["monthly_labor"] == 6000
    assert d["monthly_variable_cost"] is None  # 缺 vc
    assert d["monthly_profit"] is None
    assert d["gross_margin"] is None
    assert d["annual_fixed_cost"] == 93600     # 固定成本已知仍能算
    assert m["monthly_variable_cost"]["source"] == "missing"


def test_fm3_missing_root_cause():
    """FM3：missing 追溯到根因（缺哪个用户输入）。"""
    p = dict(_FULL); p.pop("employee_count"); p.pop("avg_salary")
    d, _ = derive(p)
    dv = derived_values(p, {})
    labor = next(x for x in dv if x["field"] == "monthly_labor")
    assert labor["status"] == "missing"
    assert "员工人数" in labor["missing"] and "人均薪资" in labor["missing"], labor


def test_fm4_consistency_rules():
    """FM4：一致性规则——打架即报、一致不报、缺输入不报。"""
    conflict = {**_FULL, "monthly_revenue": 20000, "daily_traffic": 100, "price_per_unit": 12}
    issues = consistency_issues(conflict)
    assert any("月营收" in i["message"] and "36,000" in i["message"] for i in issues), issues
    ok = {**_FULL, "monthly_revenue": 36000, "daily_traffic": 100, "price_per_unit": 12}
    assert consistency_issues(ok) == []
    missing = {**_FULL, "monthly_revenue": 20000}
    assert consistency_issues(missing) == []


def test_fm5_derived_values_shape():
    """FM5：derived_values 展示——ok 带公式、missing 带根因。"""
    dv = derived_values(_FULL, {})
    ok = [x for x in dv if x["status"] == "ok"]
    assert len(ok) == len([k for k in DERIVED_SPECS if not DERIVED_SPECS[k].get("_hidden")])
    rev = next(x for x in dv if x["field"] == "monthly_revenue")
    assert rev["formula"] == "日均客流 60 × 客单价 10 × 30天", rev
    profit = next(x for x in dv if x["field"] == "monthly_profit")
    assert "18000" in profit["formula"] and "7800" in profit["formula"], profit


def test_fm6_formula_single_source():
    """FM6：公式唯一出处——derive 与 derived_values 同源（展示层按 display_percent ×100）。"""
    d, _ = derive(_FULL)
    dv = {x["field"]: x for x in derived_values(_FULL, {})}
    for field, val in d.items():
        if field not in dv or val is None:
            continue
        exp = val
        if DERIVED_SPECS[field].get("display_percent") and isinstance(val, (int, float)):
            exp = round(val * 100, 1)
        assert dv[field]["value"] == (round(exp, 2) if isinstance(exp, float) else exp), field
    assert set(_DERIVED_ORDER) == set(DERIVED_SPECS.keys())


def test_fm9_gross_margin_ratio_contract():
    """FM9（S3）：gross_margin 与 variable_cost_ratio 统一为 0~1，双向闭合。

    旧契约自相矛盾：INPUT_SPECS 声明 "%" 却在公式里 ÷100，
    derive({"gross_margin": 0.6}) 算出 vcr=0.994（应为 0.4）。
    """
    assert abs(derive({"gross_margin": 0.4})[0]["variable_cost_ratio"] - 0.6) < 1e-9
    assert abs(derive({"gross_margin": 0.6})[0]["variable_cost_ratio"] - 0.4) < 1e-9
    assert abs(derive({"variable_cost_ratio": 0.4})[0]["gross_margin"] - 0.6) < 1e-9
    # 口径契约：用 unit_key（"ratio" = 0~1）断言，而非展示文案。
    # 文案已外置（i18n），unit 会随 locale 变化；unit_key 才是语言无关的口径标识。
    assert DERIVED_SPECS["gross_margin"]["unit_key"] == "ratio"
    assert INPUT_SPECS["gross_margin"]["unit_key"] == "ratio"
    # 中文下展示仍为 0~1（保证外置改造没有偷换口径）
    assert field_unit("gross_margin") == "0~1"


def test_fm10_percent_fields_display_as_percent():
    """FM10（S3）：内部 0~1 的比例字段，展示层 ×100 成百分数（视觉与旧版一致）。"""
    dv = {x["field"]: x for x in derived_values(_FULL, {})}
    gm = dv["gross_margin"]
    assert gm["value"] == 45.0 and gm["unit"] == "%", gm
    vcr = dv["variable_cost_ratio"]
    assert vcr["value"] == 55.0 and vcr["unit"] == "%", vcr


def test_fm11_vc_derivation_defined_only_in_model():
    """FM11（S4）：变动成本率的推导公式只存在于 field_model，引擎不手写第二份。

    旧实现在 workflow_engine 里手写了一条并行的四路推导
    （user > unit_var÷price > 1−gm > None），与 DERIVED_SPECS 的公式重复，
    改一处必须记得改另一处 —— 结构断言防复发。
    """
    import inspect
    import tools.workflow_engine as we
    code = inspect.getsource(we)
    assert "gm_ratio" not in code, "单位猜测变量 gm_ratio 仍在引擎里"
    assert "1 - gm_ratio" not in code
    assert 'float(user_unit_var) / p["price_per_unit"]' not in code, "手写的 unit_var÷price 分支仍在"
    assert "1 - float(user_gm)" not in code, "手写的 1−毛利率 分支仍在"


def test_fm7_fixed_cost_real_formula():
    """FM7：monthly_fixed_cost 真公式——只给租金也能算出。"""
    d, _ = derive({"monthly_rent": 8000})
    assert d["monthly_fixed_cost"] == 8000
    d2, _ = derive({"monthly_rent": 10000, "employee_count": 2, "avg_salary": 5000, "labor_burden": 0.0})
    assert d2["monthly_fixed_cost"] == 20000  # 10000+10000


def test_fm8_vc_multi_path():
    """FM8：variable_cost_ratio 多路推导。"""
    # unit_var ÷ price
    d1, m1 = derive({"unit_variable_cost": 12, "price_per_unit": 15})
    assert abs(d1["variable_cost_ratio"] - 0.8) < 1e-9, d1
    assert m1["variable_cost_ratio"]["source"] == "derived"
    # 1 - gross_margin（S3：gm 输入口径与变量同为 0~1）
    d2, m2 = derive({"gross_margin": 0.4})
    assert abs(d2["variable_cost_ratio"] - 0.6) < 1e-9, d2
    # 全缺 → None
    d3, m3 = derive({})
    assert d3["variable_cost_ratio"] is None
    assert m3["variable_cost_ratio"]["source"] == "missing"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
            print(f"ERROR {t.__name__}: {e}")
            failed += 1
    print(f"\n=== {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
